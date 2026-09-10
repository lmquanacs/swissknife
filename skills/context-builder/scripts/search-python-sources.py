#!/usr/bin/env python3
"""search-python-sources.py — rank Python files to read for a keyword.

Usage: <skill-dir>/scripts/search-python-sources.py <keyword>... [root] [-n 200] [--json]

Requires tree-sitter:  pip install tree-sitter tree-sitter-python

The Python counterpart of search-java-sources.py, search-kotlin-sources.py and
search-ts-sources.py: same reading-list output, different notion of what a module
is. Files that mention the keyword become seeds — including fuzzily, so a
misspelled or abbreviated name still lands — and the ranking fans out along
top-level definition references *and resolved import modules*, because a Python
import names a module path, not a type. Past the first hop the fan-out is gated
on the keyword, so it stays a search instead of drifting into the whole module
graph. Prints at most 200 files, best first.

Every structural fact — what a file defines, what it imports, and whether it
subclasses, decorates with, calls or merely mentions a name — comes from a
tree-sitter parse, so a name in a comment or a docstring can never contribute an
edge and a declaration split across lines is still seen. Raw mention *frequency*
stays on ripgrep, because how often a word occurs in a file is a text question
and rg answers it an order of magnitude faster than a parse.

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

def _reexec_in_skill_venv():
    """Re-run under the skill's own venv, the one scripts/bootstrap.sh creates."""
    venv_python = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".venv", "bin", "python3"
    )
    if os.environ.get("CONTEXT_BUILDER_BOOTSTRAPPED") or not os.path.exists(venv_python):
        return  # already re-execed once, or there is no venv to re-exec into
    os.environ["CONTEXT_BUILDER_BOOTSTRAPPED"] = "1"  # one attempt, never a loop
    os.execv(venv_python, [venv_python, os.path.abspath(__file__), *sys.argv[1:]])


try:
    from tree_sitter import Language, Parser, Query
    import tree_sitter_python
except ImportError as exc:  # a hard dependency: there is no regex path any more
    _reexec_in_skill_venv()  # returns only when that did not resolve it
    sys.exit(
        f"error: {exc.name} is required by this script.\n"
        "  run this skill's scripts/bootstrap.sh once, or:\n"
        "  pip install tree-sitter tree-sitter-python\n"
        "Structural facts (definitions, imports, subclass/decorator edges)\n"
        "are read from a real parse; there is no regex fallback."
    )

try:  # tree-sitter >= 0.25 moved captures onto a cursor
    from tree_sitter import QueryCursor
except ImportError:  # 0.23/0.24 keep Query.captures, same dict-of-lists result
    QueryCursor = None

if hasattr(signal, "SIGPIPE"):  # so `| head` exits quietly instead of trapping
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)

HARD_LIMIT = 200  # never print more than this many files

# Build output, caches, virtualenvs and vendored code — never search results.
EXCLUDE_GLOBS = [
    "!**/.venv/**",
    "!**/venv/**",
    "!**/site-packages/**",
    "!**/__pycache__/**",
    "!**/.mypy_cache/**",
    "!**/.pytest_cache/**",
    "!**/.ruff_cache/**",
    "!**/.tox/**",
    "!**/.nox/**",
    "!**/node_modules/**",
    "!**/dist/**",
    "!**/build/**",
    "!**/.eggs/**",
    "!**/*.egg-info/**",
    "!**/migrations/**",
    "!**/_pb2.py",
    "!**/.git/**",
]
EXCLUDE_DIRS = {
    ".venv",
    "venv",
    "site-packages",
    "__pycache__",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".nox",
    "node_modules",
    "dist",
    "build",
    ".eggs",
    "migrations",
    ".git",
}
SOURCE_EXTS = (".py", ".pyi")
# Where source lives when a project bothers to separate it. Python has no
# enforced layout — a flat `mypackage/` at the repo root is as common as `src/`
# — so these are checked at any depth and find_source_roots falls back to the
# whole root when none of them exist, which is the normal case for a flat
# package. Test trees are included on purpose: they are demoted at ranking time
# and tagged [test], not hidden, because how a thing is called in a test is
# often the fastest explanation of what it does.
SOURCE_DIR_NAMES = ("src", "app", "lib", "tests", "test")

# Wiring that is not source: packaging metadata, env files, schemas, the tool
# config at the repo root. Not ranked or read, but a keyword landing in one is
# often the fastest orientation there is, so they get a trailer section.
CONFIG_GLOBS = [
    "pyproject.toml",
    "setup.cfg",
    "setup.py",
    "requirements*.txt",
    "*.toml",
    "*.yaml",
    "*.yml",
    "*.ini",
    "*.cfg",
    ".env*",
    "*.json",
    "*.sql",
]
CONFIG_EXCLUDE = EXCLUDE_GLOBS + [
    "!**/*.lock",
    "!**/*-lock.json",
]
CONFIG_LIMIT = 6

# One query per file, capture names doubling as the edge kind.
#
# Only *module-level* definitions count. A cross-file graph can only be built
# from names other files can import, and picking up locals means every `result`
# and `response` in the repo becomes an edge joining unrelated files. Python has
# no `export`, so being a direct child of `module` is what stands in for it.
# `@callee` is not an edge: it exists so an `import_module("x")` argument can be
# told apart from any other single-string call, which is resolved in Python below.
PY_QUERY = """
(module (class_definition name: (identifier) @decl))
(module (function_definition name: (identifier) @decl))
(module (decorated_definition (class_definition name: (identifier) @decl)))
(module (decorated_definition (function_definition name: (identifier) @decl)))
(module (expression_statement (assignment left: (identifier) @decl)))

(class_definition body: (block (function_definition name: (identifier) @member)))
(class_definition
  body: (block (decorated_definition (function_definition name: (identifier) @member))))
(class_definition
  body: (block (expression_statement (assignment left: (identifier) @member))))

(import_statement name: (dotted_name) @import)
(import_statement name: (aliased_import (dotted_name) @import))
(import_from_statement module_name: (dotted_name) @import)
(import_from_statement module_name: (relative_import) @import)
(call
  function: (identifier) @callee
  arguments: (argument_list (string (string_content) @callmod)))

(class_definition superclasses: (argument_list (identifier) @extends))
(class_definition superclasses: (argument_list (attribute attribute: (identifier) @extends)))
(decorator (identifier) @decorates)
(decorator (call function: (identifier) @decorates))
(decorator (attribute attribute: (identifier) @decorates))
(call function: (identifier) @call)
(call function: (attribute object: (identifier) @call))
(type (identifier) @mention)
"""

MAX_PARSE_BYTES = 2_000_000  # a generated 5MB bundle is not what anyone is looking for

# Score weights. Direct evidence is worth far more than fan-out, so a file the
# keyword actually names always outranks something merely adjacent to it.
W_STEM_EXACT = 60.0  # file is named exactly after the keyword
W_STEM_PART = 30.0  # keyword appears inside the file name
W_PATH = 12.0  # keyword appears in a directory segment
W_TYPE_DECL = 25.0  # defines a top-level symbol whose name contains the keyword
W_MEMBER_DECL = 8.0  # declares a member/prop whose name contains the keyword
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
MAX_SYMBOL_SPREAD = 0.2  # a symbol used across more of the repo than this is noise
MAX_SYMBOL_FILES = 50  # ...and never more than this many, however large the repo is
FANOUT = [0.35, 0.12, 0.05, 0.02, 0.01]  # share of a file's score passed on per hop
TEST_PENALTY = 0.3
BARREL_PENALTY = 0.35  # an index.ts that only re-exports teaches you nothing

# Not every mention of a symbol is the same kind of evidence. Subclassing it or
# implementing its interface is a structural commitment, constructing/calling or
# rendering it is real use, and a bare type mention may be a parameter type — so
# the fan-out share is scaled by which one it is.
EDGE_WEIGHT = {
    "extends": 1.6,
    "decorates": 1.3,
    "call": 1.15,
    "mention": 1.0,
}
EDGE_VERB = {
    "extends": "subclasses",
    "decorates": "decorated by",
    "call": "calls",
    "mention": "uses",
}

# Output tiers, indexed by how many hops from a keyword match a file was found.
TIERS = [
    ("READ FIRST", "the keyword is named or declared here"),
    ("THEN", "imported by / imports the files above"),
    ("SKIM IF NEEDED", "further out, reached through an on-topic module"),
]

TEST_PATH = re.compile(r"(^|/)(tests?|testing|e2e)(/|$)")
TEST_STEM = re.compile(r"^test_|_test$|^conftest$")

EVIDENCE_WIDTH = 96  # a source line is evidence, not a paragraph
# True, and tells you nothing: the import that pulled the name in, and the tail
# of a multi-line one.
BORING_LINE = re.compile(r"^\s*(?:import\b|from\b[\w. ]+\bimport\b)")
CACHE_VERSION = 3  # bumped: the cache now holds a full structural index


def die(msg):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(1)


def rg(args, roots):
    """Run ripgrep over roots, returning stdout ('' when nothing matched)."""
    # One --type-add per glob: the comma form is accepted but matches nothing.
    cmd = ["rg", "--no-messages"]
    for ext in SOURCE_EXTS:
        cmd += ["--type-add", f"pysrc:*{ext}"]
    cmd += ["-t", "pysrc"]
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
    since Python file names use it constantly.
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
    """Per-file match counts for a pattern.

    Text frequency, deliberately including comments and string literals: a
    keyword in a log message or a JSDoc block is still a sign the file is about
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
    already carry their own exact positions from the parse.
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
        # import/export boilerplate wins.
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
    return os.path.join(base, "context-builder", f"py-{key}.json")


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


def enclosing_call(node):
    """Walk up from a string argument to the call_expression wrapping it."""
    while node is not None and node.type != "call":
        node = node.parent
    return node


def build_index(roots, files, use_cache=True):
    """Parse every source once and record what it defines, imports and references.

    Returns a dict of four maps plus a note:

      decls[file]   -> [[name, line, source_line], ...]  top-level, in file order
      members[file] -> [name, ...]                       methods, props, signatures
      imports[file] -> [specifier, ...]                  raw, resolved later
      refs[file]    -> {name: edge kind}                 strongest edge per name

    `refs` is filtered down to names that something in this tree actually
    defines, because a reference to `Optional` or `dataclass` is not a cross-file
    edge and keeping it would bloat both the cache and the fan-out. The whole
    thing is cached against the tree's newest mtime, since a research session
    runs this script several times with different keywords and the parse never
    changes.
    """
    cached = read_cache(roots, files) if use_cache else None
    if cached is not None:
        return cached, None

    language = Language(tree_sitter_python.language())
    parser, query = Parser(language), Query(language, PY_QUERY)

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

        specs = {text_of(n) for n in found.get("import", ())}
        # `import_module("x")` looks like any other one-string call to the
        # grammar, so the callee is checked here rather than with a query
        # predicate — those are not applied uniformly across binding versions.
        for node in found.get("callmod", ()):
            call = enclosing_call(node)
            if call is None:
                continue
            callee = call.child_by_field_name("function")
            if callee is not None and text_of(callee) in ("import_module", "__import__"):
                specs.add(text_of(node))
        if specs:
            imports[path] = sorted(specs)

        edges = {}
        for kind in ("extends", "decorates", "call", "mention"):
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

    Names referenced across more of the repo than the cap are dropped: an
    module-level `logger` or a `Base` class reaches most of the codebase and says
    nothing about relevance, and letting it fan out drowns the files that do. The
    percentage alone is not enough on a large repo — 20% of 2,000 files is 400 —
    so an absolute ceiling is what actually keeps infrastructure symbols out.
    """
    out = defaultdict(dict)
    for path, edges in index["refs"].items():
        for name, kind in edges.items():
            out[name][path] = kind
    cap = max(10, min(MAX_SYMBOL_FILES, int(file_count * MAX_SYMBOL_SPREAD)))
    return {n: r for n, r in out.items() if len(r) <= cap}


def module_index(files):
    """Index every path suffix -> files, for resolving absolute imports.

    `app.services.auth` has to land on `src/app/services/auth.py` without
    reading pyproject or a PYTHONPATH setting, so every suffix of every
    candidate is indexed and the longest match wins.
    """
    index = defaultdict(set)
    for path in files:
        stripped = re.sub(r"\.pyi?$", "", path)
        for candidate in (stripped, re.sub(r"/__init__$", "", stripped)):
            segments = candidate.split("/")
            for i in range(1, min(len(segments), 5) + 1):
                index["/".join(segments[-i:])].add(path)
    return index


def resolve_module(spec, importer, index, candidates):
    """Resolve one import specifier to files in this repo (empty for externals)."""
    if not spec:
        return set()
    if spec.startswith("."):
        # `from . import x` is the importer's own package; every dot past the
        # first climbs one directory. `from ..lib.auth import x` arrives here
        # as `..lib.auth`, so the dots are counted before the rest is split.
        dots = len(spec) - len(spec.lstrip("."))
        base = os.path.dirname(importer)
        for _ in range(dots - 1):
            base = os.path.dirname(base)
        rest = spec[dots:].replace(".", "/")
        target = os.path.normpath(os.path.join(base, rest)) if rest else base
        hits = set()
        for ext in SOURCE_EXTS:
            for candidate in (target + ext, os.path.join(target, "__init__" + ext)):
                if candidate in candidates:
                    hits.add(candidate)
        return hits
    # Absolute: `app.services.auth` -> the longest path suffix present in this
    # tree. A third-party module simply matches nothing, which is the point.
    segments = spec.replace(".", "/").split("/")
    for i in range(len(segments), 0, -1):
        hit = index.get("/".join(segments[-i:]))
        if hit:
            return set(hit)
    return set()


def resolve_imports(index, module_idx, candidates):
    """Map file -> the repo files it imports, resolved through the module graph."""
    edges = defaultdict(set)
    for path, specs in index["imports"].items():
        for spec in specs:
            edges[path] |= resolve_module(spec, path, module_idx, candidates) - {path}
    return edges


def config_mentions(patterns, root):
    """Keyword hits in manifests, env files and schemas — wiring, not code."""
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
    move with a module. Commits touching more than `max_files` files are ignored —
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
    """`auth_service.py` and `auth_service.pyi` both stem to what they are about."""
    name = os.path.basename(path)
    return name[: name.index(".")] if "." in name else name


def is_test(path):
    name = os.path.splitext(os.path.basename(path))[0]
    return bool(TEST_PATH.search(path)) or bool(TEST_STEM.search(name))


def is_barrel(path, declared):
    """An __init__ that defines nothing of its own is a re-export hub."""
    return stem_of(path) == "__init__" and not declared


def test_partners(files):
    """Map a source file -> the test files written against it.

    A module's test is the best documentation it has, so it belongs next to its
    subject in the list rather than several tiers below it on its own. Both
    Both layouts collapse to the same rule once the test affixes are stripped:
    `test_auth.py` and `tests/auth.py` are both about `auth`.
    """
    by_stem = defaultdict(list)
    for path in files:
        if not is_test(path):
            by_stem[stem_of(path)].append(path)
    partners = defaultdict(list)
    for path in files:
        stem = stem_of(path)
        if not is_test(path) or stem == "__init__":  # every __init__.py shares that
            continue
        for subject in by_stem.get(stem, ()):
            partners[subject].append(path)
    return partners


def score_keyword(keyword, files, roots, root, index, files_by_name, referrers, min_fuzzy):
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
    # own primary export, then its first textual mention of the keyword, then —
    # best of all — the export whose name actually matches.
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
        segments = os.path.dirname(os.path.relpath(path, root)).split("/")
        if any(partial.search(seg) for seg in segments):
            score[path] += W_PATH
            why[path].append("keyword in directory path")
        # Exports and members come from the parse, so a match here is a real
        # declaration — never a mention of the word in a comment above one.
        matching_types = [n for n, _, _ in index["decls"].get(path, ()) if partial.search(n)]
        if matching_types:
            score[path] += W_TYPE_DECL
            why[path].append(f"defines {matching_types[0]}")
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

    # Fuzzy pass, against the repo's own vocabulary — file names and exported
    # symbols — rather than raw text, because that is where a misspelling or an
    # abbreviation is recoverable. Discounted by similarity, so it always sits
    # below a real match.
    vocabulary = {stem_of(p) for p in files} | set(files_by_name)
    ranked_vocab = sorted(
        ((similarity(keyword, term), term) for term in vocabulary if not partial.search(term)),
        reverse=True,
    )
    fuzzy = dict((t, s) for s, t in ranked_vocab if s >= min_fuzzy) if min_fuzzy > 0 else {}
    fuzzy = dict(sorted(fuzzy.items(), key=lambda kv: -kv[1])[:MAX_FUZZY_TERMS])

    exact_hit = {p for p, s in score.items() if s > 0}
    carrier = set()
    for term, sim in fuzzy.items():
        weight = sim * FUZZY_DISCOUNT
        # Where the term lives, plus everything referencing it. The first half
        # matters on its own: a term recovered from a file stem is not always an
        # identifier anything references, and the file it names is the answer.
        touching = set(referrers.get(term, {}))
        touching |= set(files_by_name.get(term, ()))
        touching |= {p for p in files if stem_of(p) == term}
        for path in touching:
            entries = index["decls"].get(path, ())
            gain = W_FUZZY_MENTION
            if stem_of(path) == term:
                gain += W_STEM_PART
                carrier.add(path)
            if any(n == term for n, _, _ in entries):
                gain += W_TYPE_DECL
                # Only a file's *primary* export makes it the thing itself. One
                # of forty re-exported symbols is evidence the file is a
                # collaborator, not the answer, so it scores but stays out of
                # the top tier.
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
        description="Rank Python files to read for a keyword.",
        epilog="Example: search-python-sources.py retry_policy ~/work/service",
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
    ap.add_argument("--no-tests", action="store_true", help="drop test files entirely")
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
        die(f"no .py files under {', '.join(roots)}")
    # `app`, `lib` and `src` are common directory names outside Python too, and
    # a repo can carry one with no .py in it. Keep only roots holding sources.
    roots = [r for r in roots if any(f.startswith(r + "/") for f in files)]
    candidates = set(files)
    if from_file and from_file not in candidates:
        files.append(from_file)  # seeding from outside the source dirs is allowed
        candidates.add(from_file)

    # One capped label, used by both the success banner and the no-match message:
    # a monorepo has hundreds of source dirs and printing them all buries the
    # sentence the reader actually needs.
    rel_names = [os.path.relpath(r, root) for r in roots]
    rel_roots = ", ".join(
        rel_names[:4] + ([f"(+{len(rel_names) - 4} more)"] if len(rel_names) > 4 else [])
    )
    index, index_note = build_index(roots, files, use_cache=not args.no_cache)
    if index_note:
        print(f"note: {index_note}", file=sys.stderr)
    files_by_name = defaultdict(set)
    for path, entries in index["decls"].items():
        for name, _, _ in entries:
            files_by_name[name].add(path)
    referrers = invert_refs(index, len(files))
    module_idx = module_index(files)
    all_imports = resolve_imports(index, module_idx, candidates)

    passes = [
        score_keyword(kw, files, roots, root, index, files_by_name, referrers, args.fuzzy)
        for kw in keywords
    ]

    # Combine the keywords. The union just adds up; --all keeps only files that
    # every keyword found and scores them by the weakest one, so "the file where
    # auth and retry meet" beats "the file that says auth forty times".
    score = defaultdict(float)
    why = defaultdict(list)
    mentions = defaultdict(int)
    # With no keyword at all (--from-file on its own) nothing has anchored the
    # results yet, so fall back to each file's own exported declaration.
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

    origin = {p: 0 for p in direct}
    for path in direct:
        if path not in exact_hit and path not in carrier:
            origin[path] = 1

    def note_reason(path, reason):
        if reason not in why[path] and len(why[path]) < 3:
            why[path].append(reason)

    # Fan out along two kinds of edge: files that reference a seed's exported
    # symbols, and the module graph in both directions (what a seed imports, and
    # who imports the seed). Each hop passes on a fraction of the score.
    importers = defaultdict(set)
    for src, targets in all_imports.items():
        for target in targets:
            importers[target].add(src)

    frontier = sorted(direct, key=direct.get, reverse=True)[: args.seeds]
    seeds = list(frontier)
    seen = set(frontier)
    for hop in range(args.depth):
        if not frontier:
            break
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
            for name, _, _ in index["decls"].get(src, ()):
                on_topic = not gated or bool(topical.search(name))
                for ref, kind in referrers.get(name, {}).items():
                    if ref != src and (on_topic or ref in direct):
                        shares[ref].append(share * EDGE_WEIGHT[kind])
                        note_reason(ref, f"{EDGE_VERB[kind]} {name}")
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
        if is_barrel(path, index["decls"].get(path)):
            value *= BARREL_PENALTY
        ranked.append((value, path, test))
    ranked.sort(key=lambda r: (-r[0], r[1]))

    # The floor is per tier, and the deepest tier is exempt: each hop multiplies
    # scores by a fraction, so a file three hops out can never clear 1% of a
    # direct hit, and every deep result would vanish no matter what --depth said.
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
    # is the order to read them, tier first.
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
                "defines": [n for n, _, _ in index["decls"].get(path, ())],
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
