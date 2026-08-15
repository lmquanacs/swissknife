---
name: code-researcher
description: Investigate unfamiliar or large codebases fast using tree, ripgrep (rg), fd, ast-grep, jq, and yq, saving reusable searches as scripts in a local .scripts/ folder. Includes bundled scripts that turn a keyword into a ranked reading list for Java/Kotlin and TypeScript/JavaScript repos. Use when locating where a symbol is defined or used, tracing call sites and data flow, auditing a pattern across many files, finding recently-changed files, comparing how something is done in two places, deciding which files to read first in a Gradle/Maven or TypeScript project, or answering "where is X" / "how does Y work" in a repo too big to read.
---

# Code Researcher

A workflow for answering questions about a codebase with search tools instead of
reading files at random. The goal is always to **narrow to a small set of files,
then read those files properly.**

## Pick the right tool

| Question | Tool |
|---|---|
| What does this repo/directory look like at a glance? | `tree` |
| Where does this *text* appear? | `rg` |
| What files *exist* with this name/extension/age? | `fd` |
| Where does this *code shape* appear (calls, defs, JSX, imports)? | `ast-grep` |
| Something emitted JSON | `jq` |
| Something is YAML (config, CI, compose) | `yq` |
| I have <10 candidate files and need to understand them | the `Read` tool |

Rule of thumb: **`rg` for text, `ast-grep` for structure.** Reach for `ast-grep`
the moment a regex would need to care about whitespace, line breaks, nesting, or
balanced parens — those are exactly the cases regex gets wrong.

Never fall back to `find`, `grep`, recursive `ls`, or regex-based source rewrites
when the purpose-built tool above applies — those produce noisier output and, for
rewrites, are more likely to corrupt code. **If a preferred tool is missing, say so
before falling back** (see Availability and fallbacks at the end).

## Method

0. **Get oriented** with `tree -L 2 -I '.git|node_modules|build|dist|target'` (or
   `-L 3` for a smaller repo) the first time you touch an unfamiliar directory —
   cheaper than a blind search and it tells you where the code even lives.
1. **Cast wide, cheaply.** `rg -l` / `rg -c` to see *which* and *how many* files
   are involved before printing any match bodies. A search that returns 400 hits
   is a signal to narrow, not to read 400 hits.
2. **Narrow** with type filters (`-t ts`), globs (`-g`), and word boundaries (`-w`).
3. **Confirm structurally** with `ast-grep` if the pattern is code-shaped.
4. **Read** the surviving handful of files with the `Read` tool.
5. **Save anything you'll run again** into `.scripts/` (see below) instead of
   retyping it on the next pass.

Report findings as `path/to/file.ts:42` — those are clickable.

## Java/Kotlin/TypeScript: run the bundled script instead of steps 1-4

This skill ships with two scripts that do the whole narrowing pass in one
command — `scripts/search-jvm-sources.py` for `.java`/`.kt`/`.kts`, and
`scripts/search-ts-sources.py` for `.ts`/`.tsx`/`.js`/`.jsx`. Same CLI, same
flags, same output. Invoke by absolute path from this skill's own directory (the
one holding `SKILL.md`) — they are *not* on `PATH` and do not live in the repo
being searched:

```bash
~/.claude/skills/code-researcher/scripts/search-jvm-sources.py <keyword> [root]
~/.claude/skills/code-researcher/scripts/search-ts-sources.py <keyword> [root]
```

It prints a reading list of at most 200 files, grouped by why each one is on it:

```
Reading list for 'mcp server' — 7 files, ~1,159 lines to read

READ FIRST (2 files, ~172 lines) — the keyword is named or declared here
    1. java/dev/mcp/workspace/config/ServerConfig.java:86   1 mention, defines ServerConfig
THEN (4 files, ~918 lines) — direct collaborators of the files above
    3. java/dev/mcp/workspace/transport/HttpRunner.java:20  uses ServerConfig, uses ServerIdentity
SKIM IF NEEDED (1 file, ~69 lines) — further out, reached through an on-topic type
    7. java/dev/mcp/workspace/transport/McpError.java:11    uses McpServlet
```

Read top-down and stop when the question is answered — the tiers exist so that
stopping early is safe. `--json` gives the same data with `tier`, `hops`, `score`
and `lines_total` per file for scripted use.

What it saves you from doing by hand: it searches only real source sets
(`src/main/java`, `src/main/kotlin`, `test/…`, `commonMain/…`) so `build/`,
`out/`, `target/` and `generated/` never pollute results; matches the keyword
case-insensitively across `userProfile` / `user_profile` / `USER-PROFILE`; then
fans out from the direct hits along type references and imports to pull in the
call sites and collaborators — gated on the keyword past the first hop, so it
stays a search instead of enumerating the dependency graph.

**It is fuzzy, so a wrong guess still lands.** Matching runs against the repo's
own vocabulary — file names and declared type names — so `srvconfig` finds
`ServerConfig` (0.86), `McpServelt` finds `McpServlet` (0.93), and `workspace svc`
finds `WorkspaceService`. Word order does not matter (`config server` works).
Matches are shown as `≈ServerConfig (0.86)` and discounted by similarity, so they
never outrank the real thing. This is what to use **instead of spending round 2
of the search budget on synonyms** — one run tells you whether the name you are
guessing at exists in some other spelling. When nothing clears the bar, the error
names the closest identifiers in the repo, which answers the vocabulary-mismatch
question directly.

Flags worth knowing: `--depth 0` for direct hits only (5 hops by default),
`--no-tests` to drop test sources (they are demoted and tagged `[test]`
otherwise), `-n` to shorten the list, `--seeds` to widen the fan-out base,
`--fuzzy` to move the similarity bar (0.8 default; `--fuzzy 0` for exact only).

**The TypeScript one follows the module graph.** A TS import names a file, not a
type, so `search-ts-sources.py` resolves module specifiers to real paths and
follows them both ways — what a seed imports and who imports it. `@/lib/auth`,
`~/lib/auth` and `src/lib/auth` all resolve without reading tsconfig. Source
roots are `src`/`app`/`lib`/test dirs at any depth, so monorepos work as-is. Only
*exported* declarations become graph symbols, symbols spread across more than 20%
of the repo are ignored as edges, and re-export-only `index.ts` barrels are
demoted.

Both need `rg`; `fd` and `ast-grep` are used when present and each prints a notice
before falling back. Declared names come from `ast-grep` by node kind rather than
regex — a Java comment reading "the record that ..." otherwise registers a type
called `that`, and in TS a regex cannot tell an exported symbol from a local
`const res` inside an exported function.

For anything that isn't Java, Kotlin, or TypeScript/JavaScript, use the method
above.

## Search budget: three rounds, then ask

Each individual search is cheap, which is exactly the trap — a stalled
investigation doesn't announce itself, it just keeps producing plausible next
commands. Twenty of them cost more than the question you should have asked after
the third. Searching is not free just because no single command is expensive.

**Budget: three rounds per unknown.** A round is one hypothesis, however many
commands it takes to test. Escalate deliberately rather than re-rolling the same
idea:

1. The user's exact vocabulary — `rg -lw 'theirTerm'`.
2. Loosened — drop `-w`, add `-i`, add `-u` (the file may be `.gitignore`d),
   widen the glob.
3. Structural or synonymous — `ast-grep` for the code shape, or the two or three
   names the codebase would plausibly use instead.

If round 3 ends without a candidate file set, **stop and ask.** Do not start a
fourth round with a fourth synonym.

### Stop before spending the budget when

- **The user's term returns zero hits anywhere**, including `-i -u`. Their word
  doesn't exist in this repo — that's a vocabulary mismatch, and no amount of
  additional searching invents the mapping. Ask what it's called here.
- **Two readings both have real hits.** That's ambiguity, not a search problem;
  more searching cannot resolve which one they meant.
- **Narrowing twice still leaves 100+ hits.** The request is too broad to act on.
  Ask which subsystem, not which regex.
- **The answer depends on intent that isn't in the code** — which of two designs
  they want, whether a behavior is a bug or deliberate. Unknowable by grep.

### Ask a question that carries the search

A bare "can you clarify?" throws away everything you learned and makes the user
do the work twice. State what you looked for, what you found, and offer the
specific choice:

> `rg -lw 'sessionToken'` finds nothing. The closest things are `authToken` in
> [auth/session.ts:18](auth/session.ts#L18) and `refreshToken` in
> [auth/refresh.ts:40](auth/refresh.ts#L40). Which is the one that's expiring early?

That's answerable in three words. "Where is the session code?" is not.

### Don't ask when

You haven't run a single search yet — spend at least round 1 first; most
questions die there. The answer is derivable from what you've already read.
Or it's a routine judgment call a colleague would just make and mention
(naming, file placement, test location) — make it, say you made it, move on.

### Stay inside the question

Adjacent problems you notice mid-search — a nearby bug, a dubious pattern, a
tempting refactor — get **mentioned in one line at the end**, not investigated.
Each detour costs another handful of files in context and pushes the actual
answer further away. Finish the asked question first.

## Work through `.scripts/`, don't re-type pipelines

Investigation is repetitive: the same search gets re-run with a tweaked pattern, a
wider glob, a different directory. Retyping a long pipeline each time burns turns
and quietly introduces typos that silently change the result.

**Rule: the moment a command is worth running a second time, it belongs in a script.**

- Every script goes in a `.scripts/` directory at the root of the current working
  directory. **Create it yourself when it doesn't exist** (`mkdir -p .scripts`) —
  don't ask, don't fall back to running inline.
- **Don't re-create what ships with this skill.** `scripts/search-jvm-sources.py`
  and `scripts/search-ts-sources.py` already cover "which files should I read for
  X" in JVM and TS repos — call them, don't write a smaller version into
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
  matches only a plain block-bodied fun with no return type — it misses
  `suspend fun`, `fun f(): T`, *and* expression bodies (`fun f() = expr`, which needs
  `fun $N($$$) = $EXPR`). Writing one pattern per shape is a losing game; reach for
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

## Don't hang the terminal

Read files with the `Read` tool, not a pager-backed command. Anything that may
page or wait for input needs that disabled explicitly — `git --no-pager diff`,
`git log | cat` — or it blocks forever.

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
