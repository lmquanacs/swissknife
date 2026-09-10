---
name: context-builder
description: Discover, verify, and shape the minimum context needed before acting on a task. Use whenever you must understand code, repos, docs, or files you haven't read — "where is X defined", "how does Y work", starting work in an unfamiliar area or codebase, bug hunts, tracing call sites and data flow, cross-file refactors, auditing a pattern, deciding what to read first, code review, or briefing a subagent or another agent. Covers search-tool craft (rg, fd, ast-grep, jq, yq, tree, plus Semgrep taint mode) and ships reading-list scripts that rank files by keyword for Java, Kotlin, Python, and TypeScript/JavaScript repos. Also use when the user mentions context engineering, context window, token budget, prompt caching, cost per task, running low on context, taint or dataflow tracking, or asks why an agent's answer was wrong or expensive. Prefer it over ad-hoc file reading any time a task touches more than two files — opening files to "get oriented" is what it replaces.
---

# Context Builder

Build context before you act. Four phases: **Frame → Discover → Reflect → Shape**.

Frame the task as questions. Discover evidence cheaply. Reflect on whether to
continue or stop. Shape what you found into a pack.

## Budget rules

- Search is cheap; reading is expensive. One `rg` costs a few hundred tokens and
  tells you which of 400 files matter. Four wrong reads cost 20k and tell you
  nothing.
- Spend more on search than feels necessary so you can spend less on reads.
- A 50k context of noise performs *worse* than a 3k context of signal, at 15×
  the price. Cut irrelevant tokens, don't just add relevant ones.
- No single search is expensive, so a stalled investigation won't announce
  itself. Cap it in Phase 3.
- Per-command costs: `references/discovery-recipes.md`.

## Phase 1 — Frame

Open no files yet. Write down three things.

**1. Context questions.** Questions with checkable answers, not topics. Three to
seven is normal.

- Bad: "understand the auth system"
- Good: "Where is the session token validated?" / "What happens on expiry?" /
  "Is there existing retry logic I'd be duplicating?"

If you can't name a question, you don't know what you're looking for — any file
you open is a guess.

**2. Success shape.** What the acting agent produces: a patch, a review, a
design. This decides what context is relevant.

**3. Budget.** Pick a tier and hold to it.

| Tier | Reads | Pack size | Fits |
|---|---|---|---|
| Micro | 1–3 files, ranges only | < 500 tokens | single-file edit, known location |
| Standard | 4–10 files, mostly ranges | 1.5–3k tokens | feature work, bug with a symptom |
| Deep | 10–25 files | ≤ 8k tokens | architecture review, cross-cutting refactor |

Needs more than Deep? The task is too big. Split it, one pack per piece.

## Phase 2 — Discover

### Pick the tool by the question

| Question | Tool |
|---|---|
| What does this repo/directory look like at a glance? | `tree` |
| Where does this *text* appear? | `rg` |
| What files *exist* with this name, extension, or age? | `fd` |
| Where does this *code shape* appear (calls, defs, JSX, imports)? | `ast-grep` |
| Does a value *reach* a sink (flow, not shape)? | `semgrep` taint mode |
| Something emitted JSON | `jq` |
| Something is YAML (config, CI, compose) | `yq` |
| I have <10 candidate files and need to understand them | the `Read` tool |
| I have >10, and the reading *is* the work | a subagent, briefed (below) |

Use `rg` for text and `ast-grep` for structure. Switch to `ast-grep` the moment a
regex would need to care about whitespace, line breaks, nesting, or balanced
parens.

Escalate to Semgrep for dataflow — it's the only tool here that answers whether a
value *reaches* something. It isn't one of the six: check `command -v semgrep`
only when a flow question arises, and log the question as open if it's absent.

Run `command -v tree fd rg ast-grep jq yq` once per session. If one is missing,
say so before falling back. Never silently degrade to `find`, `grep`, recursive
`ls`, or a regex rewrite.

Flags, `ast-grep` gotchas, taint rules, fallbacks: `references/tool-cookbook.md`.

### Climb the ladder

Each rung costs ~10× the one below. Exhaust a rung before going up.

1. **Structure** — `tree -L 2 -I '.git|node_modules|build|dist|target'` on any
   unfamiliar directory. The shape of the world for ~200 tokens.
2. **Paths** — `fd` on names: `**/*repository*`, `**/*.config.*`. Names encode
   intent; use them before content.
3. **Content** — `rg -l` / `rg -c` for *which* and *how many* files before
   printing any match bodies, then `rg -n -C3`. 400 hits means narrow
   (`-t ts`, `-g`, `-w`), not read 400 hits.
4. **Structure confirmation** — `ast-grep` when the pattern is code-shaped.
5. **Ranged reads** — the 40 lines around the hit, not the 900-line file.
6. **Full reads** — only files that are both small and central: interfaces,
   configs, schemas, the one class the task is about.
7. **Delegated reads** — when rungs 5–6 would pull more than ~10 files into
   *this* window, send the reading to a subagent instead. Its reads cost its
   context, not yours; what comes back is a page of anchors.

Rung 7 is the one rung that doesn't cost 10× the one below — it costs a cold
start. A subagent re-derives what you already know, so it loses on anything
small. Delegate when the read volume is real: give it your Phase 1 questions
verbatim, name the tier, and require the Findings format from Phase 4 —
`[verified] claim — path:line`. A subagent asked to "look into" something
returns prose you then have to verify, which is worse than reading it yourself.

### Seed

- **Task names a symbol, error string, endpoint, or file** → anchor-out. Search
  the exact string, land on it, expand through callers and callees. This is the
  default; it beats browsing.
- **Task is vague, or the domain is unfamiliar** → top-down. README, entry point,
  config, directory structure. Build a map, then anchor.

Follow references one hop at a time. From an anchor, take the interface it
implements, its direct caller, and its configuration. Not its tests, not its
siblings, not the whole package.

### Java, Kotlin, Python, TS/JS: run the bundled script instead of rungs 2–4

One command does the whole narrowing pass: `search-java-sources.py` (`.java`),
`search-kotlin-sources.py` (`.kt`/`.kts`), `search-python-sources.py`
(`.py`/`.pyi`), `search-ts-sources.py` (`.ts`/`.tsx`/`.js`/`.jsx`). Not on
`PATH` — invoke by path:

```bash
SKILL_DIR="${CLAUDE_SKILL_DIR:-$HOME/.claude/skills/context-builder}"
"$SKILL_DIR/scripts/bootstrap.sh"                          # once per machine
"$SKILL_DIR/scripts/search-ts-sources.py" <keyword>... [root]   # or -python-, -java-, -kotlin-
```

You get a reading list tiered **READ FIRST / THEN / SKIM IF NEEDED**, each row
carrying its matched line, mention count, and the relation that pulled it in.
Read top-down, stop when the question is answered. Keep the tiers and evidence
columns when you report a run — they're what makes stopping early safe.

Matching is fuzzy against the repo's own vocabulary, so a guessed or misspelled
name still lands. Use it instead of spending a round on synonyms.

On `tree_sitter is required by this script`, run `scripts/bootstrap.sh` and
retry. Don't fall back to the ladder. For other languages, use the ladder.

Output format, evidence columns, `--all` / `--from-file` / `--depth` and the rest
of the flags, troubleshooting: `references/reading-list-scripts.md`.

## Phase 3 — Reflect

Don't skip this. Skipping it is why packs come out both incomplete and bloated.

After every discovery round, write a ledger:

| # | Question | Status | Evidence |
|---|---|---|---|
| 1 | Where is the token validated? | answered | `auth/Filter.kt:88` |
| 2 | What happens on expiry? | partial | refresh path exists, unread |
| 3 | Existing retry logic? | open | — |

Then ask three things:

1. **Which questions are still open, and is there a specific search that closes
   them?** Run it. If you can't name the search, more reading won't help.
2. **What did this round newly expose?** Chase a new identifier only if a live
   question depends on it. Curiosity is how packs reach 40k tokens.
3. **Am I saturating?** A round that changed no statuses means further reads in
   that direction are dead weight. Change direction or stop.

### Three rounds per open question

A round is one hypothesis, however many commands it takes.

1. The user's exact vocabulary — `rg -lw 'theirTerm'`.
2. Loosened — drop `-w`, add `-i`, add `-u` (the file may be `.gitignore`d),
   widen the glob.
3. Structural or synonymous — `ast-grep` for the shape, or the two or three names
   the codebase would plausibly use instead.

No candidate file set after round 3? **Stop.** Don't start a fourth round with a
fourth synonym. Take one of two exits:

- **The user can resolve it** → ask, carrying the search.
- **They can't, or it isn't blocking** → log it under Open questions.

An open question is a legitimate output. "I could not determine X; the likely
place is Y" beats a confident guess and costs almost nothing. Never fill a gap
with plausible invention.

### Stop when

Whichever comes first: all questions answered, a round changed no statuses, or
the tier budget is spent. Stop early, before the budget is spent, when:

- **The term returns zero hits anywhere**, including `-i -u`. The word doesn't
  exist in this repo. No further searching invents the mapping.
- **Two readings both have real hits.** That's ambiguity, not a search problem.
- **Narrowing twice still leaves 100+ hits.** Ask which subsystem, not which regex.
- **The answer depends on intent that isn't in the code** — which design they
  want, whether a behavior is a bug or deliberate.

### When the window is already tight

Context pressure changes what to do next, not just how much of it to do.

- **Shape early.** Write the pack now, from what you have. A pack survives
  compaction; scrollback doesn't.
- **Re-anchor, don't re-read.** After a compaction the pack *is* your context.
  Cite it. Re-opening a file you already summarized pays for it twice.
- **Delegate what's left** (rung 7), with the pack as the subagent's brief.
- **Never spend the last of the window on discovery.** Leave enough room to act,
  or you finish with perfect context and no budget to use it.

### Ask a question that carries the search

Never ask a bare "can you clarify?" — it throws away what you learned and makes
the user do the work twice. State what you looked for, what you found, and offer
the specific choice:

> `rg -lw 'sessionToken'` finds nothing. The closest things are `authToken` in
> [auth/session.ts:18](auth/session.ts#L18) and `refreshToken` in
> [auth/refresh.ts:40](auth/refresh.ts#L40). Which is the one that's expiring early?

That's answerable in three words. "Where is the session code?" is not.

Don't ask before running round 1 — most questions die there. Don't ask what's
derivable from what you've read. Don't ask a routine judgment call a colleague
would just make and mention: make it, say so, move on.

## Phase 4 — Shape

Write a briefing, not an archive.

- **Anchor, don't excerpt.** `auth/Filter.kt:88 — validates JWT, throws on
  expiry` beats pasting the method. Paste code only where the agent must
  reproduce exact syntax: signatures, schemas, config keys, error strings. Write
  anchors as `path/to/file.ts:42` — they're clickable.
- **Label confidence.** Mark every claim `verified` (you read it), `inferred`
  (deduced from naming or structure), or `assumed`. Unlabelled inference is how
  hallucination gets downstream.
- **Position deliberately.** Attention is strongest at start and end. Task and
  constraints first, bulk in the middle, next action last.
- **Order for cache reuse.** For a pack reused across turns, put stable material
  (conventions, schemas, map) first and volatile material (current task, latest
  findings) last. A stable prefix is a cacheable prefix.

### Standard pack template

```markdown
# Context: <task>

## Objective
<one or two sentences: what the acting agent must produce>

## Constraints
<conventions, versions, style rules, things that must not break>

## Map
<path> — <one line: what it does, why it's here>

## Findings
- [verified] <claim> — `path:line`
- [inferred] <claim> — basis: <what you're inferring from>

## Excerpts
<only where exact syntax matters; smallest span that carries the meaning>

## Open questions
- <what's unknown> — <where it probably lives>

## Not included
<what you searched and deliberately left out, so nobody re-searches it>

## Next action
<the single first step>
```

Drop empty sections; don't write "N/A". For Micro tier, Objective + Findings +
Next action is the whole pack. Run the self-check in
`references/pack-templates.md` before handing off.

## Anti-patterns

| Instead of | Do this |
|---|---|
| Reading files to "get oriented" | Search for the task's own words first |
| Reading the whole file | Read the range the hit is in |
| Reading tests to learn the API | Read the interface; tests are 5× the tokens |
| Printing match bodies on the first pass | `rg -l` / `rg -c` to size the blast radius |
| A fourth synonym after three rounds | Stop: ask, or log it as an open question |
| Pasting large excerpts | Anchor + one-line claim |
| Chasing every new identifier | Chase only what an open question depends on |
| Pulling 20 files into this window yourself | Delegate the reading, take back anchors |
| Investigating an adjacent problem you spotted | One line at the end, after the answer |
| Regexing toward a dataflow answer | A Semgrep taint rule, or log it open |
| Silently guessing a gap | Log it under Open questions |
| One pack for a sprawling task | Split the task, one pack each |
| Re-reading a file already in context | Cite what you already have |

## Where to go next

Open only the row that matches what you're doing.

| Scenario | Open |
|---|---|
| Empty search; a long pipeline to retype; per-language `ast-grep` patterns; a Semgrep taint rule; a missing tool's fallback | `references/tool-cookbook.md` |
| Tracing a value backward or forward; finding config, an error's origin, a convention, or a change's blast radius; unfamiliar repo with no anchor; per-command costs | `references/discovery-recipes.md` |
| Interpreting, narrowing, or troubleshooting a reading-list script run | `references/reading-list-scripts.md` |
| Delta, handoff, review, or working-set packs; the hand-off self-check; producing a pack section with a tool instead of by reading | `references/pack-templates.md` |

`prompts/` holds five ready-to-send prompts that drive this loop — each fixes the
tier, states what discovery must answer, and names the result file. Offer the
matching one: `01-before-debugging`, `02-before-planning-implementing`,
`03-before-reviewing-code`, `04-before-refactoring`, `05-for-documentation`.

Never read a pager-backed command's output into context. Use `Read` for files,
and disable paging explicitly (`git --no-pager diff`) or it blocks forever.
