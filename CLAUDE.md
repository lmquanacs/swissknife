# CLAUDE.md

This repo is a personal collection of Claude Code skills — not an application.
There's no build, no tests, no runtime; the deliverable is the Markdown itself.

## Layout

```
skills/<skill-name>/SKILL.md
```

Each skill is a self-contained directory under `skills/`, named to match its
frontmatter `name:` field (Claude Code requires this). Currently there's one:
`code-researcher`.

## Editing a skill

- `SKILL.md`'s frontmatter `description:` is what Claude Code matches against
  to decide when to load the skill — when you change what a skill covers,
  update `description` too, or it stops triggering on the cases you just added.
- `README.md` documents the same skill for humans (prerequisites, install
  instructions, a shorter summary of the method) at the project root. It's a
  separate audience from `SKILL.md`, so it drifts independently — when you
  change what a skill *does*, check whether the README's summary or
  prerequisites table needs the same update.
- New skills follow the same pattern: `skills/<name>/SKILL.md` with `name` and
  `description` frontmatter, a section in the root `README.md`, and an entry in
  the "Should this be a plugin?" section's trigger condition (a second skill is
  explicitly called out there as the point to reconsider plugin packaging).

## Testing a change

There's no test suite — verification is starting a new Claude Code session
(skills load at session start, not mid-session) and either invoking the skill
by name (`/code-researcher`) or issuing a prompt that should trigger its
`description`.

## Scope of this file

Tool-usage rules (rg vs grep, ast-grep vs sed, etc.) belong inside the relevant
`SKILL.md`, not here — that's the artifact Claude Code actually loads when
those rules need to apply. Don't duplicate them into this file.
