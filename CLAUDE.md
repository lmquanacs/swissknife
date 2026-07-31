# CLAUDE.md

This repo hosts personal Claude Code skills. It currently has one:
`skills/code-researcher/SKILL.md`, a workflow for investigating codebases with
search tools instead of reading files at random. See `README.md` for the
install/symlink story.

## Mandatory tooling policy

This applies whenever you (Claude) are exploring, searching, or reading code —
in this repo or any other. It is not optional and not limited to when the
`code-researcher` skill is explicitly invoked.

| Task | Use | Never |
|---|---|---|
| Repo/directory overview | `tree -L 2 -I '.git\|node_modules\|build\|dist\|target'` | recursive `ls` |
| Find files by name/extension/age | `fd` | `find` |
| Search file contents | `rg` | `grep` |
| Match/rewrite code by shape (calls, defs, JSX, imports) | `ast-grep` | regex-based `sed`/`perl` rewrites |
| Query JSON | `jq` | ad hoc parsing |
| Query YAML | `yq` | ad hoc parsing |
| Read a known file | the `Read` tool | `cat`, pager-backed commands |

Full flag reference, gotchas, and worked combinations live in
[`skills/code-researcher/SKILL.md`](skills/code-researcher/SKILL.md) — treat it
as the source of truth, not this summary.

Rules, not suggestions:

- **`rg` for text, `ast-grep` for structure.** Reach for `ast-grep` the moment a
  regex would need to care about whitespace, line breaks, nesting, or balanced
  parens.
- **Orient before searching.** Run `tree` on an unfamiliar directory before
  reaching for `rg`/`fd` — it's cheaper than a blind search.
- **Cast wide, then narrow.** `rg -l` / `rg -c` before printing match bodies;
  400 hits means narrow the query, not read all 400.
- **If a preferred tool is missing, say so before falling back** to a noisier
  substitute — don't silently degrade.
- **Save reusable searches.** The moment a command is worth running a second
  time, save it as a parameterized script in `.scripts/` (create the directory
  if needed) instead of retyping it with slight variations.
- **Report findings as `path/to/file.ts:42`** — clickable, and lets the reader
  jump straight to the line.

## Editing this repo

- `skills/code-researcher/SKILL.md` is the skill Claude Code actually loads —
  keep its frontmatter `name`/`description` accurate, since `description` is
  what triggers the skill.
- `README.md` documents the same tools at a higher level for humans (install
  instructions, prerequisites). When the tool list or method in `SKILL.md`
  changes, check whether `README.md` needs the same update.
