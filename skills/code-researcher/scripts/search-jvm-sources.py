#!/usr/bin/env python3
"""search-jvm-sources.py — rank Java/Kotlin source files to read for a keyword.

Usage: <skill-dir>/scripts/search-jvm-sources.py <keyword> [root] [-n 200] [--json]

Searches only real source roots (src/main/java, src/main/kotlin, src/...), never
build/, out/, target/ or generated output. Files that mention the keyword become
seeds — including fuzzily, so a misspelled or abbreviated name still lands — and
the ranking then fans out along type references and imports, so the list also
contains the callers and collaborators you need to make sense of the seeds.
Past the first hop the fan-out is gated on the keyword, so it stays a search
instead of drifting into the dependency closure. Prints at most 200 files.
"""

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
from collections import defaultdict
from difflib import SequenceMatcher

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
FUZZY_DISCOUNT = 0.8  # fuzzy evidence never outweighs the same real evidence
MAX_FUZZY_TERMS = 8  # one loose keyword must not drag in half the vocabulary
HITS_CAP = 8  # ignore mention counts past this, one busy file isn't the answer
FANOUT = [0.35, 0.12, 0.05, 0.02, 0.01]  # share of a file's score passed on per hop
TEST_PENALTY = 0.3

# Output tiers, indexed by how many hops from a keyword match a file was found.
# The point is to tell a reader where to start and where it is safe to stop.
TIERS = [
    ("READ FIRST", "the keyword is named or declared here"),
    ("THEN", "direct collaborators of the files above"),
    ("SKIM IF NEEDED", "further out, reached through an on-topic type"),
]

TEST_PATH = re.compile(r"(^|/)(test|tests|androidTest|integrationTest|testFixtures)(/|$)")
TEST_STEM = re.compile(r"(Test|Tests|Spec|IT)$")


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


def first_line(pattern, roots):
    """Line number of each file's first match, so results are clickable."""
    lines = {}
    for line in rg(["-i", "-n", "-m", "1", "--no-heading", "-e", pattern], roots).splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] not in lines:
            lines[parts[0]] = int(parts[1])
    return lines


def ast_grep(args):
    proc = subprocess.run(["ast-grep"] + args, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        die(f"ast-grep failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else []


def declared_types(roots, candidates):
    """Map file -> type names it declares, and name -> files declaring it.

    Uses ast-grep so that only real declarations count; the regex fallback also
    matches prose in comments ("the record that ..."), which then fans out to
    every file mentioning the word "that".
    """
    pairs = []
    note = None
    if shutil.which("ast-grep"):
        for rule in AST_RULES:
            pairs += [
                (m["file"], m["text"])
                for m in ast_grep(["scan", "--inline-rules", rule, "--json=compact"] + roots)
            ]
        for m in ast_grep(
            ["run", "-p", AST_OBJECT_PATTERN, "-l", "kotlin", "--json=compact"] + roots
        ):
            name = m["metaVariables"]["single"].get("N", {}).get("text")
            if name:
                pairs.append((m["file"], name))
    else:
        note = "`ast-grep` is unavailable; using an rg declaration regex instead."
        out = rg(["-o", "--null", "--no-line-number", "-r", "$1", "-e", TYPE_DECL], roots)
        for line in out.splitlines():
            path, sep, name = line.partition("\0")
            if sep and name:
                pairs.append((path, name))

    by_file, by_name = defaultdict(set), defaultdict(set)
    for path, name in pairs:
        if path in candidates:  # ast-grep does not honour our exclude globs
            by_file[path].add(name)
            by_name[name].add(path)
    return by_file, by_name, note


def references(names, roots):
    """Map type name -> files mentioning it, in a single ripgrep pass."""
    hits = defaultdict(set)
    if not names:
        return hits
    pattern = r"\b(?:" + "|".join(sorted(re.escape(n) for n in names)) + r")\b"
    out = rg(["-w", "-o", "--json", "-e", pattern], roots)
    path = None
    for line in out.splitlines():
        event = json.loads(line)
        if event["type"] == "begin":
            path = event["data"]["path"].get("text")
        elif event["type"] == "match" and path:
            for sub in event["data"]["submatches"]:
                hits[sub["match"]["text"]].add(path)
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


def count_lines(path):
    """File length, so the caller can budget how much reading it is signing up for."""
    try:
        with open(path, "rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def is_test(path):
    return bool(TEST_PATH.search(path)) or bool(
        TEST_STEM.search(os.path.splitext(os.path.basename(path))[0])
    )


def main():
    ap = argparse.ArgumentParser(
        description="Rank Java/Kotlin source files to read for a keyword.",
        epilog="Example: search-jvm-sources.py AuthToken ~/work/api",
    )
    ap.add_argument("keyword", help="what you are looking for (camelCase or spaced)")
    ap.add_argument("root", nargs="?", default=".", help="search root (default: .)")
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
    ap.add_argument("--no-tests", action="store_true", help="drop test sources entirely")
    ap.add_argument("--json", action="store_true", help="emit JSON for jq")
    args = ap.parse_args()

    if not shutil.which("rg"):
        die("`rg` is unavailable and this script has no fallback for it; install ripgrep.")
    root = os.path.abspath(os.path.expanduser(args.root))
    if not os.path.isdir(root):
        die(f"{args.root} is not a directory")

    roots, note = find_source_roots(root)
    files = list_sources(roots)
    if not files:
        die(f"no .java/.kt/.kts files under {', '.join(roots)}")

    pattern = keyword_pattern(args.keyword)
    type_pattern = rf"(?:class|interface|object|enum|record)\s+\w*(?:{pattern})\w*"
    sub_hits = counts(pattern, roots)
    word_hits = counts(pattern, roots, word=True)
    type_hits = counts(type_pattern, roots)
    member_hits = counts(rf"(?:fun|val|var|void|public|private|protected)\s+\w*(?:{pattern})\w*", roots)

    # Anchor each result at the most useful line: the matching declaration if
    # there is one, else the first mention, else whatever type the file declares
    # (fan-out results have no mention of their own).
    lines = first_line(TYPE_DECL, roots)
    lines.update(first_line(pattern, roots))
    lines.update(first_line(type_pattern, roots))

    exact = re.compile(rf"^{pattern}$", re.I)
    partial = re.compile(pattern, re.I)
    # Used to keep deep hops on topic. A type is on topic if its name carries any
    # word of the keyword — "mcp server" reaches McpServlet and ServerIdentity,
    # but not CallToolResult. Words under three letters are too common to gate on.
    words = [re.escape(p) for p in keyword_parts(args.keyword) if len(p) >= 3]
    topical_words = list(words)
    topical = re.compile("|".join(words) if words else pattern, re.I)

    score = defaultdict(float)
    why = defaultdict(list)
    for path in files:
        stem = os.path.splitext(os.path.basename(path))[0]
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
            why[path].append(f"{n_sub} mention{'s' if n_sub > 1 else ''}")

    def note_reason(path, reason):
        if reason not in why[path] and len(why[path]) < 3:
            why[path].append(reason)

    rel_roots = ", ".join(os.path.relpath(r, root) for r in roots)
    types_by_file, files_by_type, ast_note = declared_types(roots, set(files))
    if ast_note:
        print(f"note: {ast_note}", file=sys.stderr)

    # Fuzzy pass. Matching runs against the repo's own vocabulary — file names and
    # declared type names — rather than against raw text, because that is where a
    # misspelling or an abbreviation is recoverable: "srvconfig" is 0.86 similar to
    # ServerConfig and under 0.5 to everything else. Fuzzy evidence is discounted
    # by similarity so it always sits below a real match.
    vocabulary = {os.path.splitext(os.path.basename(p))[0] for p in files} | set(files_by_type)
    ranked_vocab = sorted(
        ((similarity(args.keyword, term), term) for term in vocabulary if not partial.search(term)),
        reverse=True,
    )
    fuzzy = dict((t, s) for s, t in ranked_vocab if s >= args.fuzzy) if args.fuzzy > 0 else {}
    fuzzy = dict(sorted(fuzzy.items(), key=lambda kv: -kv[1])[:MAX_FUZZY_TERMS])

    exact_hit = {p for p, s in score.items() if s > 0}
    fuzzy_carrier = set()  # files that are named after / declare a fuzzy match
    if fuzzy:
        fuzzy_mentions = references(set(fuzzy), roots)  # one pass for every term
        for term, sim in fuzzy.items():
            weight = sim * FUZZY_DISCOUNT
            for path in fuzzy_mentions.get(term, ()):
                stem = os.path.splitext(os.path.basename(path))[0]
                gain = W_FUZZY_MENTION
                if stem == term:
                    gain += W_STEM_PART
                    fuzzy_carrier.add(path)
                if term in types_by_file.get(path, ()):
                    gain += W_TYPE_DECL
                    fuzzy_carrier.add(path)
                score[path] += gain * weight
                note_reason(path, f"≈{term} ({sim:.2f})")
        # A fuzzily-matched name is on topic for the deep-hop gate too, or the
        # collaborators of the file we just recovered would be unreachable.
        topical = re.compile("|".join(topical_words + [re.escape(t) for t in fuzzy]), re.I)

    direct = {p: s for p, s in score.items() if s > 0}
    if not direct:
        print(f"nothing matches '{args.keyword}' under {rel_roots}", file=sys.stderr)
        near = [f"{t} ({s:.2f})" for s, t in ranked_vocab[:3] if s > 0.4]
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
        if path not in exact_hit and path not in fuzzy_carrier:
            origin[path] = 1

    frontier = sorted(direct, key=direct.get, reverse=True)[: args.seeds]
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
                for ref in referrers.get(name, ()):
                    if ref != src and (on_topic or ref in direct):
                        shares[ref].append(share)
                        note_reason(ref, f"uses {name}")
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
    ranked = ranked[: min(args.n, HARD_LIMIT)]
    # Which files make the cut is decided by score; the order they are listed in
    # is the order to read them, tier first. Numbering follows the reading order.
    ranked.sort(key=lambda r: (min(origin.get(r[1], 0), 2), -r[0], r[1]))

    rows = [
        {
            "rank": i,
            "score": round(value, 1),
            "file": os.path.relpath(path, root),
            "line": lines.get(path),
            "lines_total": count_lines(path),
            "hops": origin.get(path, 0),
            "tier": TIERS[min(origin.get(path, 0), 2)][0],
            "test": test,
            "why": why[path][:3],
        }
        for i, (value, path, test) in enumerate(ranked, 1)
    ]

    if args.json:
        print(
            json.dumps(
                {
                    "keyword": args.keyword,
                    "roots": roots,
                    "sources_searched": len(files),
                    "lines_total": sum(r["lines_total"] for r in rows),
                    "results": rows,
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
            f"note: {len(direct)} files mention '{args.keyword}' — this is the top {len(rows)}; "
            "a more specific keyword will read better",
            file=sys.stderr,
        )
    shown = [os.path.relpath(r, root) for r in roots]
    if len(shown) > 4:
        shown = shown[:4] + [f"(+{len(roots) - 4} more)"]
    noun = "file" if len(rows) == 1 else "files"
    total = sum(r["lines_total"] for r in rows)
    print(f"Reading list for '{args.keyword}' — {len(rows)} {noun}, ~{total:,} lines to read")
    print(f"searched {len(files)} sources under {', '.join(shown)}")
    print("Read top-down; stop as soon as the question is answered.\n")

    width = max(len(r["file"]) + len(str(r["line"] or "")) + 1 for r in rows)
    for tier, (name, blurb) in enumerate(TIERS):
        group = [r for r in rows if min(r["hops"], 2) == tier]
        if not group:
            continue
        tier_lines = sum(r["lines_total"] for r in group)
        print(f"{name} ({len(group)} {'file' if len(group) == 1 else 'files'}, "
              f"~{tier_lines:,} lines) — {blurb}")
        for row in group:
            where = f"{row['file']}:{row['line']}" if row["line"] else row["file"]
            tag = " [test]" if row["test"] else ""
            print(f"  {row['rank']:>3}. {where:<{width}}  {', '.join(row['why'])}{tag}")
        print()


if __name__ == "__main__":
    main()
