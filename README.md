# utilities

Personal Claude Code tooling.

## Skills

### `context-builder`

One skill covering the whole arc from "I don't know this codebase" to "here is
exactly what the next agent needs to know." A four-phase loop — **Frame →
Discover → Reflect → Shape** — built on the premise that most bad agent output
is a context failure, not a reasoning failure: the model was missing the one
file that mattered, or it was buried under forty that didn't.

- **Frame** — turn the task into three to seven checkable questions and pick a
  budget tier before opening anything.
- **Discover** — climb the cost ladder (structure → paths → text search →
  structural search → ranged reads → full reads), where each rung costs ~10× the
  one below. **`rg` for text, `ast-grep` for structure**; `fd` for files by name,
  extension or age; `jq`/`yq` for anything structured. `semgrep` is the one
  escalation off the ladder — the only tool here with dataflow, for whether a
  value *reaches* a sink rather than where a shape appears.
- **Reflect** — the phase that gets skipped. Write a ledger of question →
  status → evidence after each round. **Three rounds per open question**, then
  either ask a question that carries the search, or log it as an open question.
  An open question is a legitimate output; a confident guess is not.
- **Shape** — pack findings as anchors (`path:line`) rather than excerpts, label
  every claim `verified` / `inferred` / `assumed`, and order stable material
  first so the prefix stays cacheable.

Bundled: **two reading-list scripts** ([below](#the-reading-list-scripts)) that
collapse the whole narrowing pass into one command for JVM and TypeScript repos,
and a **`.scripts/` convention** — any command worth running twice gets saved as
a parameterized script instead of retyped with slight variations every pass.

Source: [`skills/context-builder/SKILL.md`](skills/context-builder/SKILL.md).
The body stays under 500 lines; the detail lives in `references/` and loads only
when needed — [`tool-cookbook.md`](skills/context-builder/references/tool-cookbook.md)
(tool flags, `ast-grep` gotchas, Semgrep taint mode, fallbacks, `.scripts/`),
[`discovery-recipes.md`](skills/context-builder/references/discovery-recipes.md)
(search patterns by question type), and
[`pack-templates.md`](skills/context-builder/references/pack-templates.md)
(delta, handoff, review, and working-set pack variants).

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
skills/context-builder/scripts/search-jvm-sources.py <keyword>... [root] \
  [-n 200] [--depth 5] [--fuzzy 0.8] [--no-tests] [--from-file PATH] [--json]

# once the skill is installed, the copies on hand are:
~/.claude/skills/context-builder/scripts/search-jvm-sources.py AuthToken ~/work/api
~/.claude/skills/context-builder/scripts/search-ts-sources.py useAuth ~/work/app
~/.claude/skills/context-builder/scripts/search-ts-sources.py billing . --no-tests
~/.claude/skills/context-builder/scripts/search-ts-sources.py 'vector store' . --json \
  | jq -r '.results[] | select(.tier=="READ FIRST") | .file'
```

From inside the skill body, they are invoked as
`${CLAUDE_SKILL_DIR}/scripts/search-jvm-sources.py` — Claude Code substitutes
that variable with the skill's own directory, so the path works whether the skill
is installed personally, per-project, or symlinked.

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

One optional escalation, not assumed present:

| Tool | Purpose |
|---|---|
| [Semgrep](https://semgrep.dev/) (`semgrep`) | dataflow and taint — the question `ast-grep` can't answer |

```bash
brew install semgrep
```

The skill checks for it only when a flow question comes up, and logs that
question as open if it's missing. Regex is not a fallback for dataflow.

[Konsist](https://docs.konsist.lemonappdev.com/) (Kotlin) and
[ArchUnit](https://www.archunit.org/) (Java) aren't installed — they're *found*.
When a JVM repo already encodes layering and naming as tests, those tests are
the conventions, and get read rather than inferred from sample files.

## Installing the skill

Claude Code discovers skills in two places: `~/.claude/skills/` (available in
every project) and `<project>/.claude/skills/` (that project only). The command
you type comes from the **directory name**, so the installed directory must be
called `context-builder` for `/context-builder` to work. This repo keeps skills
in a plain top-level `skills/` directory, so pick one of the following to make it
visible to Claude.

### Symlink for personal use — recommended

Keeps this repo as the single source of truth. Edits to `SKILL.md`, the
`references/`, and the scripts take effect immediately, and `git pull` updates
the installed skill.

```bash
mkdir -p ~/.claude/skills
ln -s "$(pwd)/skills/context-builder" ~/.claude/skills/context-builder
```

### Symlink into a single project

When you only want it in one repo, and/or want to commit it for teammates:

```bash
mkdir -p /path/to/project/.claude/skills
ln -s "$(pwd)/skills/context-builder" /path/to/project/.claude/skills/context-builder
```

Symlinks don't survive a `git clone`, so to share it with a team, copy the
directory in and commit it instead:

```bash
cp -R skills/context-builder /path/to/project/.claude/skills/
```

### Copy instead of symlink

If you'd rather pin a version and not have it move under you:

```bash
mkdir -p ~/.claude/skills
cp -R skills/context-builder ~/.claude/skills/
```

### Verify

Start a new Claude Code session — skills are picked up at session start, so an
already-running session won't see it. Then either invoke it by name with
`/context-builder`, or just ask a question it should trigger on, like
"where is X defined in this repo?"

If it doesn't show up, check that the file is at
`~/.claude/skills/context-builder/SKILL.md` (the directory name and the `name:`
field in the frontmatter should match) and that the YAML frontmatter is intact.

## Use case prompt templates

Five prompts for the situations the skill is built for. Each one names the skill
so it loads, states a **budget tier** so it doesn't over-read, and asks for a
specific **output shape** so what comes back is a briefing rather than a
transcript of the search.

Fill the `<angle brackets>` and delete any line that doesn't apply. The two lines
that carry the most weight are the tier and the output shape — leave them out and
Claude picks its own, and the default instinct is always "read more files."

Every template ends by **writing its result to a markdown file**. That line is
not decoration: a pack that only exists in scrollback has to be rebuilt from
scratch next session, and rebuilding it costs the same tokens as building it did.
Written to a file, it survives a `/clear`, gets read by the next agent for a few
hundred tokens, and can be diffed as the work moves.

Each template is also on disk as a standalone file, ready to copy whole:

| # | Use | File |
|---|---|---|
| 1 | Debugging | [`prompts/01-before-debugging.md`](skills/context-builder/prompts/01-before-debugging.md) |
| 2 | Planning and implementing | [`prompts/02-before-planning-implementing.md`](skills/context-builder/prompts/02-before-planning-implementing.md) |
| 3 | Reviewing code | [`prompts/03-before-reviewing-code.md`](skills/context-builder/prompts/03-before-reviewing-code.md) |
| 4 | Refactoring | [`prompts/04-before-refactoring.md`](skills/context-builder/prompts/04-before-refactoring.md) |
| 5 | Documentation | [`prompts/05-for-documentation.md`](skills/context-builder/prompts/05-for-documentation.md) |

| Tier | Reads | Use for |
|---|---|---|
| Micro | 1–3 files, ranges only | single-file edit, location already known |
| Standard | 4–10 files | most debugging, most feature work, most reviews |
| Deep | 10–25 files | architecture-wide refactors, documentation, audits |

Anything that won't fit in Deep is too big for one pass — split it and run one
prompt per piece.

### 1. Before debugging

Keeps the investigation from starting at the fix. The key constraint is the last
line: a cause proposed before the ledger exists is a guess wearing evidence.

```text
Use context-builder before touching anything.

Symptom: <what happens — paste the error verbatim if there is one>
Expected: <what should happen instead>
Repro: <steps, or the name of the failing test>
Where I think it lives (may be wrong): <path or subsystem, or "no idea">

Frame the questions first, then discover. Budget: Standard.
Search the literal error string before anything else, and walk the value
backward to its write site rather than forward from the symptom.
If the trail turns into "does this value actually reach that call", stop
regexing and write a throwaway `semgrep` taint rule scoped to the package. If
semgrep isn't installed, say so and leave that question open.

Give me back:
- a ledger: question / status / evidence
- findings as `path:line` anchors, each labelled [verified] or [inferred]
- the 2-3 most likely causes, ranked, each tied to a specific anchor
- anything you could not determine, under Open questions

Do not propose a fix in this turn, and do not open files that no question needs.

Write the results to `debug-result.md`: the ledger, the findings, and the ranked
causes. I want it on disk before we discuss the fix.
```

### 2. Before planning and implementing

The "am I duplicating something" question is the one that pays for this prompt
outright — it is also the one that never gets asked without prompting.

```text
Use context-builder, then plan. Don't write code in this turn.

Goal: <the change, in one sentence>
Constraints I already know: <versions, what must not break, style rules>

Discovery must answer at minimum:
- Is there existing code that already does this, that I would be duplicating?
- What is the convention here for <the kind of thing being added>?
  Look at three similar files, not one — one file might be the outlier.
  Check the existing unit and integration tests first — if the conventions are
  already enforced as tests, those are the answer, and my change keeps them
  green.
- What is the blast radius of touching <symbol or module>?
- Where do the tests for this area live?

Budget: Standard. Raise to Deep only if the blast radius count justifies it.

Give me back a standard pack — Objective, Constraints, Map, Findings with
`path:line`, Open questions, Not included — and then a numbered plan where
every step cites an anchor from the Findings. Flag any step that rests on an
[inferred] claim rather than a [verified] one.

Write both to `plan-result.md` — the pack first, the plan after it. That file is
what I'll hand back to you when we start implementing, so it has to stand alone.
```

### 3. Before reviewing code

A diff read in isolation is the main source of confident-but-wrong review
findings. This ordering forces the caller context to be gathered before any
judgement is allowed.

Comments come out in [Conventional Comments](https://conventionalcomments.org/)
format — `<label> [decorations]: <subject>`. The label does real work beyond
tidiness: it forces a decision about *what kind* of feedback each comment is,
and `question:` gives uncertainty somewhere to go that isn't a padded `issue:`.
Blocking versus non-blocking stops being tone the author has to infer, and the
output stays greppable.

```text
Use context-builder in review-pack mode: evidence before judgement.

Under review: <PR number, branch, or `git diff main...HEAD`>
What it claims to do: <the PR description, one line>
I care most about: <correctness / performance / security / API surface / all>

Start from `git diff --stat` and `git log --oneline -8` on the touched paths.
For every changed file, find its callers before judging the change. Budget: Standard.
If I said security above, run semgrep taint mode over the touched paths first:
an issue: (security) has to name a source, a sink, and the path between them.
No semgrep on PATH — say so, and those comments are question:, not issue:.

Give me back, in this order:
1. What changed — the diff summarized, no opinions yet
2. The context each change lands in — `path:line` anchors for callers,
   contracts, and the existing conventions the change should be matching
3. Only then the review comments, in Conventional Comments format
4. Open questions — things that need the author, not more searching

Format every comment as:

    <label> [decorations]: <subject>

    [discussion]

- label — one of praise, nitpick, suggestion, issue, todo, question, thought,
  chore, note. Use typo, polish or quibble if one of them fits better.
- subject — the point itself, one line.
- decorations — parenthesised, comma-separated. Always carry (blocking) or
  (non-blocking); add a topic decoration such as (security), (test), (perf),
  (ux) where it classifies further. Keep the list short — a comment wearing
  four decorations has stopped being readable.
- discussion — optional, but required on anything (blocking): the why, and
  what resolving it looks like.

Label rules I care about:
- issue: only for a problem you can state as a concrete failure — specific
  input or state -> wrong output. Pair it with a suggestion: for the fix.
- question: when you suspect a problem but cannot demonstrate it. Do not
  promote a suspicion to issue: to make it land harder; that is how reviews
  lose credibility.
- nitpick:, thought:, note: are non-blocking by nature. Never mark them blocking.
- praise: at least one, and only where it is sincere. Skip it rather than
  manufacture it.
- Every comment carries a `path:line` anchor.

Ground each comment in something you actually read. If a claim rests on
inference rather than a file you opened, it is a question:, not an issue:.

Write the review to `review-result.md` as: Summary (no opinions), Context (the
anchors from step 2), Blocking (most severe first), Non-blocking (grouped by
label), Open questions. If nothing blocking survived verification, say so
plainly at the top rather than promoting a nitpick to fill the space.
```

### 4. Before refactoring

Refactors fail on the sites nobody found. Counting first is what stops the
budget being spent reading site 4 of 60, and `ast-grep` is non-negotiable here —
a regex misses calls split across lines and matches them inside comments.

```text
Use context-builder before any edit.

Refactor: <from X to Y — rename, extract, change a signature, replace a pattern>
Scope: <whole repo / this package / these paths>

Count before you read:
- `rg -lw '<symbol>' | wc -l` for the blast radius as a number
- `rg -cw '<symbol>'` for where it concentrates
- `ast-grep` for the structural sites — not regex, so multi-line calls aren't
  missed and matches inside comments and strings aren't counted
- `rg -l 'Konsist\.scopeFrom|archunit'` on a JVM repo — architecture rules are
  constraints on the refactor, not tests to fix afterwards

If the blast radius is over ~15 files, stop and give me the number instead of
reading them all — that count is itself the finding. Budget: Deep.

Give me back:
- the blast radius count and the per-file concentration
- every site grouped by the kind of change it needs — mechanical / needs
  thought / ambiguous — each as a `path:line` anchor
- the interface or contract that pins the current shape, quoted exactly
- what test coverage already exists over the affected sites, plus any
  Konsist/ArchUnit rule the refactor would violate, quoted
- Open questions for any site you cannot classify

Then propose an edit order, safest first. Don't start editing.

Write it to `refactor-result.md`: the blast radius count, the three site groups
as a checklist I can tick through, and the edit order. Keep that file updated as
edits land, so it doubles as the progress tracker.
```

### 5. For documentation

The only one that starts top-down rather than anchor-out, because there is no
anchor. The `[inferred]` labels matter more here than anywhere else — an inferred
claim that ships in a doc becomes something the next person trusts.

```text
Use context-builder with top-down seeding — I have no anchor.

Document: <what — a module, a service, the public API, onboarding for <area>>
Audience: <new teammate / API consumer / future me>
Length target: <e.g. one page>

Orient first with the unfamiliar-repo sequence — tree, the directory histogram,
the manifest through jq/yq, recently-changed files — then anchor out from the
entry point. Budget: Deep, but stop early if the map converges sooner.

Read interfaces, types, and configs. Skip tests and implementation bodies unless
a behavior is documented nowhere else. Exception on JVM repos: Konsist or
ArchUnit tests are the architecture written as code — read them, and cite them
[verified], because they're enforced rather than observed.

Give me back:
- a Map: `path` plus one line each, for every component that earns a mention
- the public surface: exact signatures pulled with ast-grep, not paraphrased
- configuration: the precedence order (default -> file -> env -> flag), which
  matters more than any individual value
- every claim labelled [verified] or [inferred] — I won't ship an [inferred]
  claim without checking it myself
- Open questions: what the code does not explain about itself

Then draft the doc, with every factual claim traceable to an anchor above.

Write two files, because they have different lifespans:
- `<topic>.md` — the doc itself, clean prose, no labels, ready to ship
- `doc-result.md` — the evidence behind it: the map, the anchors, and every
  [inferred] claim I still need to verify. I delete this once the doc is checked.
```

### Adapting any of them

- **JVM or TS/JS repo** — prepend: *"Run the bundled reading-list script first
  (`~/.claude/skills/context-builder/scripts/search-ts-sources.py <keyword>`) and
  triage from its evidence column before opening anything."* That replaces the
  whole manual narrowing pass.
- **Unsure of the vocabulary** — append: *"If my term returns zero hits, don't
  try synonyms. Tell me the closest identifiers in the repo and ask."* Stops the
  fourth-synonym spiral before it starts.
- **Task already in progress** — swap the output shape for a delta pack: *"Only
  what changed since the last pack: ledger, new findings, next action. Don't
  restate anything we already established."*
- **Handing off to another agent or a fresh session** — ask for a handoff pack:
  self-contained, assuming no shared history.
- **Two keywords, one question** — *"where do auth and retry meet"* is a single
  run: `search-ts-sources.py auth retry --all`.
- **A flow or security question** — append: *"Write a throwaway semgrep taint
  rule rather than more grep rounds. If semgrep isn't installed, tell me instead
  of inferring the flow."*

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
