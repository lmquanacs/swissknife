# Mandatory Tooling Policy

You **MUST** use the preferred repository tools below when they are available. Before any repository exploration, file discovery, code search, structured-data inspection, or syntax-aware refactor, run:

```sh
command -v tree fd rg ast-grep jq yq
```

Treat each tool as available only if this command outputs its executable path. Do not assume tools are installed. If a preferred tool is missing, explicitly state: **“`<tool>` is unavailable; using `<fallback>` instead.”** Then use the specified fallback. Never silently use a fallback.

## Required Tool Selection

| Task | Required tool | Fallback if unavailable |
|---|---|---|
| Show repository structure | `tree` | Use `fd -t d -d 2 . \| head -50`; if `fd` is unavailable, use `ls` one directory level at a time. |
| Find files/directories | `fd` | Use `find` with explicit `.git` and `node_modules` pruning. |
| Search text/code | `rg` | Use `grep -rn --exclude-dir={.git,node_modules,dist,build}`. |
| Perform syntax-aware code search/refactors | `ast-grep` | Use `rg` to locate candidates, inspect context, and edit every match individually. |
| Read or transform JSON | `jq` | Use `node -e` or `python3 -c` with a real JSON parser. |
| Read or transform YAML | `yq` | Use Python with `yaml.safe_load` if supported; otherwise use `rg -n -A3` only for non-structural inspection. |

## Safe Fallback Commands

When `fd` is unavailable, use:

```sh
find . \
  -path './node_modules' -prune -o \
  -path './.git' -prune -o \
  -type f -print
```

When `rg` is unavailable, use:

```sh
grep -rn \
  --exclude-dir={.git,node_modules,dist,build} \
  'PATTERN' .
```

When `jq` is unavailable, use one of:

```sh
node -e "const fs=require('fs'); console.log(JSON.parse(fs.readFileSync('FILE.json','utf8')))"
```

```sh
python3 -c "import json; print(json.load(open('FILE.json')))"
```

When `yq` is unavailable, prefer:

```sh
python3 -c "import yaml; print(yaml.safe_load(open('FILE.yml')))"
```

## Non-Negotiable Safety Rules

- **Never** run an unbounded recursive `ls`.
- **Never** use `grep -r` without exclusions for generated, dependency, and Git directories.
- **Never** use a blind repository-wide `sed -i` replacement as a substitute for `ast-grep`.
- **Never** parse JSON or YAML with regex when a language parser is available.
- **Always** inspect code in context before modifying it.
- **Always** preserve `.git`, `node_modules`, `dist`, and `build` exclusions during fallback search operations.
