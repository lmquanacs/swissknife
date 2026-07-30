# utilities

Personal Claude Code tooling.

## Skills

### `code-researcher`

A workflow for investigating unfamiliar or large codebases with search tools
instead of reading files at random: narrow to a small set of files, then read
those properly.

- **`rg` for text, `ast-grep` for structure** — reach for ast-grep the moment a
  regex would have to care about whitespace, line breaks, or balanced parens.
- **`fd`** for finding files by name, extension, or age (`--changed-within 1d` to
  see what someone was just working on).
- **`jq`** for anything that emits JSON.
- **`.scripts/`** — any command worth running twice gets saved as a parameterized
  shell or Python script in a local `.scripts/` folder, instead of being retyped
  with slight variations on every pass.

Source: [`skills/code-researcher/SKILL.md`](skills/code-researcher/SKILL.md)

## Prerequisites

The skill assumes these are on your `PATH`:

| Tool | Purpose |
|---|---|
| [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) | fast text search |
| [fd](https://github.com/sharkdp/fd) | fast file finding |
| [ast-grep](https://ast-grep.github.io/) | structural code search and rewrite |
| [jq](https://jqlang.github.io/jq/) | JSON querying |

```bash
brew install ripgrep fd ast-grep jq
```

Note that ast-grep's binary is `ast-grep`. It also ships an `sg` alias, but that
one is deprecated and prints a warning on every invocation.

## Installing the skill

Claude Code discovers skills in two places: `~/.claude/skills/` (available in
every project) and `<project>/.claude/skills/` (that project only). This repo
keeps skills in a plain top-level `skills/` directory, so pick one of the
following to make it visible to Claude.

### Symlink for personal use — recommended

Keeps this repo as the single source of truth. Edits to `SKILL.md` take effect
immediately, and `git pull` updates the installed skill.

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/code-researcher" ~/.claude/skills/code-researcher
```

### Symlink into a single project

When you only want it in one repo, and/or want to commit it for teammates:

```bash
mkdir -p /path/to/project/.claude/skills
ln -s "$(pwd)/skills/code-researcher" /path/to/project/.claude/skills/code-researcher
```

Symlinks don't survive a `git clone`, so to share it with a team, copy the
directory in and commit it instead:

```bash
cp -R skills/code-researcher /path/to/project/.claude/skills/
```

### Copy instead of symlink

If you'd rather pin a version and not have it move under you:

```bash
mkdir -p ~/.claude/skills
cp -R skills/code-researcher ~/.claude/skills/
```

### Verify

Start a new Claude Code session — skills are picked up at session start, so an
already-running session won't see it. Then either invoke it by name with
`/code-researcher`, or just ask a question it should trigger on, like
"where is X defined in this repo?"

If it doesn't show up, check that the file is at
`~/.claude/skills/code-researcher/SKILL.md` (the directory name and the `name:`
field in the frontmatter should match) and that the YAML frontmatter is intact.

## Should this be a plugin?

**Not yet — and deferring costs nothing.**

Claude Code plugins bundle skills, slash commands, subagents, hooks, and MCP
servers into one installable unit, distributed through a marketplace repo. That's
worth the ceremony when you have several things to ship, want versioned updates
across machines, or are handing them to other people.

Right now this repo has exactly one skill, and a plugin would add a manifest, a
marketplace entry, and a multi-step install flow to replace a one-line `ln -s`.

The reason there's no hurry: a plugin expects its skills in a top-level `skills/`
directory, which is exactly the layout here already. Converting later is purely
additive — add a `.claude-plugin/plugin.json` manifest at the root and nothing
moves, no paths change, and the symlink instructions above keep working for
anyone who prefers them.

So the trigger to revisit is adding a second skill, a slash command, or a
subagent — or wanting someone else to install this by name rather than by
cloning.
