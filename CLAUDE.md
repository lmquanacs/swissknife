# Tool Availability and Fallbacks

Before assuming any repository-inspection tool exists, run:

```sh
command -v tree fd rg ast-grep jq yq
```

If a preferred tool is unavailable, explicitly say so before using its fallback. Never silently degrade to a noisier or less safe command.

## Required Tool Fallbacks

| Preferred tool | If unavailable |
|---|---|
| `tree` | State that `tree` is missing. Use `fd -t d -d 2 . \| head -50`; if `fd` is also unavailable, use `ls` one directory level at a time. |
| `fd` | State that `fd` is missing. Use `find` with explicit pruning for `.git` and `node_modules`. |
| `rg` | State that `rg` is missing. Use `grep -rn --exclude-dir={.git,node_modules,dist,build}`. |
| `ast-grep` | State that `ast-grep` is missing. Use `rg` to find candidates, inspect each occurrence, and edit each site individually. Never use blind repository-wide `sed -i` replacements. |
| `jq` | State that `jq` is missing. Use `node -e` or `python3 -c` to parse JSON structurally. |
| `yq` | State that `yq` is missing. Use `python3 -c 'import yaml,sys;...'` when YAML support is available, or `rg -n -A3` only for a quick non-structural inspection. |
