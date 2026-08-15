#!/usr/bin/env python3
"""search-ts-sources.py — rank TypeScript/JavaScript files to read for a keyword.

Usage: <skill-dir>/scripts/search-ts-sources.py <keyword> [root] [-n 200] [--json]

The TypeScript counterpart of search-jvm-sources.py: same reading-list output,
different notion of what a module is. Files that mention the keyword become seeds
— including fuzzily, so a misspelled or abbreviated name still lands — and the
ranking fans out along exported-symbol references *and resolved import paths*,
because a TS import names a file, not a type. Past the first hop the fan-out is
gated on the keyword, so it stays a search instead of drifting into the whole
module graph. Prints at most 200 files, best first.
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

# Build output, caches and vendored code — never search results.
EXCLUDE_GLOBS = [
    "!**/node_modules/**",
    "!**/dist/**",
    "!**/build/**",
    "!**/out/**",
    "!**/.next/**",
    "!**/.nuxt/**",
    "!**/.svelte-kit/**",
    "!**/.expo/**",
    "!**/coverage/**",
    "!**/.turbo/**",
    "!**/.vercel/**",
    "!**/storybook-static/**",
    "!**/.cache/**",
    "!**/generated/**",
    "!**/__snapshots__/**",
    "!**/.git/**",
]
EXCLUDE_DIRS = {
    "node_modules",
    "dist",
    "build",
    "out",
    ".next",
    ".nuxt",
    ".svelte-kit",
    ".expo",
    "coverage",
    ".turbo",
    ".vercel",
    "storybook-static",
    ".cache",
    "generated",
    "__snapshots__",
    ".git",
}
SOURCE_EXTS = (".ts", ".tsx", ".js", ".jsx", ".mts", ".cts", ".mjs", ".cjs")
# Where source lives when a project bothers to separate it. Unlike Maven/Gradle
# there is no single convention, so these are checked at any depth — which is
# also what makes monorepos (packages/*/src, apps/*/src) work for free.
# Test trees are included on purpose — they are demoted at ranking time and
# tagged [test], not hidden, because how a thing is called in a test is often
# the fastest explanation of what it does.
SOURCE_DIR_NAMES = ("src", "app", "lib", "source", "tests", "test", "__tests__", "e2e")

# Fallback declaration regexes, used only when ast-grep is missing.
TYPE_DECL = r"export\s+(?:default\s+)?(?:abstract\s+)?(?:class|interface|type|enum|function)\s+([A-Za-z_$][\w$]*)"
CONST_DECL = r"export\s+(?:const|let|var)\s+([A-Za-z_$][\w$]*)"
# `from "x"`, `require("x")`, `import("x")` — the module graph, as written.
MODULE_SPEC = r"""(?:from|require\(|import\()\s*['"]([^'"]+)['"]"""
# The names pulled out of a module: `import { a, b as c }`, `import d from`.
IMPORT_NAMES = r"""import\s+(?:type\s+)?([\w${},\s*]+?)\s+from"""

# Declarations by node kind, so comments and strings cannot contribute names.
# `.ts` and `.tsx` need separate passes: the tsx grammar reads only .tsx files
# and vice versa, silently matching nothing if you use the wrong one.
#
# Only *exported* declarations count. A cross-file graph can only be built from
# names other files can import, and picking up locals means every `const user`
# and `const res` in the repo becomes an edge joining unrelated files.
# `inside` here must stay direct-parent: with `stopBy: end` it climbs the whole
# ancestor chain, so a local inside an exported function counts as exported.
AST_RULES = [
    """
id: ts-decl-names
language: {lang}
rule:
  any:
    - kind: type_identifier
      inside:
        any:
          - {{kind: class_declaration}}
          - {{kind: interface_declaration}}
          - {{kind: type_alias_declaration}}
        inside: {{kind: export_statement}}
    - kind: identifier
      inside:
        any:
          - {{kind: function_declaration}}
          - {{kind: enum_declaration}}
        inside: {{kind: export_statement}}
    - kind: identifier
      inside:
        kind: variable_declarator
        inside: {{kind: lexical_declaration, inside: {{kind: export_statement}}}}
"""
]
AST_LANGS = ("typescript", "tsx")

# Score weights. Direct evidence is worth far more than fan-out, so a file the
# keyword actually names always outranks something merely adjacent to it.
W_STEM_EXACT = 60.0  # file is named exactly after the keyword
W_STEM_PART = 30.0  # keyword appears inside the file name
W_PATH = 12.0  # keyword appears in a directory segment
W_TYPE_DECL = 25.0  # declares a symbol whose name contains the keyword
W_MEMBER_DECL = 8.0  # declares a member/prop whose name contains the keyword
W_WORD_HIT = 3.0  # standalone-word mention
W_SUB_HIT = 1.0  # mention inside a longer identifier
W_FUZZY_MENTION = 6.0  # mention of a fuzzily-matched name, before the discount
FUZZY_DISCOUNT = 0.8  # fuzzy evidence never outweighs the same real evidence
MAX_FUZZY_TERMS = 8  # one loose keyword must not drag in half the vocabulary
HITS_CAP = 8  # ignore mention counts past this, one busy file isn't the answer
MAX_SYMBOL_SPREAD = 0.2  # a symbol used across more of the repo than this is noise
FANOUT = [0.35, 0.12, 0.05, 0.02, 0.01]  # share of a file's score passed on per hop
TEST_PENALTY = 0.3
BARREL_PENALTY = 0.35  # an index.ts that only re-exports teaches you nothing

# Output tiers, indexed by how many hops from a keyword match a file was found.
TIERS = [
    ("READ FIRST", "the keyword is named or declared here"),
    ("THEN", "imported by / imports the files above"),
    ("SKIM IF NEEDED", "further out, reached through an on-topic module"),
]

TEST_PATH = re.compile(r"(^|/)(__tests__|__mocks__|tests?|e2e|cypress|playwright)(/|$)")
TEST_STEM = re.compile(r"\.(test|spec|stories|e2e|cy)$")


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def rg(args, roots):
    """Run ripgrep over roots, returning stdout ('' when nothing matched)."""
    # One --type-add per glob: the comma form is accepted but matches nothing.
    cmd = ["rg", "--no-messages"]
    for ext in SOURCE_EXTS:
        cmd += ["--type-add", f"tsjs:*{ext}"]
    cmd += ["-t", "tsjs"]
    for glob in EXCLUDE_GLOBS:
        cmd += ["-g", glob]
    cmd += args + [str(r) for r in roots]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in (0, 1):  # 1 == no matches, which is not an error
        die(f"rg failed: {proc.stderr.strip()}")
    return proc.stdout


def keyword_parts(keyword):
    """The keyword's own words: "user profile" -> ["user", "profile"]."""
    parts = [p for p in re.split(r"[^A-Za-z0-9]+", keyword) if p]
    if len(parts) == 1:
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", parts[0]) or parts
    return parts


def similarity(keyword, term):
    """How close an identifier is to the keyword, 0..1.

    Two readings, best one wins: the whole strings compared with separators
    stripped (catches typos — "usePofile" vs useProfile), and a word-by-word
    alignment (catches word order and abbreviation — "auth ctx" vs AuthContext).
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
    """A regex tolerant of camelCase/kebab-case/snake_case spellings.

    "user profile", "userProfile", "user-profile" and "user_profile" all compile
    to the same pattern — kebab-case matters here in a way it does not on the JVM,
    since TS file names use it constantly.
    """
    return r"[_\-]?".join(re.escape(p) for p in keyword_parts(keyword))


def find_source_roots(root):
    """Locate source trees under root; returns (roots, note_for_the_user)."""
    names = "|".join(SOURCE_DIR_NAMES)
    if shutil.which("fd"):
        proc = subprocess.run(
            ["fd", "-t", "d", "--full-path", rf"/({names})$"]
            + [x for g in EXCLUDE_GLOBS for x in ("-E", g.lstrip("!"))]
            + [str(root)],
            capture_output=True,
            text=True,
        )
        found = [line.rstrip("/") for line in proc.stdout.splitlines() if line]
        note = None
    else:
        note = "`fd` is unavailable; using os.walk instead."
        found = []
        for dirpath, dirnames, _ in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
            if os.path.basename(dirpath) in SOURCE_DIR_NAMES:
                found.append(dirpath)

    # Drop roots nested inside another root, so a package's src/app/ does not get
    # counted twice under its own src/.
    roots = sorted(set(found))
    roots = [r for r in roots if not any(r != o and r.startswith(o + "/") for o in roots)]
    if not roots:
        inside_src = any(part in SOURCE_DIR_NAMES for part in str(root).split("/"))
        return [str(root)], (
            None if inside_src else "no src/app/lib directory found; searching the whole root"
        )
    return roots, note


def list_sources(roots):
    if shutil.which("fd"):
        cmd = ["fd", ".", "-t", "f"]
        for ext in SOURCE_EXTS:
            cmd += ["-e", ext.lstrip(".")]
        cmd += [x for g in EXCLUDE_GLOBS for x in ("-E", g.lstrip("!"))] + roots
        proc = subprocess.run(cmd, capture_output=True, text=True)
        return [line for line in proc.stdout.splitlines() if line]
    files = []
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS and not d.startswith(".")]
            files += [os.path.join(dirpath, f) for f in filenames if f.endswith(SOURCE_EXTS)]
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


def count_lines(path):
    """File length, so the caller can budget how much reading it is signing up for."""
    try:
        with open(path, "rb") as handle:
            return sum(1 for _ in handle)
    except OSError:
        return 0


def ast_grep(args):
    proc = subprocess.run(["ast-grep"] + args, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        die(f"ast-grep failed: {proc.stderr.strip()}")
    return json.loads(proc.stdout) if proc.stdout.strip() else []


def declared_names(roots, candidates):
    """Map file -> symbols it declares, and symbol -> files declaring it.

    Uses ast-grep so that only real declarations count; the regex fallback also
    matches prose in comments, which then fans out to every file using that word.
    """
    pairs = []
    note = None
    if shutil.which("ast-grep"):
        for lang in AST_LANGS:
            for rule in AST_RULES:
                pairs += [
                    (m["file"], m["text"])
                    for m in ast_grep(
                        ["scan", "--inline-rules", rule.format(lang=lang), "--json=compact"] + roots
                    )
                ]
    else:
        note = "`ast-grep` is unavailable; using rg declaration regexes instead."
        for expr in (TYPE_DECL, CONST_DECL):
            out = rg(["-o", "--null", "--no-line-number", "-r", "$1", "-e", expr], roots)
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
    """Map symbol name -> files mentioning it, in a single ripgrep pass."""
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


def module_index(files):
    """Index every path suffix -> files, for resolving non-relative imports.

    `@/lib/auth`, `~/lib/auth` and `src/lib/auth` all have to land on the same
    file without reading tsconfig paths, so every suffix of every candidate is
    indexed and the longest match wins.
    """
    index = defaultdict(set)
    for path in files:
        stripped = re.sub(r"\.(tsx?|jsx?|mts|cts|mjs|cjs)$", "", path)
        for candidate in (stripped, re.sub(r"/index$", "", stripped)):
            segments = candidate.split("/")
            for i in range(1, min(len(segments), 5) + 1):
                index["/".join(segments[-i:])].add(path)
    return index


def resolve_module(spec, importer, index, candidates):
    """Resolve one import specifier to files in this repo ('' for externals)."""
    if not spec or spec[0] not in "./@~#" and not spec.startswith("src/"):
        return set()  # a bare package name: react, zod, lodash
    if spec.startswith("."):
        base = os.path.normpath(os.path.join(os.path.dirname(importer), spec))
        hits = set()
        for ext in SOURCE_EXTS:
            for candidate in (base + ext, os.path.join(base, "index" + ext)):
                if candidate in candidates:
                    hits.add(candidate)
        return hits
    # Aliased: strip the alias prefix and match on the longest path suffix.
    trimmed = re.sub(r"^[@~#][\w.-]*/|^src/", "", spec).strip("/")
    segments = trimmed.split("/")
    for i in range(len(segments), 0, -1):
        hit = index.get("/".join(segments[-i:]))
        if hit:
            return set(hit)
    return set()


def imported_modules(files, index, candidates):
    """Map file -> the repo files it imports, resolved through the module graph."""
    edges = defaultdict(set)
    if not files:
        return edges
    out = rg(["-o", "--null", "--no-line-number", "-r", "$1", "-e", MODULE_SPEC], files)
    for line in out.splitlines():
        path, sep, spec = line.partition("\0")
        if sep and spec:
            edges[path] |= resolve_module(spec, path, index, candidates) - {path}
    return edges


def is_test(path):
    name = os.path.splitext(os.path.basename(path))[0]
    return bool(TEST_PATH.search(path)) or bool(TEST_STEM.search(name))


def is_barrel(path, declared):
    """An index file that declares nothing is a re-export hub, not a place to read."""
    return os.path.splitext(os.path.basename(path))[0] == "index" and not declared


def main():
    ap = argparse.ArgumentParser(
        description="Rank TypeScript/JavaScript files to read for a keyword.",
        epilog="Example: search-ts-sources.py useAuth ~/work/app",
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
    ap.add_argument("--no-tests", action="store_true", help="drop test and story files entirely")
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
        die(f"no .ts/.tsx/.js/.jsx files under {', '.join(roots)}")
    # `app` and `lib` are common directory names outside JS too — an Android
    # `app/` matches the same rule. Keep only roots that hold actual sources.
    roots = [r for r in roots if any(f.startswith(r + "/") for f in files)]

    pattern = keyword_pattern(args.keyword)
    decl_pattern = rf"(?:class|interface|type|enum|function|const|let|var)\s+\w*(?:{pattern})\w*"
    sub_hits = counts(pattern, roots)
    word_hits = counts(pattern, roots, word=True)
    type_hits = counts(decl_pattern, roots)
    member_hits = counts(rf"(?:{pattern})\w*\s*[:(]", roots)

    # Anchor each result at the most useful line: the matching declaration if
    # there is one, else the first mention, else whatever the file declares.
    lines = first_line(TYPE_DECL, roots)
    lines.update(first_line(pattern, roots))
    lines.update(first_line(decl_pattern, roots))

    exact = re.compile(rf"^{pattern}$", re.I)
    partial = re.compile(pattern, re.I)
    words = [re.escape(p) for p in keyword_parts(args.keyword) if len(p) >= 3]
    topical_words = list(words)
    topical = re.compile("|".join(words) if words else pattern, re.I)

    score = defaultdict(float)
    why = defaultdict(list)
    for path in files:
        # `auth.service.ts`, `use-auth.tsx` — take the name before the first dot.
        name = os.path.basename(path)
        stem = name[: name.index(".")] if "." in name else name
        if exact.match(stem):
            score[path] += W_STEM_EXACT
            why[path].append("file name is the keyword")
        elif partial.search(stem):
            score[path] += W_STEM_PART
            why[path].append("keyword in file name")
        segments = os.path.dirname(os.path.relpath(path, root)).split("/")
        if any(partial.search(seg) for seg in segments):
            score[path] += W_PATH
            why[path].append("keyword in directory path")
        if type_hits.get(path):
            score[path] += W_TYPE_DECL
            why[path].append("declares a matching symbol")
        if member_hits.get(path):
            score[path] += W_MEMBER_DECL * min(member_hits[path], 3)
            why[path].append("matching props/members")
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
    candidates = set(files)
    names_by_file, files_by_name, ast_note = declared_names(roots, candidates)
    if ast_note:
        print(f"note: {ast_note}", file=sys.stderr)
    index = module_index(files)

    # Fuzzy pass, against the repo's own vocabulary — file names and declared
    # symbols — rather than raw text, because that is where a misspelling or an
    # abbreviation is recoverable. Discounted by similarity, so it always sits
    # below a real match.
    vocabulary = {os.path.basename(p).split(".")[0] for p in files} | set(files_by_name)
    ranked_vocab = sorted(
        ((similarity(args.keyword, term), term) for term in vocabulary if not partial.search(term)),
        reverse=True,
    )
    fuzzy = dict((t, s) for s, t in ranked_vocab if s >= args.fuzzy) if args.fuzzy > 0 else {}
    fuzzy = dict(sorted(fuzzy.items(), key=lambda kv: -kv[1])[:MAX_FUZZY_TERMS])

    exact_hit = {p for p, s in score.items() if s > 0}
    fuzzy_carrier = set()
    if fuzzy:
        fuzzy_mentions = references(set(fuzzy), roots)  # one pass for every term
        for term, sim in fuzzy.items():
            weight = sim * FUZZY_DISCOUNT
            for path in fuzzy_mentions.get(term, ()):
                name = os.path.basename(path)
                stem = name[: name.index(".")] if "." in name else name
                gain = W_FUZZY_MENTION
                if stem == term:
                    gain += W_STEM_PART
                    fuzzy_carrier.add(path)
                if term in names_by_file.get(path, ()):
                    gain += W_TYPE_DECL
                    fuzzy_carrier.add(path)
                score[path] += gain * weight
                note_reason(path, f"≈{term} ({sim:.2f})")
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

    origin = {p: 0 for p in direct}
    for path in direct:
        if path not in exact_hit and path not in fuzzy_carrier:
            origin[path] = 1

    # Fan out along two kinds of edge: files that reference a seed's exported
    # symbols, and the module graph in both directions (what a seed imports, and
    # who imports the seed). Each hop passes on a fraction of the score.
    importers = defaultdict(set)
    all_imports = imported_modules(files, index, candidates)
    for src, targets in all_imports.items():
        for target in targets:
            importers[target].add(src)

    frontier = sorted(direct, key=direct.get, reverse=True)[: args.seeds]
    seen = set(frontier)
    for hop in range(args.depth):
        if not frontier:
            break
        symbols = {n for f in frontier for n in names_by_file.get(f, ())}
        referrers = references(symbols, roots)
        # Drop symbols so widely used they say nothing about relevance — an
        # exported `formatDate` or a `Props` type reaches most of the codebase.
        spread_cap = max(10, int(len(files) * MAX_SYMBOL_SPREAD))
        referrers = {n: r for n, r in referrers.items() if len(r) <= spread_cap}

        weight = FANOUT[hop]
        # Hop 1 is unconditional: the direct neighbours of a seed are worth
        # reading whatever they are called. From hop 2 on, an ungated walk stops
        # being a search — it just enumerates the module graph — so a file only
        # qualifies if it mentions the keyword itself or is reached along an edge
        # whose symbol or path is on topic.
        gated = hop >= 1
        shares = defaultdict(list)
        for src in frontier:
            share = score[src] * weight
            for name in names_by_file.get(src, ()):
                on_topic = not gated or bool(topical.search(name))
                for ref in referrers.get(name, ()):
                    if ref != src and (on_topic or ref in direct):
                        shares[ref].append(share)
                        note_reason(ref, f"uses {name}")
            # Parent directory included: repos have many types.ts / index.ts, and
            # "imports types.ts" is useless when three of them exist.
            src_label = "/".join(src.split("/")[-2:])
            for target in all_imports.get(src, ()):
                rel = os.path.relpath(target, root)
                if not gated or topical.search(rel) or target in direct:
                    shares[target].append(share)
                    note_reason(target, f"imported by {src_label}")
            for importer in importers.get(src, ()):
                rel = os.path.relpath(importer, root)
                if not gated or topical.search(rel) or importer in direct:
                    shares[importer].append(share)
                    note_reason(importer, f"imports {src_label}")

        # Being reached from several seeds counts for something, but with heavy
        # diminishing returns — otherwise a shared util accumulates its way past
        # the files that actually mention the keyword.
        gains = {p: max(v) + 0.25 * (sum(v) - max(v)) for p, v in shares.items()}
        for path, gained in gains.items():
            score[path] += gained
            origin.setdefault(path, hop + 1)
        frontier = [p for p in sorted(gains, key=gains.get, reverse=True) if p not in seen][
            : args.seeds
        ]
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
        if is_barrel(path, names_by_file.get(path)):
            value *= BARREL_PENALTY
        ranked.append((value, path, test))
    ranked.sort(key=lambda r: (-r[0], r[1]))

    # The floor is per tier, and the deepest tier is exempt: each hop multiplies
    # scores by a fraction, so a file three hops out can never clear 1% of a
    # direct hit, and every deep result would vanish no matter what --depth said.
    tier_best = defaultdict(float)
    for value, path, _ in ranked:
        tier_best[min(origin.get(path, 0), 2)] = max(
            tier_best[min(origin.get(path, 0), 2)], value
        )
    ranked = [
        r
        for r in ranked
        if min(origin.get(r[1], 0), 2) == 2 or r[0] >= tier_best[min(origin.get(r[1], 0), 2)] * 0.01
    ]
    ranked = ranked[: min(args.n, HARD_LIMIT)]
    # Which files make the cut is decided by score; the order they are listed in
    # is the order to read them, tier first.
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
        print(
            f"{name} ({len(group)} {'file' if len(group) == 1 else 'files'}, "
            f"~{tier_lines:,} lines) — {blurb}"
        )
        for row in group:
            where = f"{row['file']}:{row['line']}" if row["line"] else row["file"]
            tag = " [test]" if row["test"] else ""
            print(f"  {row['rank']:>3}. {where:<{width}}  {', '.join(row['why'])}{tag}")
        print()


if __name__ == "__main__":
    main()
