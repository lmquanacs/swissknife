---
name: code-researcher
description: Investigate unfamiliar or large codebases fast using ripgrep (rg), fd, ast-grep, and jq, saving reusable searches as scripts in a local .scripts/ folder. Use when locating where a symbol is defined or used, tracing call sites and data flow, auditing a pattern across many files, finding recently-changed files, comparing how something is done in two places, or answering "where is X" / "how does Y work" in a repo too big to read.
---

# Code Researcher

A workflow for answering questions about a codebase with search tools instead of
reading files at random. The goal is always to **narrow to a small set of files,
then read those files properly.**

## Pick the right tool

| Question | Tool |
|---|---|
| Where does this *text* appear? | `rg` |
| What files *exist* with this name/extension/age? | `fd` |
| Where does this *code shape* appear (calls, defs, JSX, imports)? | `ast-grep` |
| Something emitted JSON | `jq` |
| I have <10 candidate files and need to understand them | the `Read` tool |

Rule of thumb: **`rg` for text, `ast-grep` for structure.** Reach for `ast-grep`
the moment a regex would need to care about whitespace, line breaks, nesting, or
balanced parens — those are exactly the cases regex gets wrong.

## Method

1. **Cast wide, cheaply.** `rg -l` / `rg -c` to see *which* and *how many* files
   are involved before printing any match bodies. A search that returns 400 hits
   is a signal to narrow, not to read 400 hits.
2. **Narrow** with type filters (`-t ts`), globs (`-g`), and word boundaries (`-w`).
3. **Confirm structurally** with `ast-grep` if the pattern is code-shaped.
4. **Read** the surviving handful of files with the `Read` tool.
5. **Save anything you'll run again** into `.scripts/` (see below) instead of
   retyping it on the next pass.

Report findings as `path/to/file.ts:42` — those are clickable.

## Work through `.scripts/`, don't re-type pipelines

Investigation is repetitive: the same search gets re-run with a tweaked pattern, a
wider glob, a different directory. Retyping a long pipeline each time burns turns
and quietly introduces typos that silently change the result.

**Rule: the moment a command is worth running a second time, it belongs in a script.**

- Every script goes in a `.scripts/` directory at the root of the current working
  directory. **Create it yourself when it doesn't exist** (`mkdir -p .scripts`) —
  don't ask, don't fall back to running inline.
- **Check `.scripts/` before writing a new one.** The script you need may already
  be there; extend it rather than adding a near-duplicate. `ls .scripts/` is the
  fastest check — and note that `rg --files` and `fd` do **not** list `.scripts/`
  by default, because a leading dot makes it a hidden directory. Use `ls`, or
  `fd -H`. (The upside: your tooling never pollutes your own search results.)
- **Shell (`.sh`)** for pipelines of rg/fd/ast-grep/jq. **Python (`.py`)** when you
  need data structures, counting/grouping, or JSON reshaping past the point where
  jq stays readable.
- **Parameterize.** A script with the search term hardcoded gets thrown away and
  rewritten next time; one that takes `$1` gets reused. Default the path argument
  to `.` so the common case stays a one-word invocation.
- `chmod +x` it and give it a shebang, so it runs as `./.scripts/name.sh`.
- Open with a two-line comment: what it does, and the usage line.
- Name them verb-first: `find-callers.sh`, `audit-imports.py`, `list-todos.sh`.

Whether `.scripts/` gets committed is the repo owner's call — add it to
`.gitignore` if it's personal scratch tooling, commit it if the team benefits.

Shell template:

```bash
#!/usr/bin/env bash
# find-callers.sh — list call sites of a symbol, tests excluded.
# Usage: .scripts/find-callers.sh <symbol> [path]
set -euo pipefail
symbol="${1:?usage: find-callers.sh <symbol> [path]}"
rg -nw "$symbol" "${2:-.}" -g '!**/*.test.*'
```

Python template — reaches for structure that a shell pipeline handles badly:

```python
#!/usr/bin/env python3
"""audit-imports.py — count how often each module is imported.
Usage: .scripts/audit-imports.py [path]
"""
import collections, json, subprocess, sys

path = sys.argv[1] if len(sys.argv) > 1 else "."
out = subprocess.run(
    ["ast-grep", "run", "-p", "import $$$ from '$SRC'", "-l", "ts", "--json", path],
    capture_output=True, text=True, check=True,
).stdout
counts = collections.Counter(
    m["metaVariables"]["single"]["SRC"]["text"] for m in json.loads(out)
)
for src, n in counts.most_common(20):
    print(f"{n:5d}  {src}")
```

Captured metavariables live at `metaVariables.single.$NAME.text` for `$VAR` and
under `metaVariables.multi` for `$$$VAR`.

## ripgrep (`rg`)

```bash
rg -n 'pattern'                   # line numbers (default when piped to a terminal)
rg -l 'pattern'                   # filenames only — best first move
rg -c 'pattern'                   # count per file — gauge blast radius
rg -w 'send'                      # whole word: excludes resend/sender/sends
rg -F 'a.b[0]'                    # literal, no regex escaping needed
rg -t py -t js 'pattern'          # only these languages (rg --type-list to see all)
rg -g '!**/*.test.ts' 'pattern'   # exclude a glob (note the leading !)
rg -A 5 -B 2 'pattern'            # context after/before
rg -U 'foo\s*\{[^}]*bar'          # multiline mode
rg -P 'foo(?=\()'                 # PCRE2: lookarounds/backrefs (this build has +pcre2)
rg --files-without-match 'license' # inverse: files LACKING the pattern
rg -o '"version":\s*"[^"]+"'      # print just the matched part
rg --files                        # list every file rg would search (respects .gitignore)
```

`rg` skips `.gitignore`d files and hidden files by default. Add `-u` (ignore
`.gitignore`), `-uu` (also hidden), `-uuu` (also binary) when a file is
suspiciously missing from results — this is the #1 cause of "but I know it's there".

## fd

```bash
fd auth                       # fuzzy-ish filename match, respects .gitignore
fd -e ts -e tsx               # by extension
fd -t f / -t d / -t l         # files / dirs / symlinks
fd -H -I                      # include hidden and ignored files
fd --changed-within 2d        # touched in last 2 days — great for "what did I just break"
fd --changed-before 1y        # stale code
fd -g '**/*.config.*'         # explicit glob
fd -e ts -x wc -l {}          # run a command per result
```

`fd --changed-within` is the fastest way to orient in a repo someone else was
just working in.

## ast-grep

Structural search. `$VAR` matches one node, `$$$ARGS` matches zero or more.
Patterns match the parsed tree, so formatting and line breaks are irrelevant.

```bash
ast-grep run -p 'console.log($$$ARGS)' -l ts        # every call, even multi-line ones
ast-grep run -p 'function $NAME($$$) { $$$ }' -l js # all function declarations
ast-grep run -p 'await $EXPR' -l ts                 # every await site
ast-grep run -p 'useEffect($$$)' -l tsx             # hook usage
ast-grep run -p 'catch ($E) { }' -l ts              # empty catch blocks (real bug smell)
ast-grep run -p '$OBJ.query($$$)' -l ts             # method calls on any receiver
```

Rewriting (verify without `-U` first, then apply):

```bash
ast-grep run -p 'foo($A, $B)' --rewrite 'foo($B, $A)' -l ts      # preview a diff
ast-grep run -p 'foo($A, $B)' --rewrite 'foo($B, $A)' -l ts -U   # write to disk
```

**`-l` is required** for most invocations — ast-grep needs to know the grammar.
Common values: `ts`, `tsx`, `js`, `jsx`, `py`, `go`, `rs`, `java`, `c`, `cpp`.

**Gotcha: `--json` line numbers are 0-indexed**, while the default human-readable
output is 1-indexed. Add 1 before reporting a line number sourced from JSON.

**The command is `ast-grep`, not `sg`.** `sg` still works but prints a deprecation
warning on every run, which pollutes output.

## jq

```bash
jq -r '.scripts | keys[]' package.json         # what commands exist
jq -r '.dependencies | to_entries[] | "\(.key) \(.value)"' package.json
jq '.[] | select(.level == "error")' logs.json # filter
jq -r '.. | .name? // empty' big.json          # recursive descent for a key
jq -s 'length' *.json                          # slurp multiple files
```

`-r` gives raw strings (no quotes) — almost always what you want when piping onward.

## Combinations that do real work

```bash
# Which files reference this symbol, deduped and sorted
rg -lw 'AuthService' | sort

# Structural search -> just the file list (remember: +1 for 1-indexed lines)
ast-grep run -p 'fetch($$$)' -l ts --json | jq -r '.[] | "\(.file):\(.range.start.line + 1)"'

# Search only files changed in the last day
fd --changed-within 1d -e ts -x rg -l 'TODO' {}

# Count matches per file, busiest first
rg -c 'deprecated' | sort -t: -k2 -rn | head

# Find the definition, not the 200 usages
rg -n '(function|const|class|interface|type)\s+MyThing\b'
```

## Don't hang the terminal

Read files with the `Read` tool, not a pager-backed command. Anything that may
page or wait for input needs that disabled explicitly — `git --no-pager diff`,
`git log | cat` — or it blocks forever.
