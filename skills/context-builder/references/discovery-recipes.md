# Discovery Recipes

Concrete search patterns for Phase 2. Read the section matching the question
you're trying to close, not the whole file.

**Contents**
- Tooling (read once, then skip)
- Recipe 1: Where does this value come from? (data flow, backward)
- Recipe 2: Where does this go? (data flow, forward)
- Recipe 3: Where is this configured?
- Recipe 4: Where does this error originate?
- Recipe 5: What is the convention here?
- Recipe 6: What breaks if I change this?
- Recipe 7: Unfamiliar repo, no anchor
- Low-value reads (skip list)
- Cost reference
- If a tool is missing

---

## Tooling

Check once per session, before the first search:

```bash
command -v tree fd rg ast-grep jq yq
```

Every recipe below assumes this split:

| Looking for | Tool |
|---|---|
| Text — a name, a message, a key | `rg` |
| A code *shape* — call, assignment, declaration, import | `ast-grep` |
| Files by name, extension, or age | `fd` |
| The shape of a directory | `tree` |
| A value inside JSON / YAML / XML | `jq` / `yq` |

Three habits that do most of the saving:

- **`rg -l` before `rg -n` before any read.** A filename list costs ~50 tokens
  and usually changes what you read next.
- **`rg` for text, `ast-grep` for structure.** The moment a regex would have to
  care about line breaks, nesting, or balanced parens, it is the wrong tool —
  `rg '\.method\('` misses calls split across lines and matches them inside
  comments and strings.
- **Don't re-exclude what's already excluded.** `rg` and `fd` honour
  `.gitignore`, so `node_modules`, `dist`, `build`, and `target` are out by
  default. Adding `-g '!**/node_modules/**'` is noise; reach for `-uu` (rg) or
  `-I` (fd) on the rare occasion you need those trees.

If one of these tools is missing, say so before falling back — see the last
section.

---

## Recipe 1 — Where does this value come from? (backward)

Start at the symptom, walk toward the source.

```bash
rg -lw 'fieldName'                              # which files — cheapest first move
rg -nw 'fieldName' -g '!**/*test*'              # where, minus test noise
ast-grep run -p '$X.fieldName = $V' -l <lang>   # writes only, usually 3–5 sites
ast-grep run -p '{ fieldName: $V }' -l ts       # construction sites (DTOs, literals)
```

Read the write sites, not the read sites. One assignment usually explains fifty
reads. Stop as soon as you hit a constructor, a mapper, or a deserialization
boundary — that is the source.

> ast-grep patterns must parse on their own. Bare `fieldName: $V` is read as a
> labelled statement and matches nothing; the braces are what make it an object
> property.

## Recipe 2 — Where does this go? (forward)

```bash
ast-grep run -p '$X.methodName($$$)' -l <lang>    # every call, multi-line included
ast-grep run -p 'import $$$ from "$SRC"' -l ts    # what a module pulls in
rg -lw 'ClassName'                                # dependents, by file count first
```

If call sites exceed ~15, the thing is infrastructure — read its contract, not
its consumers. Sample two call sites at most.

## Recipe 3 — Where is this configured?

Configuration resolves through layers; find the layers before reading values.

```bash
fd -e yaml -e yml -e toml -e properties -e env -d 3
rg -nw 'KEY_NAME'                                          # every reference
rg -n 'getenv|process\.env|@Value|ConfigurationProperties' # the resolution layer
```

Then read the values with a parser, never a regex:

```bash
yq '.server.auth' config.yaml
jq -r '.scripts // {} | keys[]' package.json
yq -p xml -oy '.project.properties' pom.xml     # -oy silences the format warning
```

The precedence order (default → file → env → flag) matters more than any single
value. Capture the order as a finding; capture values only if the task depends
on them.

## Recipe 4 — Where does this error originate?

Search the literal message first — it's the highest-signal string available.

```bash
rg -nF 'part of the exact error text'      # -F: literal, no escaping needed
rg -n 'fragment between the placeholders'  # when the message is templated
rg -nw 'FooException'                      # the named type, if there is one
ast-grep run -p 'throw new $E($$$)' -l <lang> <dir>   # every throw site
```

If the message is templated, search the invariant fragment between the
placeholders. Then read the throw site's enclosing function only.

## Recipe 5 — What is the convention here?

Never infer conventions from one file — you'll copy an outlier.

```bash
git log --oneline -15                                    # what recent work looks like
fd -e <ext> . <dir> | head -20                           # naming patterns from paths
ast-grep run -k class_declaration -l <lang> <dir> | head -30
fd -H -d 2 -g '{.editorconfig,*eslintrc*,CONTRIBUTING*}' # rules stated outright
```

Three similar files, skimmed for structure, beats one file read closely. The
config and CONTRIBUTING files state conventions directly and cost almost
nothing — check them before inferring anything.

> Two `fd` gotchas: the pattern comes first and the path second, so `fd -e ts src`
> searches the *current* directory for files named `src` — you want `fd -e ts . src`.
> And `-g` takes one value; a second `-g` is parsed as a search path. Put the
> alternatives in one brace glob instead.

## Recipe 6 — What breaks if I change this?

```bash
rg -lw 'SymbolName' | wc -l              # blast radius as a number
rg -cw 'SymbolName'                      # per-file counts — where it concentrates
git log --oneline -8 -- <path>           # is this hot or frozen?
rg -lw 'SymbolName' -g '**/*test*'       # existing coverage
```

Count first, read second. If the blast radius is large, that count *is* the
finding — record it and don't read all the sites.

## Recipe 7 — Unfamiliar repo, no anchor

Fixed sequence, roughly 800 tokens total:

```bash
tree -L 2 -I '.git|node_modules|build|dist|target'
git ls-files | sed 's|/[^/]*$||' | sort | uniq -c | sort -rn | head -20
fd -e md -d 2
jq '{name, scripts: (.scripts // {} | keys), deps: (.dependencies // {} | keys)}' package.json
yq -p xml -oy '.project.dependencies' pom.xml     # or: yq '.project' pyproject.toml
fd --changed-within 14d -e <ext> | head -20       # where the work is happening now
```

`tree` gives you the shape; the directory histogram (line 2) tells you where the
substance lives versus where the boilerplate lives, and it is the single
highest-value command here. The manifest read through `jq`/`yq` gives you the
scripts and dependencies without the 200 lines of lockfile-adjacent noise around
them. Then switch to anchor-out.

---

## Low-value reads (skip unless the task is specifically about them)

- Lock files, `dist/`, `build/`, `target/`, generated clients, `.min.` files
- Vendored dependencies and `node_modules`
- Migration histories — read the current schema instead
- Test files, when the interface or type definition exists
- README marketing sections — the install and architecture parts may be useful,
  the pitch is not
- Files you already read this session — cite them, don't re-read
- Sibling implementations of an interface you've already read — one plus the
  interface is enough

Most of this list is excluded for free: `rg` and `fd` skip anything in
`.gitignore`, so the discipline you actually need is not re-adding it with `-uu`.

## Cost reference

Rough order of magnitude, for budgeting:

| Action | Tokens |
|---|---|
| `tree -L 2` | 100–300 |
| `rg -l` / `rg -c` across a repo | 50–200 |
| `ast-grep run -p` across a repo | 100–400 |
| `jq`/`yq` on one manifest key | 50–150 |
| `rg -n -C3` with ~20 hits | 400–800 |
| Ranged read, 40 lines | 400–600 |
| Full read, 300-line file | 3–4k |
| Full read, 1000-line file | 10–13k |

Four full reads of medium files ≈ one Standard-tier budget spent entirely on
raw material, leaving nothing for reasoning. This is the arithmetic that makes
search-first non-negotiable: every row above the ranged-read line is nearly free
by comparison, and any one of them can remove a read from the plan.

## If a tool is missing

Name the missing tool before you fall back — a silent downgrade to a noisier
command is how a pack ends up with bad evidence in it.

| Preferred | If missing, state that, then |
|---|---|
| `tree` | `fd -t d -d 2 . \| head -50`, or `ls` one level at a time |
| `fd` | `find . -path './node_modules' -prune -o -path './.git' -prune -o -type f -print` |
| `rg` | `grep -rn --exclude-dir={.git,node_modules,dist,build}` |
| `ast-grep` | `rg` to locate candidates, then inspect each in context — never a blind `sed -i` |
| `jq` | `python3 -c 'import json,sys; ...'` or `node -e` |
| `yq` | `python3 -c 'import yaml,sys; ...'`; `rg -n -A3` only for a non-structural peek |
