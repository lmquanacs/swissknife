# Tool Cookbook

Flags, recipes, and failure modes for the search tools Phase 2 runs on. Read the
section you need, not the whole file.

**Contents**
- Saving reusable searches in `.scripts/`
- `tree` — orientation
- `rg` — text search
- `fd` — file discovery
- `ast-grep` — structural search (**start at "the four gotchas"** when a pattern
  returns nothing)
- Semgrep — the escalation from `ast-grep`, for dataflow and taint
- `jq` — JSON
- `yq` — YAML
- Combinations that do real work
- Availability and fallbacks

---

## Work through `.scripts/`, don't re-type pipelines

Investigation is repetitive: the same search gets re-run with a tweaked pattern, a
wider glob, a different directory. Retyping a long pipeline each time burns turns
and introduces typos that silently change the result.

**Rule: the moment a command is worth running a second time, it belongs in a script.**

- Every script goes in a `.scripts/` directory at the root of the current working
  directory. **Create it yourself when it doesn't exist** (`mkdir -p .scripts`) —
  don't ask, don't fall back to running inline.
- **Don't re-create what ships with this skill.**
  `${CLAUDE_SKILL_DIR}/scripts/search-java-sources.py` and its Kotlin, Python
  and TS twins already cover "which files should I read for X" in Java, Kotlin,
  Python and TS/JS repos — call them, don't write a smaller version into
  `.scripts/`.
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

## tree

Orientation before search, not a substitute for it. Limit depth and exclude
generated/dependency/VCS directories or the output is useless noise.

```bash
tree -L 2 -I '.git|node_modules|build|dist|target|.gradle|.idea'   # shallow first pass
tree -L 3 src                                                      # deeper, scoped to one dir
tree -d -L 2                                                       # directories only
```

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
ast-grep run -p 'console.log($$$ARGS)' -l ts   # every call, even multi-line ones
ast-grep run -p 'await $EXPR' -l ts            # every await site
ast-grep run -p '$OBJ.query($$$)' -l ts        # method calls on any receiver
ast-grep run -k function_declaration -l ts     # match by node kind, no pattern
```

**`-l` is required** for most invocations — ast-grep needs to know the grammar.
Values used here: `ts`, `tsx`, `js`, `jsx`, `py`, `kotlin` (alias `kt`), `go`,
`rs`, `java`, `c`, `cpp`.

### The four gotchas that waste the most time

1. **`-l` sets the grammar, not which files get read.** File selection is still by
   extension, so `-l tsx` over a `.ts` file matches *nothing* — silently, with exit
   status 1 and no error. If a search comes back empty, check this first: use
   `-l ts` for `.ts`, `-l tsx` for `.tsx`, and run both when a codebase mixes them.
   (`-l kotlin` covers both `.kt` and `.kts`.)
2. **Modifiers and return types are part of the signature node.** A pattern is not
   a prefix match — `def $N($$$):` will not match `def f() -> None:`, and
   `fun $N($$$) { $$$ }` will not match `suspend fun f(): Result<T> { }`. See the
   per-language notes below; the general escape hatch is `-k <kind>`.
3. **`-k` and `-p` are mutually exclusive** (`the argument '--kind' cannot be used
   with '--pattern'`). To constrain a pattern by kind or by surrounding context,
   use an inline rule instead — see *Inline rules* below.
4. **`--json` line numbers are 0-indexed**, while the default human-readable output
   is 1-indexed. Add 1 before reporting a line number sourced from JSON.

**The command is `ast-grep`, not `sg`.** `sg` still works but prints a deprecation
warning on every run, which pollutes output.

When a pattern returns nothing and you can't see why, ask ast-grep how it parsed
the *pattern* — `--debug-query=ast` prints the tree. `ERROR` nodes in that output
mean the fragment isn't valid standalone code in that grammar (a bare `catch (e) {}`
in Kotlin, for example, parses as a function call, not a catch clause), so it can
never match anything. Widen the pattern until it parses.

### TypeScript / TSX

```bash
ast-grep run -p 'useEffect($FN, [$$$DEPS])' -l tsx   # hooks WITH a dep array
ast-grep run -p 'try { $$$ } catch ($E) {}' -l ts    # swallowed errors
ast-grep run -p '$E as any' -l ts                    # type-safety escape hatches
ast-grep run -p 'const $N = ($$$) => { $$$ }' -l ts  # arrow fns, incl. `export const`
ast-grep run -p 'fetch($$$)' -l ts                   # every network call site
ast-grep run -k interface_declaration -l ts          # all interfaces
```

- `export` is a wrapper node, so patterns for the *inner* declaration match
  exported ones too: `const $N = ($$$) => { $$$ }` finds `export const Widget = …`.
  Don't write `export` into the pattern unless you specifically want only exports.
- `async` and the return type are **not** free: `async function $N($$$) { $$$ }`
  misses `async function f(): Promise<T> {}`. Add the return type as a metavariable
  (`async function $N($$$): $R { $$$ }`) or use `-k function_declaration`.
- `useEffect($$$)` finds every call; `useEffect($FN, [$$$DEPS])` finds only those
  with a dependency array — the diff between the two is your missing-deps list.

### Python

```bash
ast-grep run -k function_definition -l py            # every def, sync + async
ast-grep run -p 'async def $N($$$) -> $R:
    $$$' -l py                                       # typed async defs
ast-grep run -p 'try:
    $$$
except $E:
    pass' -l py                                      # silently swallowed exceptions
ast-grep run -p '@$DEC
def $N($$$): $$$' -l py                              # decorated functions (route handlers)
ast-grep run -p 'self.$ATTR.$M($$$)' -l py           # calls through an instance attribute
ast-grep run -k class_definition -l py               # all classes
```

- Patterns are **indentation-sensitive** — the body goes on its own line, indented,
  exactly as Python source would be. Single-line `def $N($$$): $$$` only matches
  genuinely single-line defs.
- The return annotation is structural but `async` is not: `def $N($$$) -> $R:` also
  matches `async def f() -> dict:`, while plain `def $N($$$):` matches *neither*
  annotated nor async defs. For "every function regardless of shape", use
  `-k function_definition`.
- `$DEC` on a decorator captures the whole expression (`app.route("/users")`), which
  is what you want when auditing routes or fixtures.

### Kotlin

```bash
ast-grep run -k function_declaration -l kotlin           # every fun, all modifiers
ast-grep run -p 'suspend fun $N($$$): $R { $$$ }' -l kotlin
ast-grep run -p '$SCOPE.launch { $$$ }' -l kotlin        # coroutine launch sites
ast-grep run -p '$X!!' -l kotlin                         # not-null assertions (crash risk)
ast-grep run -p 'try { $$$ } catch ($E: $T) { }' -l kotlin  # empty catch
ast-grep run -p 'data class $N($$$)' -l kotlin
ast-grep run -p '@Composable fun $N($$$) { $$$ }' -l kotlin
ast-grep run -p 'runCatching { $$$ }' -l kotlin
ast-grep run -p '$A ?: $B' -l kotlin                     # elvis / default-value sites
```

- Kotlin is the **strictest of the three about signatures**. `fun $N($$$) { $$$ }`
  matches only a plain block-bodied fun with no return type. It misses
  `suspend fun`, `fun f(): T`, and expression bodies — `fun f() = expr` needs
  `fun $N($$$) = $EXPR`. Writing one pattern per shape is a losing game; reach for
  `-k function_declaration` and filter the results, or use an inline rule.
- **A `catch` clause does not parse on its own** — write the whole `try { $$$ } catch
  (...) { }`. A bare `catch ($E: $T) { }` pattern parses as a call to a function named
  `catch` and matches nothing.
- Trailing-lambda calls match as written: `$SCOPE.launch { $$$ }` catches
  `viewModelScope.launch { … }`, `lifecycleScope.launch { … }`, and friends in one go.

### Inline rules — kind + pattern + context

When `-p` alone is too blunt and `-k` alone too broad, `ast-grep scan --inline-rules`
takes a YAML rule that composes them, plus relational operators (`inside`, `has`,
`follows`, with `stopBy: end` to search transitively).

```bash
# Kotlin: only suspend functions
ast-grep scan --inline-rules '
id: suspend-funs
language: kotlin
rule:
  kind: function_declaration
  has: { kind: modifiers, regex: suspend }
' .

# Python: method calls that occur inside an async def
ast-grep scan --inline-rules '
id: calls-in-async
language: python
rule:
  pattern: $C.$M($$$)
  inside:
    kind: function_definition
    stopBy: end
    has: { regex: "^async$", stopBy: end }
' .
```

### Rewriting

Verify without `-U` first (prints a diff), then apply:

```bash
ast-grep run -p 'foo($A, $B)' --rewrite 'foo($B, $A)' -l ts      # preview a diff
ast-grep run -p 'foo($A, $B)' --rewrite 'foo($B, $A)' -l ts -U   # write to disk
```

Metavariables carry into the rewrite by name, so a capture you didn't reuse is
silently dropped — read the previewed diff, don't skim the match count.

## Semgrep — the escalation from ast-grep

`ast-grep` answers *where does this shape appear*. Semgrep answers *does this
value reach that call* — dataflow, which `ast-grep` has none of. Use it for flow
questions only; it costs seconds to minutes per run against `ast-grep`'s
milliseconds. `brew install semgrep`.

```bash
semgrep --version                        # confirm it's here before planning around it
semgrep -e '$X.execute($Q)' -l kotlin .  # one-off pattern, no rule file
semgrep --config rules.yaml src/ --json  # local rules, scoped to a path
semgrep --config p/security-audit . --metrics off   # registry ruleset
```

Syntax overlaps `ast-grep` but isn't the same. Metavariables are `$X` in both;
the wildcard is `...`, not `$$$`. `...` also spans *statements*, which is the
part `ast-grep` can't express:

```
foo(...)                         # any arguments, ast-grep's foo($$$)
$X = source(); ...; sink($X)     # anything in between, same function body
<... $X ...>                     # $X anywhere inside this expression, any depth
```

### Taint mode — the actual reason to reach for it

```yaml
# taint.yaml — untrusted request data reaching a raw query
rules:
  - id: request-to-raw-query
    languages: [kotlin]
    severity: WARNING
    message: request value reaches execute() unsanitized
    mode: taint
    pattern-sources:
      - pattern: $REQ.queryParam(...)
    pattern-sanitizers:
      - pattern: sanitize(...)
    pattern-sinks:
      - pattern: $DB.execute(...)
```

```bash
semgrep --config taint.yaml src/ --json --metrics off \
  | jq -r '.results[] | "\(.path):\(.start.line) \(.extra.message)"'
```

That emits `path:line` anchors in the pack's format — a taint finding drops
into Findings unedited.

Three limits, before you trust a clean run:

- **Open-source Semgrep is single-file.** Taint doesn't cross files (cross-file
  is paid). "No findings" means "none within any one file", not "the flow is
  safe" — report which one you mean.
- **Registry configs (`p/…`, `r/…`, `--config auto`) hit the network** and send
  metrics. Use a local rule file or pass `--metrics off`.
- **Scope it to a path while iterating.** Repo root is for the final run.

`--json` to pipe, `--sarif` if something downstream eats it, `--severity ERROR`
to cut noise. Semgrep's `--json` line numbers are **1-indexed**, unlike
`ast-grep --json` — don't apply that +1 twice.

## jq

```bash
jq -r '.scripts | keys[]' package.json         # what commands exist
jq -r '.dependencies | to_entries[] | "\(.key) \(.value)"' package.json
jq '.[] | select(.level == "error")' logs.json # filter
jq -r '.. | .name? // empty' big.json          # recursive descent for a key
jq -s 'length' *.json                          # slurp multiple files
```

`-r` gives raw strings (no quotes) — almost always what you want when piping onward.

## yq

Same idea as `jq`, for YAML — config files, CI pipelines, docker-compose. Don't
regex your way through indentation-sensitive YAML.

```bash
yq '.services' docker-compose.yml
yq '.jobs | keys' .github/workflows/ci.yml
yq -o=json '.' config.yaml | jq '.database'   # convert to JSON mid-pipeline for jq
```

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

# Same repo, mixed .ts and .tsx — run both grammars, merge the results
for l in ts tsx; do ast-grep run -p 'useEffect($$$)' -l $l --json=compact; done | jq -s 'add'

# Every not-null assertion in Kotlin, busiest file first
ast-grep run -p '$X!!' -l kotlin --json | jq -r '.[].file' | sort | uniq -c | sort -rn
```


## Availability and fallbacks

Check before assuming: `command -v tree fd rg ast-grep jq yq`. If a tool is
missing, say so before falling back — don't silently degrade to a noisier
command.

| Preferred | If missing, state that, then |
|---|---|
| `tree` | `fd -t d -d 2 . \| head -50`, or `ls` one level at a time |
| `fd` | `find` with an explicit `-path` prune for `node_modules`/`.git` |
| `rg` | `grep -rn --exclude-dir={.git,node_modules,dist,build}` |
| `ast-grep` | `rg` to locate matches, then edit each site individually — never a blind `sed -i` across files |
| `jq` | `node -e` / `python3 -c` to parse the JSON |
| `yq` | `python3 -c 'import yaml,sys;...'`, or `rg -n -A3` for a quick peek |

**Semgrep is not in that table on purpose.** It's an escalation, so its absence
isn't a fallback situation. Check `command -v semgrep` only once a flow question
comes up; if it's missing, log that question as open. Regex is not a fallback
for dataflow.
