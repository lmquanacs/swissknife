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
- **A three-round search budget** — when a search stalls, stop and ask one
  specific question carrying what you already found, rather than grinding through
  a fourth synonym.
- **Two bundled reading-list scripts** — [`search-jvm-sources.py` and
  `search-ts-sources.py`](#the-reading-list-scripts) below, which do the whole
  narrowing pass in one command for JVM and TypeScript repos.

Source: [`skills/code-researcher/SKILL.md`](skills/code-researcher/SKILL.md)

#### The reading-list scripts

Answer "which files should I read for X?" with a reading list of at most 200
files — the narrowing move the skill describes, packaged so it doesn't get
retyped per repo. The output is written to be handed to an agent (or read
yourself): files are grouped into **read first / then / skim if needed** by how
they were found, numbered in reading order, and each group carries a line count
so the reader knows what it is signing up for.

| Script | Reads |
|---|---|
| `search-jvm-sources.py` | `.java`, `.kt`, `.kts` |
| `search-ts-sources.py` | `.ts`, `.tsx`, `.js`, `.jsx`, `.mts`, `.cts`, `.mjs`, `.cjs` |

Same CLI, same flags, same output; they differ only where the languages do. They
ship inside the skill, so installing the skill installs them — and they run
standalone just as well.

```bash
skills/code-researcher/scripts/search-jvm-sources.py <keyword> [root] \
  [-n 200] [--depth 5] [--fuzzy 0.8] [--no-tests] [--json]

# once the skill is installed, the copies on hand are:
~/.claude/skills/code-researcher/scripts/search-jvm-sources.py AuthToken ~/work/api
~/.claude/skills/code-researcher/scripts/search-ts-sources.py useAuth ~/work/app
~/.claude/skills/code-researcher/scripts/search-ts-sources.py billing . --no-tests
~/.claude/skills/code-researcher/scripts/search-ts-sources.py 'vector store' . --json \
  | jq -r '.results[] | select(.tier=="READ FIRST") | .file'
```

```
Reading list for 'mcp server' — 7 files, ~1,159 lines to read
searched 45 sources under .
Read top-down; stop as soon as the question is answered.

READ FIRST (2 files, ~172 lines) — the keyword is named or declared here
    1. java/dev/mcp/workspace/config/ServerConfig.java:86       1 mention, defines ServerConfig
    2. java/dev/mcp/workspace/transport/ServerIdentity.java:14  1 mention, uses ServerConfig

THEN (4 files, ~918 lines) — direct collaborators of the files above
    3. java/dev/mcp/workspace/transport/HttpRunner.java:20      uses ServerConfig, uses ServerIdentity, uses McpServlet
    4. java/dev/mcp/workspace/transport/McpServlet.java:41      uses ServerConfig, uses ServerIdentity, uses McpError
    5. java/dev/mcp/workspace/Main.java:18                      uses HelpRequested, uses ServerConfig
    6. java/dev/mcp/workspace/fs/WorkspaceService.java:32       uses ServerConfig

SKIM IF NEEDED (1 file, ~69 lines) — further out, reached through an on-topic type
    7. java/dev/mcp/workspace/transport/McpError.java:11        uses McpServlet
```

- **Source roots only.** It locates `src/` trees and keeps the source sets
  (`main/java`, `main/kotlin`, `test/…`, `commonMain/…`), so `build/`, `out/`,
  `target/`, `generated/` and resource dirs never reach the results. Pointing it
  straight at `some/module/src/main` works too; a repo with no `src/` layout at
  all gets a printed note and a whole-root search.
- **Keyword, however it's spelled.** `user profile`, `userProfile`,
  `USER_PROFILE` and `User-Profile` all compile to one case-insensitive pattern,
  so the spelling in the code doesn't have to be guessed.
- **Fuzzy, so a wrong guess still lands.** Matching runs against the repo's own
  vocabulary — file names and declared type names — not against raw text, because
  that's where a misspelling is recoverable: `srvconfig` scores 0.86 against
  `ServerConfig` and below 0.5 against everything else. `McpServelt` → `McpServlet`,
  `workspace svc` → `WorkspaceService`, and word order is free (`config server`).
  Hits show as `≈ServerConfig (0.86)`, discounted by similarity so they never
  outrank a real match, and a file that merely *mentions* a fuzzy match is tiered
  as a collaborator rather than a direct hit. `--fuzzy 0.9` tightens the bar,
  `--fuzzy 0` turns it off. When nothing clears the bar the error names the
  closest identifiers in the repo, which is usually the answer you wanted.
- **Direct hits, then fan-out.** Files that name the keyword score first — file
  name, package path, matching type or member declaration, mention count. Those
  seeds then pass a decaying share of their score to files that *use* their
  types and files that *define* what they import, five hops by default
  (`--depth 0` for direct hits only). That's what puts the call sites and
  collaborators on the list rather than just the obvious file.
- **Deep hops stay on the keyword.** Hop 1 follows any collaborator of a seed.
  From hop 2 on, a file only qualifies if it mentions the keyword itself or is
  reached through a type whose name carries one of the keyword's words — an
  ungated walk stops being a search and just enumerates the dependency closure,
  which in an MCP server meant pulling in every protocol value type for the query
  `mcp server`. The gate is why raising `--depth` is safe: past hop 1 the list
  grows only along on-topic edges, so most repos converge well before hop 5.
- **Tests demoted, not hidden** — tagged `[test]` at 0.3× score; `--no-tests`
  drops them.
- Every row is `path:line` anchored at the relevant declaration, so it's
  clickable. `--json` carries the same thing plus `tier`, `hops`, `score` and
  `lines_total` per file — `jq -r '.results[] | select(.tier=="READ FIRST") | .file'`
  is a ready-made read queue.

Declared names come from `ast-grep`, by node kind rather than by regex — a Java
comment containing "the record that ..." otherwise registers a type called
`that`, which then fans out to every file using that word. Needs `rg`; `fd` and
`ast-grep` each fall back (`os.walk`, a declaration regex) with a printed notice
when missing. An 1800-file JVM tree and a 250-file TS monorepo each rank in well
under a second.

**Where the TypeScript one differs**, because the language does:

- **Source roots** are `src/`, `app/`, `lib/`, `source/` and test dirs at any
  depth, so monorepos (`packages/*/src`, `apps/*/src`) work without configuration.
  `node_modules/`, `dist/`, `.next/`, `.turbo/`, `coverage/` and friends are out.
  Roots holding no JS/TS are dropped, so an Android `app/` doesn't sneak in.
- **Imports name files, not types**, so the fan-out resolves module specifiers to
  real paths and follows them *both* ways — what a seed imports and who imports
  it. Relative paths resolve through extensions and `index` files; `@/lib/auth`,
  `~/lib/auth` and `src/lib/auth` all land on the same file via longest-suffix
  matching, with no tsconfig parsing.
- **Only exported declarations** become graph symbols. Locals would make every
  `const res` an edge joining unrelated files.
- **Symbols used across more than 20% of the repo are ignored** as edges — an
  exported `Props` or `formatDate` reaches everything and so distinguishes nothing.
- **`index.ts` files that only re-export are demoted**; a barrel teaches you
  nothing. One that exports a real factory is not treated as a barrel.
- Tests, stories, `__tests__/`, `e2e/` and `cypress/` are demoted and tagged
  `[test]`, not hidden.

## Prerequisites

The skill assumes these are on your `PATH`:

| Tool | Purpose |
|---|---|
| [ripgrep](https://github.com/BurntSushi/ripgrep) (`rg`) | fast text search |
| [fd](https://github.com/sharkdp/fd) | fast file finding |
| [ast-grep](https://ast-grep.github.io/) | structural code search and rewrite |
| [jq](https://jqlang.github.io/jq/) | JSON querying |
| [yq](https://github.com/mikefarah/yq) | YAML querying |
| [tree](https://oldmanprogrammer.net/source.php?dir=projects/tree) | directory orientation |
| `python3` | runs the bundled `search-jvm-sources.py` (stdlib only) |

```bash
brew install ripgrep fd ast-grep jq yq tree
```

Note that ast-grep's binary is `ast-grep`. It also ships an `sg` alias, but that
one is deprecated and prints a warning on every invocation.

## Installing the skill

Claude Code discovers skills in two places: `~/.claude/skills/` (available in
every project) and `<project>/.claude/skills/` (that project only). This repo
keeps skills in a plain top-level `skills/` directory, so pick one of the
following to make it visible to Claude.

### Symlink for personal use — recommended

Keeps this repo as the single source of truth. Edits to `SKILL.md` and to
`scripts/search-jvm-sources.py` take effect immediately, and `git pull` updates
the installed skill.

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
