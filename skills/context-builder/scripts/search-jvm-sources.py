#!/usr/bin/env python3
"""search-jvm-sources.py — rank Java/Kotlin source files to read for a keyword.

Usage: <skill-dir>/scripts/search-jvm-sources.py <keyword>... [root] [-n 200] [--json]

Searches only real source roots (src/main/java, src/main/kotlin, src/...), never
build/, out/, target/ or generated output. Files that mention the keyword become
seeds — including fuzzily, so a misspelled or abbreviated name still lands — and
the ranking then fans out along type references and imports, so the list also
contains the callers and collaborators you need to make sense of the seeds.
Past the first hop the fan-out is gated on the keyword, so it stays a search
instead of drifting into the dependency closure. Prints at most 200 files.

Several keywords are allowed: by default they are unioned, with --all only files
carrying every one of them seed the search. --from-file seeds from a path instead
of (or as well as) a keyword, for when you already know where to start.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from collections import defaultdict
from difflib import SequenceMatcher
from functools import lru_cache

if hasattr(signal, "SIGPIPE"):  # so `| head` exits quietly instead of trapping
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

HARD_LIMIT = 200  # never print more than this many files

# Directories that hold generated or vendored code, excluded from every search.
EXCLUDE_GLOBS = [
    "!**/build/**",
    "!**/out/**",
    "!**/target/**",
    "!**/bin/**",
    "!**/.gradle/**",
    "!**/.idea/**",
    "!**/generated/**",
    "!**/node_modules/**",
    "!**/.git/**",
]
EXCLUDE_DIRS = {
    "build",
    "out",
    "target",
    "bin",
    ".gradle",
    ".idea",
    "generated",
    "node_modules",
    ".git",
}
SOURCE_EXTS = (".java", ".kt", ".kts")

# Wiring lives outside the source sets: Gradle files, Spring/Dagger XML, string
# resources, the manifest. They are not ranked or read, but a keyword landing in
# one is often the fastest orientation there is, so they get a trailer section.
CONFIG_GLOBS = [
    "*.gradle",
    "*.gradle.kts",
    "*.xml",
    "*.properties",
    "*.yaml",
    "*.yml",
    "*.toml",
    "*.json",
    "*.proto",
    "*.sql",
    "*.conf",
    "*.cfg",
]
CONFIG_EXCLUDE = EXCLUDE_GLOBS + ["!**/*.lock", "!**/*-lock.json", "!**/gradle/wrapper/**"]
CONFIG_LIMIT = 6

# Source sets to keep when a src/ dir uses the Gradle/Maven layout, in the order
# they are checked. Anything else under src/ (res, resources, proto) is skipped.
SOURCE_SET_DIRS = [
    f"{sourceset}/{lang}"
    for sourceset in (
        "main",
        "test",
        "androidTest",
        "commonMain",
        "commonTest",
        "androidMain",
        "iosMain",
        "jvmMain",
        "debug",
        "release",
    )
    for lang in ("java", "kotlin")
]

# Fallback declaration regex, used only when ast-grep is missing. The uppercase
# first letter is what keeps prose out ("the record that ..." in a comment).
TYPE_DECL = r"\b(?:class|interface|object|enum|record|@interface)\s+([A-Z]\w*)"
IMPORT_DECL = r"^\s*import\s+(?:static\s+)?[\w.]*?(\w+)\s*(?:;|$)"

# Type declarations by node kind, so comments and strings can't contribute names.
AST_RULES = [
    """
id: jvm-type-names
language: java
rule:
  kind: identifier
  inside:
    any:
      - {kind: class_declaration}
      - {kind: interface_declaration}
      - {kind: enum_declaration}
      - {kind: record_declaration}
      - {kind: annotation_type_declaration}
""",
    """
id: jvm-type-names
language: kotlin
rule:
  kind: type_identifier
  inside: {kind: class_declaration}
""",
]
# Kotlin `object` singletons parse differently from classes and the kind rule
# above misses them; this pattern picks them up, name in metaVariables.
AST_OBJECT_PATTERN = "object $N { $$$ }"

# Score weights. Direct evidence is worth far more than fan-out, so a file the
# keyword actually names always outranks something merely adjacent to it.
W_STEM_EXACT = 60.0  # file is named exactly after the keyword
W_STEM_PART = 30.0  # keyword appears inside the file name
W_PATH = 12.0  # keyword appears in a package/directory segment
W_TYPE_DECL = 25.0  # declares a type whose name contains the keyword
W_MEMBER_DECL = 8.0  # declares a fun/val/method whose name contains the keyword
W_WORD_HIT = 3.0  # standalone-word mention
W_SUB_HIT = 1.0  # mention inside a longer identifier
W_FUZZY_MENTION = 6.0  # mention of a fuzzily-matched name, before the discount
W_COCHANGE = 4.0  # per commit that touched this file alongside a seed
FUZZY_DISCOUNT = 0.8  # fuzzy evidence never outweighs the same real evidence
MAX_FUZZY_TERMS = 8  # one loose keyword must not drag in half the vocabulary
HITS_CAP = 8  # ignore mention counts past this, one busy file isn't the answer
COCHANGE_CAP = 3.0  # and the same for co-change: three tight commits make the point
COCHANGE_TIGHTNESS = 6.0  # a commit of this many files counts once; wider counts less
MIN_COCHANGE = 1.0  # one tight commit, or several loose ones — below that it is noise
FANOUT = [0.35, 0.12, 0.05, 0.02, 0.01]  # share of a file's score passed on per hop
TEST_PENALTY = 0.3

# Not every mention of a type is the same kind of evidence. Subclassing it is a
# structural commitment, constructing or calling it is real use, and everything
# else may be a log string or a javadoc link — so the fan-out share is scaled by
# which one it is. "Who implements this interface" is the question this answers.
EDGE_WEIGHT = {
    "extends": 1.6,
    "implements": 1.6,
    "subtypes": 1.6,
    "call": 1.15,
    "mention": 1.0,
}
EDGE_VERB = {
    "extends": "extends",
    "implements": "implements",
    "subtypes": "subtypes",
    "call": "calls",
    "mention": "uses",
}

# Output tiers, indexed by how many hops from a keyword match a file was found.
# The point is to tell a reader where to start and where it is safe to stop.
TIERS = [
    ("READ FIRST", "the keyword is named or declared here"),
    ("THEN", "direct collaborators of the files above"),
    ("SKIM IF NEEDED", "further out, reached through an on-topic type"),
]

TEST_PATH = re.compile(r"(^|/)(test|tests|androidTest|integrationTest|testFixtures)(/|$)")
# `Spec` is deliberately missing here: it only counts under a test path, because
# OpenApiSpec.kt is production code and demoting it (or dropping it entirely
# under --no-tests) loses a file that matters.
TEST_STEM = re.compile(r"(Test|Tests|IT)$")
TEST_SUFFIXES = ("Test", "Tests", "Spec", "IT")

EVIDENCE_WIDTH = 96  # a source line is evidence, not a paragraph
BORING_LINE = re.compile(r"^\s*(?:import|package)\b")  # true, and tells you nothing
CACHE_VERSION = 2


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def rg(args, roots):
    """Run ripgrep over roots, returning stdout ('' when nothing matched)."""
    # rg's built-in `java` type also covers .properties/.jsp, so define our own.
    # One --type-add per glob: the comma form is accepted but matches nothing.
    cmd = ["rg", "--no-messages"]
    for ext in SOURCE_EXTS:
        cmd += ["--type-add", f"jvm:*{ext}"]
    cmd += ["-t", "jvm"]
    for glob in EXCLUDE_GLOBS:
        cmd += ["-g", glob]
    cmd += args + [str(r) for r in roots]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1):  # 1 == no matches, which is not an error
        die(f"rg failed: {proc.stderr.strip()}")
    return proc.stdout


def keyword_parts(keyword):
    """The keyword's own words: "mcp server" -> ["mcp", "server"]."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", keyword) if p]
    if len(parts) == 1:
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", parts[0]) or parts
    return parts


def similarity(keyword, term):
    """How close an identifier is to the keyword, 0..1.

    Two readings, best one wins: the whole strings compared with separators
    stripped (catches typos — "Servor Config" vs ServerConfig), and a word-by-word
    alignment (catches word order and abbreviation — "workspace svc" vs
    WorkspaceService). A term carrying extra words is nudged down so that a short
    keyword prefers the shorter name.
    """
    kw_tokens = [t.lower() for t in keyword_parts(keyword)]
    term_tokens = [t.lower() for t in keyword_parts(term)]
    if not kw_tokens or not term_tokens:
        return 0.0
    whole = SequenceMatcher(None, "".join(kw_tokens), "".join(term_tokens)).ratio()
    per_token = [max(SequenceMatcher(None, k, t).ratio() for t in term_tokens) for k in kw_tokens]
    aligned = sum(per_token) / len(per_token)
    aligned *= 1 - min(0.2, 0.05 * max(0, len(term_tokens) - len(kw_tokens)))
    return max(whole, aligned)


def keyword_pattern(keyword):
    """Turn a keyword into a regex tolerant of camelCase/snake_case spellings.

    "user profile", "userProfile" and "user_profile" all become the same pattern,
    so one invocation covers however the codebase happens to spell it.
    """
    return r"[_\-]?".join(re.escape(p) for p in keyword_parts(keyword))


def find_source_roots(root):
    """Locate src trees under root; returns (roots, note_for_the_user)."""
    if shutil.which("fd"):
        proc = subprocess.run(
            ["fd", "-t", "d", "--full-path", r"/src$", "-H"]
            + [x for g in EXCLUDE_GLOBS for x in ("-E", g.lstrip("!"))]
            + [str(root)],
            capture_output=True,
            text=True,
        )
        found = [line for line in proc.stdout.splitlines() if line]
        note = None
    else:
        note = "`fd` is unavailable; using os.walk instead."
        found = []
        for dirpath, dirnames, _ in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
            if os.path.basename(dirpath) == "src":
                found.append(dirpath)

    roots = []
    for src in sorted(found):
        # Prefer the language dirs when the Gradle/Maven layout is present, so
        # res/ and resources/ never enter the candidate set. Test trees stay in —
        # they are demoted at ranking time, not hidden.
        narrowed = [
            os.path.join(src, sub)
            for sub in SOURCE_SET_DIRS
            if os.path.isdir(os.path.join(src, sub))
        ]
        roots.extend(narrowed or [src.rstrip("/")])

    # Drop roots nested inside another root to avoid double-counting files.
    roots = sorted(set(roots))
    roots = [r for r in roots if not any(r != o and r.startswith(o + "/") for o in roots)]
    if not roots:
        # Pointing straight at src/main (or deeper) is normal, not a miss.
        inside_src = "src" in str(root).split("/")
        return [str(root)], (
            None if inside_src else "no src/ directory found; searching the whole root instead"
        )
    return roots, note


def list_sources(roots):
    if shutil.which("fd"):
        proc = subprocess.run(
            ["fd", ".", "-t", "f", "-e", "java", "-e", "kt", "-e", "kts"]
            + [x for g in EXCLUDE_GLOBS for x in ("-E", g.lstrip("!"))]
            + roots,
            capture_output=True,
            text=True,
        )
        return [line for line in proc.stdout.splitlines() if line]
    files = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
            files += [
                os.path.join(dirpath, f) for f in filenames if f.endswith(SOURCE_EXTS)
            ]
    return files


def counts(pattern, roots, word=False):
    """Per-file match counts for a pattern."""
    args = ["-i", "-c", "-e", pattern]
    if word:
        args.insert(0, "-w")
    out = {}
    for line in rg(args, roots).splitlines():
        path, _, n = line.rpartition(":")
        if path:
            out[path] = int(n)
    return out


def trim(text):
    """One source line, squeezed to fit on a terminal row as evidence."""
    text = re.sub(r"\s+", " ", text.strip())
    return text if len(text) <= EVIDENCE_WIDTH else text[: EVIDENCE_WIDTH - 1] + "…"


def first_hit(pattern, roots, limit=4):
    """Each file's best early match as (line number, line text).

    The text is what turns a reading list into something you can triage without
    opening anything: "declares a matching type" tells you a file qualified,
    `public final class ServerConfig implements Config` tells you whether to read
    it. The first match is usually the import that pulled the name in, which
    shows nothing, so a few lines are read and the first line that is not import
    or package boilerplate wins.
    """
    hits = {}
    for line in rg(["-i", "-n", "-m", str(limit), "--no-heading", "-e", pattern], roots).splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        path, number, text = parts[0], int(parts[1]), parts[2]
        previous = hits.get(path)
        if previous is None or (BORING_LINE.match(previous[1]) and not BORING_LINE.match(text.strip())):
            hits[path] = (number, trim(text))
    return hits


def ast_grep(args):
    proc = subprocess.run(["ast-grep"] + args, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        die(f"ast-grep failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else []


def cache_path(roots):
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    key = hashlib.sha1("\0".join(sorted(roots)).encode()).hexdigest()[:16]
    return os.path.join(base, "context-builder", f"jvm-{key}.json")


def cache_stamp(files):
    """Cheap fingerprint of the tree: file count plus the newest mtime."""
    newest = 0
    for path in files:
        try:
            newest = max(newest, os.stat(path).st_mtime_ns)
        except OSError:
            pass
    return {"version": CACHE_VERSION, "files": len(files), "mtime": newest}


def read_cache(roots, files):
    path = cache_path(roots)
    try:
        with open(path) as handle:
            blob = json.load(handle)
    except (OSError, ValueError):
        return None
    return blob.get("pairs") if blob.get("stamp") == cache_stamp(files) else None


def write_cache(roots, files, pairs):
    path = cache_path(roots)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "w") as handle:
            json.dump({"stamp": cache_stamp(files), "pairs": pairs}, handle)
        os.replace(tmp, path)
    except OSError:
        pass  # a cache that cannot be written is not an error worth reporting


def declared_types(roots, files, use_cache=True):
    """Map file -> type names it declares, and name -> files declaring it.

    Uses ast-grep so that only real declarations count; the regex fallback also
    matches prose in comments ("the record that ..."), which then fans out to
    every file mentioning the word "that". The result is cached per source root
    against the tree's newest mtime, because a research session runs this script
    several times with different keywords and this pass is the same every time.
    """
    candidates = set(files)
    note = None
    pairs = read_cache(roots, files) if use_cache else None
    if pairs is None:
        pairs = []
        if shutil.which("ast-grep"):
            for rule in AST_RULES:
                pairs += [
                    [m["file"], m["text"]]
                    for m in ast_grep(["scan", "--inline-rules", rule, "--json=compact"] + roots)
                ]
            for m in ast_grep(
                ["run", "-p", AST_OBJECT_PATTERN, "-l", "kotlin", "--json=compact"] + roots
            ):
                name = m["metaVariables"]["single"].get("N", {}).get("text")
                if name:
                    pairs.append([m["file"], name])
        else:
            note = "`ast-grep` is unavailable; using an rg declaration regex instead."
            out = rg(["-o", "--null", "--no-line-number", "-r", "$1", "-e", TYPE_DECL], roots)
            for line in out.splitlines():
                path, sep, name = line.partition("\0")
                if sep and name:
                    pairs.append([path, name])
        if use_cache:
            write_cache(roots, files, pairs)

    by_file, by_name = defaultdict(set), defaultdict(set)
    for path, name in pairs:
        if path in candidates:  # ast-grep does not honour our exclude globs
            by_file[path].add(name)
            by_name[name].add(path)
    return by_file, by_name, note


@lru_cache(maxsize=512)
def edge_patterns(name):
    esc = re.escape(name)
    return (
        # `extends Foo`, `implements A, Foo` (Java) and `class Bar : Foo()` (Kotlin).
        re.compile(rf"\b(?:extends|implements)\b[^={{;]*\b{esc}\b"),
        re.compile(rf"\b(?:class|object|interface)\s+\w+[^=]*:[^=]*\b{esc}\b"),
        # `new Foo(`, `Foo(`, `Foo<T>(` — construction or invocation.
        re.compile(rf"\bnew\s+{esc}\b|\b{esc}\s*(?:<[^>]*>)?\s*\("),
    )


def edge_kind(name, line):
    """Classify one reference to `name` from the source line carrying it."""
    inherits, kotlin_super, call = edge_patterns(name)
    if inherits.search(line):
        return "implements" if "implements" in line else "extends"
    if kotlin_super.search(line):
        return "subtypes"
    return "call" if call.search(line) else "mention"


def references(names, roots):
    """Map type name -> {file: edge kind}, in a single ripgrep pass."""
    hits = defaultdict(dict)
    if not names:
        return hits
    pattern = r"\b(?:" + "|".join(sorted(re.escape(n) for n in names)) + r")\b"
    out = rg(["-w", "--json", "-e", pattern], roots)
    path = None
    for line in out.splitlines():
        event = json.loads(line)
        if event["type"] == "begin":
            path = event["data"]["path"].get("text")
        elif event["type"] == "match" and path:
            text = event["data"]["lines"].get("text", "")
            for sub in event["data"]["submatches"]:
                name = sub["match"]["text"]
                kind = edge_kind(name, text)
                known = hits[name].get(path)
                # Keep the strongest relationship seen anywhere in the file.
                if known is None or EDGE_WEIGHT[kind] > EDGE_WEIGHT[known]:
                    hits[name][path] = kind
    return hits


def imported_names(files):
    """Map file -> type names it imports, i.e. its declared collaborators."""
    imports = defaultdict(set)
    if not files:
        return imports
    out = rg(["-o", "--null", "--no-line-number", "-r", "$1", "-e", IMPORT_DECL], files)
    for line in out.splitlines():
        path, sep, name = line.partition("\0")
        if sep and name:
            imports[path].add(name)
    return imports


def config_mentions(patterns, root):
    """Keyword hits in build files, manifests and resources — wiring, not code."""
    cmd = ["rg", "--no-messages", "-i", "-n", "-m", "1", "--no-heading"]
    cmd += ["-e", "|".join(f"(?:{p})" for p in patterns)]
    for glob in CONFIG_GLOBS:
        cmd += ["-g", glob]
    for glob in CONFIG_EXCLUDE:
        cmd += ["-g", glob]
    proc = subprocess.run(cmd + [root], capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        return []
    out = []
    for line in proc.stdout.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[1].isdigit():
            out.append({"file": os.path.relpath(parts[0], root), "line": int(parts[1])})
        if len(out) >= CONFIG_LIMIT:
            break
    return out


def git_cochange(seeds, root, candidates, commits=300, max_files=25):
    """Files that git history keeps changing together with the seeds.

    Static edges miss the migration, the feature flag and the fixture that always
    move with a class. Commits touching more than `max_files` files are ignored —
    a repo-wide reformat is not evidence of anything — and what is left counts for
    less the wider it is, so a two-file commit says much more than a twenty-file
    one. Returns (weight, times, partner) per file, weight being what scores and
    times what the reader is shown.
    """
    empty = ({}, {}, {})
    if not seeds or not shutil.which("git"):
        return empty
    top = subprocess.run(
        ["git", "-C", root, "rev-parse", "--show-toplevel"], capture_output=True, text=True
    )
    if top.returncode != 0:
        return empty
    base = top.stdout.strip()
    # Two calls, because `git log --name-only -- <paths>` prints only the paths
    # that matched the pathspec: the commits are selected first, then listed in
    # full so the files that travelled with the seeds are visible.
    log = subprocess.run(
        ["git", "-C", base, "log", "--no-merges", "-n", str(commits), "--format=%H", "--"]
        + list(seeds),
        capture_output=True,
        text=True,
    )
    if log.returncode != 0 or not log.stdout.strip():
        return empty
    show = subprocess.run(
        ["git", "-C", base, "log", "--no-walk", "--stdin", "--format=%x00%H", "--name-only"],
        input=log.stdout,
        capture_output=True,
        text=True,
    )
    if show.returncode != 0:
        return empty

    seed_set = set(seeds)
    weight, times, partner = defaultdict(float), defaultdict(int), {}
    for block in show.stdout.split("\0"):
        lines = [ln for ln in block.splitlines()[1:] if ln.strip()]
        if not lines or len(lines) > max_files:
            continue
        touched = {os.path.join(base, ln) for ln in lines}
        seeds_here = touched & seed_set
        if not seeds_here:
            continue
        label = os.path.basename(sorted(seeds_here)[0])
        share = min(1.0, COCHANGE_TIGHTNESS / len(lines))
        for path in touched - seed_set:
            if path in candidates:
                weight[path] += share
                times[path] += 1
                partner.setdefault(path, label)
    weight = {p: w for p, w in weight.items() if w >= MIN_COCHANGE}
    return weight, {p: times[p] for p in weight}, {p: partner[p] for p in weight}


def stem_of(path):
    return os.path.splitext(os.path.basename(path))[0]


def is_test(path):
    return bool(TEST_PATH.search(path)) or bool(TEST_STEM.search(stem_of(path)))


def test_partners(files):
    """Map a source file -> the test files written against it.

    A class's test is the best documentation it has, so it belongs next to its
    subject in the list rather than several tiers below it on its own.
    """
    by_stem = defaultdict(list)
    for path in files:
        by_stem[stem_of(path)].append(path)
    partners = defaultdict(list)
    for path in files:
        stem = stem_of(path)
        if not is_test(path):
            continue
        for suffix in TEST_SUFFIXES:
            if stem.endswith(suffix) and len(stem) > len(suffix):
                for subject in by_stem.get(stem[: -len(suffix)], ()):
                    if subject != path and not is_test(subject):
                        partners[subject].append(path)
                break
    return partners


def count_lines(path):
    """File length, so the caller can budget how much reading it is signing up for."""
    try:
        with open(path, "rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def score_keyword(keyword, files, roots, root, types_by_file, files_by_type, min_fuzzy):
    """Everything one keyword contributes on its own: score, evidence, vocabulary.

    Returned separately per keyword so that several keywords can be combined
    either as a union or as an intersection (--all) without the passes below
    having to know which mode is in play.
    """
    pattern = keyword_pattern(keyword)
    type_pattern = rf"(?:class|interface|object|enum|record)\s+\w*(?:{pattern})\w*"
    member_pattern = rf"(?:fun|val|var|void|public|private|protected)\s+\w*(?:{pattern})\w*"
    sub_hits = counts(pattern, roots)
    word_hits = counts(pattern, roots, word=True)
    type_hits = counts(type_pattern, roots)
    member_hits = counts(member_pattern, roots)

    # Anchor each result at the most useful line: the matching declaration if
    # there is one, else the first mention, else whatever type the file declares
    # (fan-out results have no mention of their own).
    hits = first_hit(TYPE_DECL, roots)
    hits.update(first_hit(pattern, roots))
    hits.update(first_hit(type_pattern, roots))

    exact = re.compile(rf"^{pattern}$", re.I)
    partial = re.compile(pattern, re.I)

    score = defaultdict(float)
    why = defaultdict(list)
    mentions = {}
    for path in files:
        stem = stem_of(path)
        if exact.match(stem):
            score[path] += W_STEM_EXACT
            why[path].append("file name is the keyword")
        elif partial.search(stem):
            score[path] += W_STEM_PART
            why[path].append("keyword in file name")
        # Segments below the search root only — the root's own directory name is
        # not evidence about any file inside it.
        segments = os.path.dirname(os.path.relpath(path, root)).split("/")
        if any(partial.search(seg) for seg in segments):
            score[path] += W_PATH
            why[path].append("keyword in package path")
        if type_hits.get(path):
            score[path] += W_TYPE_DECL
            why[path].append("declares a matching type")
        if member_hits.get(path):
            score[path] += W_MEMBER_DECL * min(member_hits[path], 3)
            why[path].append("declares matching members")
        n_word, n_sub = word_hits.get(path, 0), sub_hits.get(path, 0)
        if n_word:
            score[path] += W_WORD_HIT * min(n_word, HITS_CAP)
        if n_sub - n_word > 0:
            score[path] += W_SUB_HIT * min(n_sub - n_word, HITS_CAP)
        if n_sub:
            mentions[path] = n_sub
            # The count itself is printed as a marker on the row; this is here so
            # that a file whose only evidence is mentions still says why it is on
            # the list — and so that under --all every keyword shows something.
            if not why[path]:
                why[path].append("mentions the keyword")

    # Fuzzy pass. Matching runs against the repo's own vocabulary — file names and
    # declared type names — rather than against raw text, because that is where a
    # misspelling or an abbreviation is recoverable: "srvconfig" is 0.86 similar to
    # ServerConfig and under 0.5 to everything else. Fuzzy evidence is discounted
    # by similarity so it always sits below a real match.
    vocabulary = {stem_of(p) for p in files} | set(files_by_type)
    ranked_vocab = sorted(
        ((similarity(keyword, term), term) for term in vocabulary if not partial.search(term)),
        reverse=True,
    )
    fuzzy = dict((t, s) for s, t in ranked_vocab if s >= min_fuzzy) if min_fuzzy > 0 else {}
    fuzzy = dict(sorted(fuzzy.items(), key=lambda kv: -kv[1])[:MAX_FUZZY_TERMS])

    exact_hit = {p for p, s in score.items() if s > 0}
    carrier = set()  # files that are named after / declare a fuzzy match
    if fuzzy:
        fuzzy_mentions = references(set(fuzzy), roots)  # one pass for every term
        for term, sim in fuzzy.items():
            weight = sim * FUZZY_DISCOUNT
            for path in fuzzy_mentions.get(term, ()):
                gain = W_FUZZY_MENTION
                if stem_of(path) == term:
                    gain += W_STEM_PART
                    carrier.add(path)
                if term in types_by_file.get(path, ()):
                    gain += W_TYPE_DECL
                    carrier.add(path)
                score[path] += gain * weight
                if len(why[path]) < 3:
                    why[path].append(f"≈{term} ({sim:.2f})")

    words = [p for p in keyword_parts(keyword) if len(p) >= 3]
    return {
        "keyword": keyword,
        "score": score,
        "why": why,
        "mentions": mentions,
        "hits": hits,
        "exact_hit": exact_hit,
        "carrier": carrier,
        "ranked_vocab": ranked_vocab,
        # A fuzzily-matched name is on topic for the deep-hop gate too, or the
        # collaborators of the file we just recovered would be unreachable.
        "topical": [re.escape(w) for w in words] + [re.escape(t) for t in fuzzy],
        "fallback_pattern": pattern,
    }


def main():
    ap = argparse.ArgumentParser(
        description="Rank Java/Kotlin source files to read for a keyword.",
        epilog="Example: search-jvm-sources.py AuthToken ~/work/api",
    )
    ap.add_argument("keyword", nargs="*", help="what you are looking for (camelCase or spaced)")
    ap.add_argument("--root", default=None, help="search root (default: . or the last argument)")
    ap.add_argument("-n", type=int, default=HARD_LIMIT, help=f"files to print (max {HARD_LIMIT})")
    ap.add_argument("--seeds", type=int, default=12, help="direct hits used to fan out (default: 12)")
    ap.add_argument(
        "--depth",
        type=int,
        choices=range(len(FANOUT) + 1),
        default=len(FANOUT),
        metavar="{0-%d}" % len(FANOUT),
        help=f"fan-out hops (default: {len(FANOUT)})",
    )
    ap.add_argument(
        "--fuzzy",
        type=float,
        default=0.80,
        metavar="MIN",
        help="similarity 0-1 for matching misspelled/abbreviated names (default: 0.8, 0 disables)",
    )
    ap.add_argument(
        "--all",
        action="store_true",
        help="with several keywords, keep only files carrying every one of them",
    )
    ap.add_argument(
        "--from-file",
        metavar="PATH",
        help="also seed from this file, for when you already know where to start",
    )
    ap.add_argument("--no-tests", action="store_true", help="drop test sources entirely")
    ap.add_argument("--no-git", action="store_true", help="skip the git co-change pass")
    ap.add_argument("--no-cache", action="store_true", help="ignore the declaration cache")
    ap.add_argument("--no-evidence", action="store_true", help="omit the matched source lines")
    ap.add_argument("--json", action="store_true", help="emit JSON for jq")
    args = ap.parse_args()

    # `<keyword>... [root]`: the trailing argument is the root when it is a
    # directory that exists, which keeps the old positional form working.
    keywords = list(args.keyword)
    root_arg = args.root
    if root_arg is None and len(keywords) > 1 and os.path.isdir(os.path.expanduser(keywords[-1])):
        root_arg = keywords.pop()
    if root_arg is None and len(keywords) == 1 and not args.from_file:
        root_arg = "."
    elif root_arg is None and len(keywords) == 1 and os.path.isdir(os.path.expanduser(keywords[0])):
        root_arg, keywords = keywords[0], []
    root_arg = root_arg or "."

    if not shutil.which("rg"):
        die("`rg` is unavailable and this script has no fallback for it; install ripgrep.")
    root = os.path.abspath(os.path.expanduser(root_arg))
    if not os.path.isdir(root):
        die(f"{root_arg} is not a directory")

    from_file = None
    if args.from_file:
        from_file = os.path.abspath(os.path.expanduser(args.from_file))
        if not os.path.isfile(from_file):
            die(f"--from-file {args.from_file} is not a file")
    if not keywords and not from_file:
        die("give a keyword, or --from-file PATH to start from a file you already know")

    roots, note = find_source_roots(root)
    files = list_sources(roots)
    if not files:
        die(f"no .java/.kt/.kts files under {', '.join(roots)}")
    candidates = set(files)
    if from_file and from_file not in candidates:
        files.append(from_file)  # seeding from outside the source sets is allowed
        candidates.add(from_file)

    rel_roots = ", ".join(os.path.relpath(r, root) for r in roots)
    types_by_file, files_by_type, ast_note = declared_types(
        roots, files, use_cache=not args.no_cache
    )
    if ast_note:
        print(f"note: {ast_note}", file=sys.stderr)

    passes = [
        score_keyword(kw, files, roots, root, types_by_file, files_by_type, args.fuzzy)
        for kw in keywords
    ]

    # Combine the keywords. The union just adds up; --all keeps only files that
    # every keyword found and scores them by the weakest one, so "the file where
    # auth and retry meet" beats "the file that says auth forty times".
    score = defaultdict(float)
    why = defaultdict(list)
    mentions = defaultdict(int)
    # With no keyword at all (--from-file on its own) nothing has anchored the
    # results yet, so fall back to each file's own type declaration.
    hits = {} if passes else first_hit(TYPE_DECL, roots)
    exact_hit, carrier = set(), set()
    for one in passes:
        hits.update(one["hits"])
        exact_hit |= one["exact_hit"]
        carrier |= one["carrier"]

    if args.all and len(passes) > 1:
        common = set.intersection(*({p for p, s in one["score"].items() if s > 0} for one in passes))
        for path in common:
            # Weakest keyword first, the rest heavily discounted: what makes a
            # file interesting here is carrying all of them, not saturating one.
            values = sorted(one["score"][path] for one in passes)
            score[path] = values[0] + 0.25 * sum(values[1:])
        exact_hit &= common
        carrier &= common
    else:
        common = None
        for one in passes:
            for path, value in one["score"].items():
                score[path] += value

    # Round-robin across the keywords rather than draining the first one, or a
    # file matching two keywords shows three reasons that are all about the first
    # — which is exactly the thing --all was asked to demonstrate.
    for rank in range(3):
        for one in passes:
            tag = f"{one['keyword']}: " if len(passes) > 1 else ""
            for path, reasons in one["why"].items():
                if common is not None and path not in common:
                    continue
                if rank < len(reasons) and len(why[path]) < 3:
                    reason = tag + reasons[rank]
                    if reason not in why[path]:
                        why[path].append(reason)
    for one in passes:
        for path, count in one["mentions"].items():
            if common is None or path in common:
                mentions[path] += count

    topical_words = {w for one in passes for w in one["topical"]}
    if from_file:
        # A named starting point is a seed by definition, and its own vocabulary
        # is what keeps the deep hops on topic when no keyword was given.
        score[from_file] += W_STEM_EXACT
        why[from_file].insert(0, "starting point")
        exact_hit.add(from_file)
        own = [stem_of(from_file)] + sorted(types_by_file.get(from_file, ()))
        topical_words |= {re.escape(w) for w in own if len(w) >= 3}
    if not topical_words:  # every word was under three letters to gate on
        topical_words = {one["fallback_pattern"] for one in passes} or {r"(?!)"}
    topical = re.compile("|".join(sorted(topical_words)), re.I)

    direct = {p: s for p, s in score.items() if s > 0}
    if not direct:
        label = " + ".join(f"'{k}'" for k in keywords)
        joiner = "all of " if args.all and len(keywords) > 1 else ""
        print(f"nothing matches {joiner}{label} under {rel_roots}", file=sys.stderr)
        if args.all and len(keywords) > 1:
            # Which keyword failed is the whole answer here: "auth: 40 files,
            # retry: 0" says the intersection was never going to exist.
            for one in passes:
                found = sum(1 for s in one["score"].values() if s > 0)
                print(f"  '{one['keyword']}': {found} files", file=sys.stderr)
            print("drop --all to see them separately", file=sys.stderr)
        elif passes:
            near = [f"{t} ({s:.2f})" for s, t in passes[0]["ranked_vocab"][:3] if s > 0.4]
            if near:
                print(f"closest names in this repo: {', '.join(near)}", file=sys.stderr)
                print(f"retry with one of those, or lower --fuzzy (now {args.fuzzy})", file=sys.stderr)
            else:
                print("try a shorter keyword, or the name the codebase uses instead", file=sys.stderr)
        sys.exit(1)

    # How far from a keyword match each file was found, which is what the tiers
    # in the output are: 0 = the keyword is in this file, 1 = a seed's direct
    # collaborator, 2+ = reached through an on-topic type further out.
    origin = {p: 0 for p in direct}
    # Merely mentioning a fuzzily-matched name is not "the keyword is here" — it is
    # the same relationship as using a seed's type, so it reads as a collaborator.
    for path in direct:
        if path not in exact_hit and path not in carrier:
            origin[path] = 1

    def note_reason(path, reason):
        if reason not in why[path] and len(why[path]) < 3:
            why[path].append(reason)

    frontier = sorted(direct, key=direct.get, reverse=True)[: args.seeds]
    seeds = list(frontier)
    seen = set(frontier)
    for hop in range(args.depth):
        if not frontier:
            break
        names = {n for f in frontier for n in types_by_file.get(f, ())}
        referrers = references(names, roots)
        imports = imported_names(frontier)

        weight = FANOUT[hop]
        # Hop 1 is unconditional: the direct collaborators of a seed are worth
        # reading whatever they are called. From hop 2 on, an ungated walk stops
        # being a search — it just enumerates the dependency closure — so a file
        # only qualifies if it mentions the keyword itself or is reached through
        # a type whose own name matches it.
        gated = hop >= 1
        shares = defaultdict(list)
        for src in frontier:
            share = score[src] * weight
            for name in types_by_file.get(src, ()):
                on_topic = not gated or bool(topical.search(name))
                for ref, kind in referrers.get(name, {}).items():
                    if ref != src and (on_topic or ref in direct):
                        shares[ref].append(share * EDGE_WEIGHT[kind])
                        note_reason(ref, f"{EDGE_VERB[kind]} {name}")
            for name in imports.get(src, ()):
                on_topic = not gated or bool(topical.search(name))
                for defn in files_by_type.get(name, ()):
                    if defn != src and (on_topic or defn in direct):
                        shares[defn].append(share)
                        note_reason(defn, f"defines {name}")

        # Being reached from several seeds counts for something, but with heavy
        # diminishing returns — otherwise a shared utility class accumulates its
        # way past the files that actually mention the keyword.
        gains = {p: max(v) + 0.25 * (sum(v) - max(v)) for p, v in shares.items()}
        for path, gained in gains.items():
            score[path] += gained
            origin.setdefault(path, hop + 1)
        frontier = [p for p in sorted(gains, key=gains.get, reverse=True) if p not in seen][: args.seeds]
        seen.update(frontier)

    cochange = {}
    if not args.no_git:
        weights, cochange, partner = git_cochange(seeds, root, candidates)
        for path, times in cochange.items():
            score[path] += W_COCHANGE * min(weights[path], COCHANGE_CAP)
            origin.setdefault(path, 1)
            plural = f" ({times}×)" if times > 1 else ""
            note_reason(path, f"changed with {partner[path]}{plural}")

    partners = test_partners(files)
    ranked = []
    for path, value in score.items():
        if value <= 0:
            continue
        test = is_test(path)
        if test:
            if args.no_tests:
                continue
            value *= TEST_PENALTY
        ranked.append((value, path, test))
    ranked.sort(key=lambda r: (-r[0], r[1]))
    # Drop the long tail of barely-connected files — a list that ends in noise
    # costs more to triage than it saves. The floor is per tier, not global:
    # each hop multiplies scores by a fraction, so a file three hops from a weak
    # seed can never clear 1% of a strong direct hit, and every deep result would
    # vanish no matter what --depth said.
    # The deepest tier is exempt: everything in it passed the keyword gate, so it
    # is on topic by construction, and its scores are fractions-of-fractions that
    # no percentage floor survives. It sorts last and fills the remaining budget.
    tier_best = defaultdict(float)
    for value, path, _ in ranked:
        tier = min(origin.get(path, 0), 2)
        tier_best[tier] = max(tier_best[tier], value)
    ranked = [
        r
        for r in ranked
        if min(origin.get(r[1], 0), 2) == 2 or r[0] >= tier_best[min(origin.get(r[1], 0), 2)] * 0.01
    ]
    # A test whose subject is already on the list is not a separate destination:
    # it is shown on the subject's own row instead, and its slot goes to a file
    # that is not already accounted for.
    listed = {r[1] for r in ranked}
    paired = {t for subject in listed for t in partners.get(subject, ()) if t in listed}
    ranked = [r for r in ranked if r[1] not in paired]
    ranked = ranked[: min(args.n, HARD_LIMIT)]
    # Which files make the cut is decided by score; the order they are listed in
    # is the order to read them, tier first. Numbering follows the reading order.
    ranked.sort(key=lambda r: (min(origin.get(r[1], 0), 2), -r[0], r[1]))

    rows = []
    for i, (value, path, test) in enumerate(ranked, 1):
        hit = hits.get(path)
        rows.append(
            {
                "rank": i,
                "score": round(value, 1),
                "file": os.path.relpath(path, root),
                "line": hit[0] if hit else None,
                "lines_total": count_lines(path),
                "hops": origin.get(path, 0),
                "tier": TIERS[min(origin.get(path, 0), 2)][0],
                "test": test,
                "mentions": mentions.get(path, 0),
                "why": why[path][:3],
                "evidence": hit[1] if hit else None,
                "tests": [os.path.relpath(t, root) for t in partners.get(path, ())],
                "cochange": cochange.get(path, 0),
            }
        )

    config = config_mentions([keyword_pattern(k) for k in keywords], root) if keywords else []
    config = [c for c in config if c["file"] not in {r["file"] for r in rows}]

    if args.json:
        print(
            json.dumps(
                {
                    "keywords": keywords,
                    "match_all": bool(args.all and len(keywords) > 1),
                    "from_file": os.path.relpath(from_file, root) if from_file else None,
                    "roots": roots,
                    "sources_searched": len(files),
                    "lines_total": sum(r["lines_total"] for r in rows),
                    "results": rows,
                    "config_mentions": config,
                },
                indent=2,
            )
        )
        return

    if note:
        print(f"note: {note}", file=sys.stderr)
    if args.n > HARD_LIMIT:
        print(f"note: -n capped at {HARD_LIMIT}", file=sys.stderr)
    if len(direct) > len(rows):
        print(
            f"note: {len(direct)} files match — this is the top {len(rows)}; "
            "a more specific keyword will read better",
            file=sys.stderr,
        )
    shown = [os.path.relpath(r, root) for r in roots]
    if len(shown) > 4:
        shown = shown[:4] + [f"(+{len(roots) - 4} more)"]
    noun = "file" if len(rows) == 1 else "files"
    total = sum(r["lines_total"] for r in rows)
    joiner = " + " if args.all else " / "
    label = joiner.join(f"'{k}'" for k in keywords)
    if from_file:
        start = os.path.relpath(from_file, root)
        label = f"{label} from {start}" if label else f"files around {start}"
    print(f"Reading list for {label} — {len(rows)} {noun}, ~{total:,} lines to read")
    print(f"searched {len(files)} sources under {', '.join(shown)}")
    print("Read top-down; stop as soon as the question is answered.\n")

    width = max(len(r["file"]) + len(str(r["line"] or "")) + 1 for r in rows)
    width = min(width, 64)
    for tier, (name, blurb) in enumerate(TIERS):
        group = [r for r in rows if min(r["hops"], 2) == tier]
        if not group:
            continue
        tier_lines = sum(r["lines_total"] for r in group)
        print(f"{name} ({len(group)} {'file' if len(group) == 1 else 'files'}, "
              f"~{tier_lines:,} lines) — {blurb}")
        for row in group:
            where = f"{row['file']}:{row['line']}" if row["line"] else row["file"]
            marks = []
            if row["mentions"] > 1:  # a single mention is implied by being here
                marks.append(f"{row['mentions']}×")
            if row["test"]:
                marks.append("test")
            tag = f" [{', '.join(marks)}]" if marks else ""
            print(f"  {row['rank']:>3}. {where:<{width}}  {', '.join(row['why'])}{tag}")
            # Evidence for the top tier only: further out the file has no line of
            # its own to show, and the reason string is the whole story.
            if tier == 0 and row["evidence"] and not args.no_evidence:
                print(f"       {row['evidence']}")
            for test in row["tests"]:
                print(f"       tested by {test}")
        print()

    if config:
        print(f"ALSO MENTIONED ({len(config)} config/resource "
              f"{'file' if len(config) == 1 else 'files'}, not sources)")
        for entry in config:
            print(f"       {entry['file']}:{entry['line']}")


if __name__ == "__main__":
    main()
