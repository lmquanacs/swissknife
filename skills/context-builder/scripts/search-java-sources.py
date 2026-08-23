#!/usr/bin/env python3
"""search-java-sources.py — rank Java source files to read for a keyword.

Usage: <skill-dir>/scripts/search-java-sources.py <keyword>... [root] [-n 200] [--json]

Requires tree-sitter:  pip install tree-sitter tree-sitter-java

Searches only real source roots (src/main/java, src/test/java, ...), never
build/, out/ or target/. Files that mention the keyword become seeds —
including fuzzily, so a misspelled or abbreviated name still lands — and the
ranking then fans out along type references and imports, so the list also
contains the callers and collaborators you need to make sense of the seeds.
Past the first hop the fan-out is gated on the keyword, so it stays a search
instead of drifting into the dependency closure. Prints at most 200 files.

Every structural fact — what a file declares, what it imports, and whether it
extends, implements, constructs or merely mentions a type — comes from a
tree-sitter parse, so comments and string literals can never contribute an edge
and a declaration split across lines is still seen. Raw mention *frequency*
stays on ripgrep, because how often a word occurs in a file is a text question
and rg answers it an order of magnitude faster than a parse would.

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

try:
    from tree_sitter import Language, Parser, Query
    import tree_sitter_java
except ImportError as exc:  # a hard dependency: there is no regex path any more
    sys.exit(
        f"error: {exc.name} is required by this script.\n"
        "  pip install tree-sitter tree-sitter-java\n"
        "Structural facts (declarations, imports, extends/implements edges) are\n"
        "read from a real parse; there is no regex fallback."
    )

try:  # tree-sitter >= 0.25 moved captures onto a cursor
    from tree_sitter import QueryCursor
except ImportError:  # 0.23/0.24 keep Query.captures, same dict-of-lists result
    QueryCursor = None

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
SOURCE_EXTS = (".java",)

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
# The kotlin/ dirs are kept too: a mixed module occasionally puts .java files
# under src/main/kotlin, and the extension filter in list_sources() is what
# actually decides which files survive.
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

# One query per file, capture names doubling as the edge kind. The grammar keeps
# `extends` and `implements` in separate nodes, so a declaration wrapped across
# lines — `class Foo\n    extends Base\n    implements Iface` — is classified
# exactly like a one-line one, and `extends_interfaces` catches the
# interface-extends-interface case a superclass rule would miss.
JAVA_QUERY = """
(class_declaration name: (identifier) @decl)
(interface_declaration name: (identifier) @decl)
(record_declaration name: (identifier) @decl)
(enum_declaration name: (identifier) @decl)
(annotation_type_declaration name: (identifier) @decl)

(method_declaration name: (identifier) @member)
(field_declaration (variable_declarator name: (identifier) @member))
(record_declaration (formal_parameters (formal_parameter name: (identifier) @member)))

(import_declaration (scoped_identifier) @import)

(superclass (type_identifier) @extends)
(super_interfaces (type_list (type_identifier) @implements))
(extends_interfaces (type_list (type_identifier) @implements))
(object_creation_expression type: (type_identifier) @call)
(method_invocation object: (identifier) @call)
(type_identifier) @mention
"""

MAX_PARSE_BYTES = 2_000_000  # a generated 5MB file is not what anyone is looking for

# Score weights. Direct evidence is worth far more than fan-out, so a file the
# keyword actually names always outranks something merely adjacent to it.
W_STEM_EXACT = 60.0  # file is named exactly after the keyword
W_STEM_PART = 30.0  # keyword appears inside the file name
W_PATH = 12.0  # keyword appears in a package/directory segment
W_TYPE_DECL = 25.0  # declares a type whose name contains the keyword
W_MEMBER_DECL = 8.0  # declares a fun/val whose name contains the keyword
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
MAX_SYMBOL_SPREAD = 0.2  # a type referenced across more of the repo than this is noise
MAX_SYMBOL_FILES = 50  # ...and never more than this many, however large the repo is
AMBIGUOUS_DECL_FILES = 4  # a name this many files declare is a common noun, not an identity
FANOUT = [0.35, 0.12, 0.05, 0.02, 0.01]  # share of a file's score passed on per hop
TEST_PENALTY = 0.3

# Not every reference to a type is the same kind of evidence. Subclassing it is a
# structural commitment, constructing or calling it is real use, and a bare type
# mention may be a parameter type — so the fan-out share is scaled by which one
# it is. "Who implements this interface" is the question this answers.
EDGE_WEIGHT = {
    "extends": 1.6,
    "implements": 1.6,
    "call": 1.15,
    "mention": 1.0,
}
EDGE_VERB = {
    "extends": "extends",
    "implements": "implements",
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
# OpenApiSpec.java is production code and demoting it (or dropping it entirely
# under --no-tests) loses a file that matters.
TEST_STEM = re.compile(r"(Test|Tests|IT)$")
TEST_SUFFIXES = ("Test", "Tests", "Spec", "IT")

EVIDENCE_WIDTH = 96  # a source line is evidence, not a paragraph
BORING_LINE = re.compile(r"^\s*(?:import|package)\b")  # true, and tells you nothing
CACHE_VERSION = 3  # bumped: the cache now holds a full structural index


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def rg(args, roots):
    """Run ripgrep over roots, returning stdout ('' when nothing matched)."""
    # rg's built-in `java` type also covers .properties/.jsp, and --type-add adds
    # to an existing type rather than replacing it, so this defines a fresh name.
    # One --type-add per glob: the comma form is accepted but matches nothing.
    cmd = ["rg", "--no-messages"]
    for ext in SOURCE_EXTS:
        cmd += ["--type-add", f"javasrc:*{ext}"]
    cmd += ["-t", "javasrc"]
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
            ["fd", ".", "-t", "f", "-e", "java"]
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
            files += [os.path.join(dirpath, f) for f in filenames if f.endswith(SOURCE_EXTS)]
    return files


def counts(pattern, roots, word=False):
    """Per-file match counts for a pattern.

    Text frequency, deliberately including comments and string literals: a
    keyword in a log message or a KDoc block is still a sign the file is about
    the thing. Structure comes from the parse; this is the other half.
    """
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
    """Each file's first keyword mention as (line number, line text).

    Only used for files the keyword touches textually; a file's declarations
    already carry their own exact positions from the parse, so the import-line
    filtering the regex era needed here is gone.
    """
    hits = {}
    for line in rg(
        ["-i", "-n", "-m", str(limit), "--no-heading", "-e", pattern], roots
    ).splitlines():
        parts = line.split(":", 2)
        if len(parts) != 3 or not parts[1].isdigit():
            continue
        path, number, text = parts[0], int(parts[1]), parts[2]
        previous = hits.get(path)
        # The first match is usually the import that pulled the name in, which
        # shows nothing, so a few lines are read and the first line that is not
        # import or package boilerplate wins.
        if previous is None or (
            BORING_LINE.match(previous[1]) and not BORING_LINE.match(text.strip())
        ):
            hits[path] = (number, trim(text))
    return hits


def count_lines(path):
    """File length, so the caller can budget how much reading it is signing up for."""
    try:
        with open(path, "rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def cache_path(roots):
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    key = hashlib.sha1("\0".join(sorted(roots)).encode()).hexdigest()[:16]
    return os.path.join(base, "context-builder", f"java-{key}.json")


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
    return blob.get("index") if blob.get("stamp") == cache_stamp(files) else None


def write_cache(roots, files, index):
    path = cache_path(roots)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}"
        with open(tmp, "w") as handle:
            json.dump({"stamp": cache_stamp(files), "index": index}, handle)
        os.replace(tmp, path)
    except OSError:
        pass  # a cache that cannot be written is not an error worth reporting


def captures(query, node):
    """capture name -> [node], across tree-sitter binding versions."""
    if QueryCursor is not None:
        return QueryCursor(query).captures(node)
    return query.captures(node)


def build_index(roots, files, use_cache=True):
    """Parse every source once and record what it declares, imports and references.

    Returns a dict of four maps plus a note:

      decls[file]   -> [[name, line, source_line], ...]  in file order
      members[file] -> [name, ...]                       funs and properties
      imports[file] -> [simple name, ...]                what it pulls in
      refs[file]    -> {name: edge kind}                 strongest edge per name

    `refs` is filtered down to names that something in this tree actually
    declares, because a reference to `String` or `Int` is not a cross-file edge
    and keeping it would bloat both the cache and the fan-out. The whole thing is
    cached against the tree's newest mtime, since a research session runs this
    script several times with different keywords and the parse never changes.
    """
    cached = read_cache(roots, files) if use_cache else None
    if cached is not None:
        return cached, None

    language = Language(tree_sitter_java.language())
    parser = Parser(language)
    query = Query(language, JAVA_QUERY)

    decls, members, imports, refs = {}, {}, {}, {}
    unreadable = 0
    for path in files:
        try:
            with open(path, "rb") as handle:
                src = handle.read(MAX_PARSE_BYTES)
        except OSError:
            unreadable += 1
            continue
        lines = src.split(b"\n")
        found = captures(query, parser.parse(src).root_node)

        def text_of(node):
            return src[node.start_byte : node.end_byte].decode("utf-8", "replace")

        file_decls = []
        for node in found.get("decl", ()):
            row = node.start_point[0]
            raw = lines[row].decode("utf-8", "replace") if row < len(lines) else ""
            file_decls.append([text_of(node), row + 1, trim(raw)])
        if file_decls:
            file_decls.sort(key=lambda d: d[1])
            decls[path] = file_decls

        names = {text_of(n) for n in found.get("member", ())}
        if names:
            members[path] = sorted(names)

        # `import a.b.Zed;` and `import a.b.*;`: the simple name is what a type
        # reference in the body will actually say, so that is what is indexed.
        # A static import contributes its member name, which the declared-name
        # filter below discards unless something really does declare it.
        pulled = set()
        for node in found.get("import", ()):
            tail = text_of(node).rstrip(".*").rsplit(".", 1)[-1]
            if tail:
                pulled.add(tail)
        if pulled:
            imports[path] = sorted(pulled)

        edges = {}
        for kind in ("extends", "implements", "call", "mention"):
            for node in found.get(kind, ()):
                name = text_of(node)
                known = edges.get(name)
                # Keep the strongest relationship seen anywhere in the file.
                if known is None or EDGE_WEIGHT[kind] > EDGE_WEIGHT[known]:
                    edges[name] = kind
        if edges:
            refs[path] = edges

    declared = {name for entries in decls.values() for name, _, _ in entries}
    refs = {
        path: {n: k for n, k in edges.items() if n in declared}
        for path, edges in refs.items()
    }
    refs = {p: e for p, e in refs.items() if e}

    index = {"decls": decls, "members": members, "imports": imports, "refs": refs}
    if use_cache:
        write_cache(roots, files, index)
    note = f"{unreadable} files could not be read" if unreadable else None
    return index, note


def invert_refs(index, file_count):
    """name -> {file: edge kind}, the reverse of index['refs'].

    Names referenced across more of the repo than MAX_SYMBOL_SPREAD are dropped:
    a `Logger` or a `Result` reaches most of the codebase and says nothing about
    relevance, and letting it fan out drowns the files that do.
    """
    out = defaultdict(dict)
    for path, edges in index["refs"].items():
        for name, kind in edges.items():
            out[name][path] = kind
    # The percentage alone is not enough on a large repo: 20% of 2,000 files is
    # 400, and a `Builder` referenced by 355 of them would sail through. The
    # absolute ceiling is what actually keeps infrastructure types out.
    cap = max(10, min(MAX_SYMBOL_FILES, int(file_count * MAX_SYMBOL_SPREAD)))
    return {n: r for n, r in out.items() if len(r) <= cap}


def unambiguous_definers(files_by_type):
    """files_by_type minus the names that too many files declare.

    `Builder`, `Config`, `Request` and `Response` are each declared in dozens to
    hundreds of places in a large repo — usually as a nested class. An import of
    one says nothing about which file is meant, so letting it fan out just hands
    score to whichever file happens to declare a nested type of that name.
    """
    return {
        name: paths
        for name, paths in files_by_type.items()
        if len(paths) <= AMBIGUOUS_DECL_FILES
    }


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


def score_keyword(keyword, files, roots, root, index, files_by_type, referrers, min_fuzzy):
    """Everything one keyword contributes on its own: score, evidence, vocabulary.

    Returned separately per keyword so that several keywords can be combined
    either as a union or as an intersection (--all) without the passes below
    having to know which mode is in play.
    """
    pattern = keyword_pattern(keyword)
    sub_hits = counts(pattern, roots)
    word_hits = counts(pattern, roots, word=True)

    exact = re.compile(rf"^{pattern}$", re.I)
    partial = re.compile(pattern, re.I)

    # Anchor each result at the most useful line. Least specific first: the file's
    # own primary declaration, then its first textual mention of the keyword, then
    # — best of all — the declaration whose name actually matches.
    hits = {p: (d[0][1], d[0][2]) for p, d in index["decls"].items() if d}
    hits.update(first_hit(pattern, roots))
    for path, entries in index["decls"].items():
        for name, line, text in entries:
            if partial.search(name):
                hits[path] = (line, text)
                break

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
        # Declarations and members come from the parse, so a match here is a real
        # declaration — never a mention of the word in a comment above one.
        matching_types = [n for n, _, _ in index["decls"].get(path, ()) if partial.search(n)]
        if matching_types:
            score[path] += W_TYPE_DECL
            why[path].append(f"declares {matching_types[0]}")
        matching_members = [n for n in index["members"].get(path, ()) if partial.search(n)]
        if matching_members:
            score[path] += W_MEMBER_DECL * min(len(matching_members), 3)
            why[path].append(f"declares {matching_members[0]}")
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
    for term, sim in fuzzy.items():
        weight = sim * FUZZY_DISCOUNT
        # Where the term lives, plus everything referencing it. The first half
        # matters on its own: a term recovered from a file stem is not always an
        # identifier anything references, and the file it names is the answer.
        touching = set(referrers.get(term, {}))
        touching |= set(files_by_type.get(term, ()))
        touching |= {p for p in files if stem_of(p) == term}
        for path in touching:
            entries = index["decls"].get(path, ())
            gain = W_FUZZY_MENTION
            if stem_of(path) == term:
                gain += W_STEM_PART
                carrier.add(path)
            if any(n == term for n, _, _ in entries):
                gain += W_TYPE_DECL
                # Only a file's *primary* declaration makes it the thing itself.
                # A nested record of that name — one of forty inside a large API
                # class — is evidence the file is a collaborator, not the answer,
                # so it scores but stays out of the top tier.
                if entries[0][0] == term:
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
        description="Rank Java source files to read for a keyword.",
        epilog="Example: search-java-sources.py AuthToken ~/work/api",
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
    ap.add_argument("--no-cache", action="store_true", help="ignore the parsed-index cache")
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
        die(f"no .java files under {', '.join(roots)}")
    candidates = set(files)
    if from_file and from_file not in candidates:
        files.append(from_file)  # seeding from outside the source sets is allowed
        candidates.add(from_file)

    # One capped label, used by both the success banner and the no-match message:
    # a monorepo has hundreds of source sets and printing them all buries the
    # sentence the reader actually needs.
    rel_names = [os.path.relpath(r, root) for r in roots]
    rel_roots = ", ".join(
        rel_names[:4] + ([f"(+{len(rel_names) - 4} more)"] if len(rel_names) > 4 else [])
    )
    index, index_note = build_index(roots, files, use_cache=not args.no_cache)
    if index_note:
        print(f"note: {index_note}", file=sys.stderr)
    files_by_type = defaultdict(set)
    for path, entries in index["decls"].items():
        for name, _, _ in entries:
            files_by_type[name].add(path)
    referrers = invert_refs(index, len(files))
    defining = unambiguous_definers(files_by_type)

    passes = [
        score_keyword(kw, files, roots, root, index, files_by_type, referrers, args.fuzzy)
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
    hits = {} if passes else {p: (d[0][1], d[0][2]) for p, d in index["decls"].items() if d}
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
        own = [stem_of(from_file)] + [n for n, _, _ in index["decls"].get(from_file, ())]
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
            for name, _, _ in index["decls"].get(src, ()):
                on_topic = not gated or bool(topical.search(name))
                for ref, kind in referrers.get(name, {}).items():
                    if ref != src and (on_topic or ref in direct):
                        shares[ref].append(share * EDGE_WEIGHT[kind])
                        note_reason(ref, f"{EDGE_VERB[kind]} {name}")
            for name in index["imports"].get(src, ()):
                on_topic = not gated or bool(topical.search(name))
                for defn in defining.get(name, ()):
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
        frontier = [p for p in sorted(gains, key=gains.get, reverse=True) if p not in seen][
            : args.seeds
        ]
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
                "declares": [n for n, _, _ in index["decls"].get(path, ())],
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
    noun = "file" if len(rows) == 1 else "files"
    total = sum(r["lines_total"] for r in rows)
    joiner = " + " if args.all else " / "
    label = joiner.join(f"'{k}'" for k in keywords)
    if from_file:
        start = os.path.relpath(from_file, root)
        label = f"{label} from @{start}" if label else f"files around @{start}"
    print(f"Reading list for {label} — {len(rows)} {noun}, ~{total:,} lines to read")
    print(f"searched {len(files)} sources under {rel_roots}")
    print("Read top-down; stop as soon as the question is answered.\n")

    width = max(len(r["file"]) + len(str(r["line"] or "")) + 2 for r in rows)
    width = min(width, 64)
    for tier, (name, blurb) in enumerate(TIERS):
        group = [r for r in rows if min(r["hops"], 2) == tier]
        if not group:
            continue
        tier_lines = sum(r["lines_total"] for r in group)
        print(
            f"{name} ({len(group)} {'file' if len(group) == 1 else 'files'}, "
            f"~{tier_lines:,} lines) — {blurb}"
        )
        for row in group:
            where = f"@{row['file']}:{row['line']}" if row["line"] else f"@{row['file']}"
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
                print(f"       tested by @{test}")
        print()

    if config:
        print(f"ALSO MENTIONED ({len(config)} config/resource "
              f"{'file' if len(config) == 1 else 'files'}, not sources)")
        for entry in config:
            print(f"       @{entry['file']}:{entry['line']}")


if __name__ == "__main__":
    main()
