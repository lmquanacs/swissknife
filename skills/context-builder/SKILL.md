---
name: context-builder
description: Systematically discover, verify, and shape the minimum context needed before acting on a task. Use whenever you must understand code, repos, docs, or file sets you haven't read yet — "where is X defined", "how does Y work", bug hunts, tracing call sites and data flow, cross-file refactors, auditing a pattern across many files, deciding which files to read first, code review, or writing a brief or handoff for another agent. Covers search-tool craft (rg, fd, ast-grep, jq, yq, tree, plus Semgrep taint mode for dataflow questions) and ships reading-list scripts that turn a keyword into a ranked file list for Java/Kotlin and TypeScript/JavaScript repos. Also use when the user mentions context engineering, context window, token budget, prompt caching, cost per task, taint or dataflow tracking, or architecture-rule tests (Konsist, ArchUnit), or asks why an agent's answer was wrong or expensive. Prefer this skill over ad-hoc file reading any time a task touches more than two files — opening files to "get oriented" is exactly what it exists to replace.
---

# Context Builder

Most bad agent output is not a reasoning failure. It is a context failure: the
model was missing the one file that mattered, or it was buried under forty
files that didn't. Both problems are solved in the same place — before the work
starts.

This skill is a four-phase loop:

**Frame → Discover → Reflect → Shape**

Frame turns the task into answerable questions. Discover finds evidence
cheaply. Reflect decides whether to keep going or stop. Shape packs what was
found into a form the acting agent can actually use.

## The economics (why this works)

Two costs, not one:

- **Token cost** — what you pay to put bytes in the window.
- **Dilution cost** — every irrelevant token makes the relevant ones harder to
  attend to. A 50k-token context of mostly-noise performs *worse* than a
  3k-token context of signal, at 15× the price.

Searching is cheap; reading is expensive. A `rg` over a repo costs a few hundred
tokens and tells you which of 400 files matter. Reading four wrong files costs
20k and tells you nothing. **Always spend more on search than you think you
need, so you can spend less on reads.**

The trap is that no single search is expensive, so a stalled investigation
doesn't announce itself — it just keeps producing plausible next commands.
Twenty of them cost more than the question you should have asked after the
third. That is what Phase 3 is for.

## Phase 1 — Frame

Do not open a single file yet. Write down:

1. **Context questions** — the specific things you must know to be correct.
   Phrase them as questions with checkable answers, not topics.
   - Bad: "understand the auth system"
   - Good: "Where is the session token validated?" / "What happens on expiry?"
     / "Is there existing retry logic I'd be duplicating?"
2. **Success shape** — what the acting agent produces (a patch? a review? a
   design?). This decides what context is even relevant.
3. **Budget** — pick a tier and hold to it:

   | Tier | Reads | Target pack size | Fits |
   |---|---|---|---|
   | Micro | 1–3 files, ranges only | < 500 tokens | single-file edit, known location |
   | Standard | 4–10 files, mostly ranges | 1.5–3k tokens | feature work, bug with a symptom |
   | Deep | 10–25 files | ≤ 8k tokens | architecture review, cross-cutting refactor |

   If a task seems to need more than Deep, the task is too big. Split it and
   build one pack per piece — that is almost always cheaper and more accurate
   than one giant pack.

Three to seven questions is the normal range. If you can't name a question,
you don't yet know what you're looking for, and any file you open is a guess.

## Phase 2 — Discover

### Pick the tool by what you're asking

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

Rule of thumb: **`rg` for text, `ast-grep` for structure.** Reach for `ast-grep`
the moment a regex would need to care about whitespace, line breaks, nesting, or
balanced parens — those are exactly the cases regex gets wrong. Never fall back
to `find`, `grep`, recursive `ls`, or regex-based source rewrites when the
purpose-built tool applies. Check availability once per session with
`command -v tree fd rg ast-grep jq yq`, and **if a preferred tool is missing, say
so before falling back** rather than silently degrading to a noisier command.

**Semgrep is the escalation above both** — the only tool here with dataflow, for
whether a value *reaches* something rather than where a shape appears. It isn't
one of the six: check `command -v semgrep` only when a flow question comes up,
and log that question as open if it's absent.

`references/tool-cookbook.md` has the flags, the per-language `ast-grep` gotchas,
Semgrep taint mode, the fallback table, and the `.scripts/` convention for
searches you'll re-run. Open it when a search comes back empty or you're about
to retype a long pipeline.

### Climb the ladder

Each rung is roughly 10× the cost of the one below it, so only go up when the
rung below has been exhausted.

1. **Structure** — `tree -L 2 -I '.git|node_modules|build|dist|target'` the first
   time you touch an unfamiliar directory. The shape of the world for ~200 tokens,
   and it tells you where the code even lives.
2. **Paths** — `fd` on names. `**/*repository*`, `**/*.config.*`. Names encode
   intent; use them before content.
3. **Content search** — `rg -l` / `rg -c` to see *which* and *how many* files are
   involved before printing any match bodies, then `rg -n -C3` to see where and
   roughly what. A search returning 400 hits is a signal to narrow (type filters
   `-t ts`, globs `-g`, word boundaries `-w`), not to read 400 hits. Often this
   alone answers a question with no file read at all.
4. **Structural confirmation** — `ast-grep` when the pattern is code-shaped.
5. **Ranged reads** — read the 40 lines around the hit, not the 900-line file.
6. **Full reads** — reserved for files that are both small and central
   (interfaces, configs, schemas, the one class the task is about).

### Seed strategy

Pick based on what the task gives you:

- Task names a symbol, error string, endpoint, or file → **anchor-out**. Search
  the exact string, land on it, expand outward through callers and callees.
  This is the default and it is much cheaper than browsing.
- Task is vague or the domain is unfamiliar → **top-down**. README, entry
  point, config, directory structure — build a map first, then anchor.

**Follow references one hop at a time.** From a landed anchor, the useful
neighbours are: the interface/type it implements, its direct caller, and its
configuration. Not its tests, not its siblings, not the whole package.

### JVM and TypeScript: run the bundled script instead of rungs 2–4

Two bundled scripts do the whole narrowing pass in one command —
`search-jvm-sources.py` for `.java`/`.kt`/`.kts`, `search-ts-sources.py` for
`.ts`/`.tsx`/`.js`/`.jsx`. Same CLI, same flags, same output. They are not on
`PATH`; invoke them from this skill's own directory:

```bash
${CLAUDE_SKILL_DIR}/scripts/search-jvm-sources.py <keyword>... [root]
${CLAUDE_SKILL_DIR}/scripts/search-ts-sources.py <keyword>... [root]
```

The output is a reading list, at most 200 files, numbered in reading order and
tiered **READ FIRST / THEN / SKIM IF NEEDED** by how each file was found. Every
tier carries a line count, so you know what you're signing up for. Read
top-down and stop when the question is answered — the tiers are what make
stopping early safe.

Every row carries its own evidence, so most files can be triaged without being
opened. Each shows the matched source line, a mention count, the file's test
attached to its subject, and the relation that pulled it in — `implements X`,
`calls X`, `renders X`. Subtyping and calls outrank a bare mention, so "who
implements this interface" answers itself. A `changed with` row means git
history keeps moving the two files together, which is what finds the migration
or config no type reference points at. `--json` carries all of it per file.

Three properties make this the right first move rather than a fallback:

- **It is fuzzy, so a wrong guess still lands.** Matching runs against the repo's
  own vocabulary, so `srvconfig` finds `ServerConfig` (0.86) and `McpServelt`
  finds `McpServlet` (0.93). Use this **instead of spending a round on
  synonyms** — one run tells you whether the name you're guessing at exists in
  another spelling. When nothing clears the bar, the error names the closest
  identifiers in the repo, which answers the vocabulary question directly.
- **Several keywords narrow better than one.** `auth retry --all` keeps only
  files carrying both and ranks by the weaker one — "where do X and Y meet" in
  one run. An empty `--all` run reports per-keyword counts, which is the answer,
  not a failure.
- **`--from-file PATH` starts from a file instead of a guess** — callers,
  collaborators, tests, co-changes. Combines with a keyword or stands alone.

Flags worth knowing: `--depth 0` for direct hits only, `--no-tests`, `-n` to
shorten, `--fuzzy` to move the similarity bar (0.8 default), `--no-git` /
`--no-evidence` / `--no-cache` to cut passes. For anything that isn't JVM or
TS/JS, use the ladder above.

## Phase 3 — Reflect

**This is the phase that gets skipped, and skipping it is why packs are both
incomplete and bloated.** After every discovery round, stop and write a ledger:

| # | Question | Status | Evidence |
|---|---|---|---|
| 1 | Where is the token validated? | answered | `auth/Filter.kt:88` |
| 2 | What happens on expiry? | partial | refresh path exists, unread |
| 3 | Existing retry logic? | open | — |

Then ask three things:

1. **Which questions are still open, and is there a specific search that would
   close them?** If yes, run it. If you can't name the search, more reading
   won't help.
2. **What did this round newly expose?** New identifiers, a config key, an
   interface you didn't know existed. Chase one *only if a live question
   depends on it.* Curiosity is how packs get to 40k tokens.
3. **Am I saturating?** If a round changed no statuses, further reads in that
   direction are dead weight. Change direction or stop.

### Three rounds per open question

A round is one hypothesis, however many commands it takes to test. Escalate
deliberately rather than re-rolling the same idea:

1. The user's exact vocabulary — `rg -lw 'theirTerm'`.
2. Loosened — drop `-w`, add `-i`, add `-u` (the file may be `.gitignore`d),
   widen the glob.
3. Structural or synonymous — `ast-grep` for the code shape, or the two or three
   names the codebase would plausibly use instead.

If round 3 ends without a candidate file set, **stop.** Do not start a fourth
round with a fourth synonym. You have two legitimate exits, and which one you
take depends on whether a human can resolve it:

- **The user can resolve it** → ask, carrying the search (below).
- **They can't, or it isn't blocking** → log it under Open questions in the pack.

**An open question is a legitimate output.** "I could not determine X; the likely
place is Y" is far more useful than a confident guess, and it costs almost
nothing. Never fill a gap with plausible invention.

**Stop conditions for the whole phase** — whichever comes first: all questions
answered, a round produced no status change, or the budget tier is spent. Two to
three rounds is normal. Beyond three, you are usually re-reading things you
already understand.

### Stop before spending the budget when

- **The user's term returns zero hits anywhere**, including `-i -u`. Their word
  doesn't exist in this repo — that's a vocabulary mismatch, and no amount of
  additional searching invents the mapping.
- **Two readings both have real hits.** That's ambiguity, not a search problem;
  more searching cannot resolve which one they meant.
- **Narrowing twice still leaves 100+ hits.** The request is too broad to act on.
  Ask which subsystem, not which regex.
- **The answer depends on intent that isn't in the code** — which of two designs
  they want, whether a behavior is a bug or deliberate. Unknowable by search.

### Ask a question that carries the search

A bare "can you clarify?" throws away everything you learned and makes the user
do the work twice. State what you looked for, what you found, and offer the
specific choice:

> `rg -lw 'sessionToken'` finds nothing. The closest things are `authToken` in
> [auth/session.ts:18](auth/session.ts#L18) and `refreshToken` in
> [auth/refresh.ts:40](auth/refresh.ts#L40). Which is the one that's expiring early?

That's answerable in three words. "Where is the session code?" is not.

**Don't ask when** you haven't run a single search yet — spend at least round 1
first; most questions die there. Or when the answer is derivable from what
you've already read. Or when it's a routine judgment call a colleague would just
make and mention (naming, file placement, test location) — make it, say you made
it, move on.

### Stay inside the question

Adjacent problems you notice mid-search — a nearby bug, a dubious pattern, a
tempting refactor — get **mentioned in one line at the end**, not investigated.
Each detour costs another handful of files in context and pushes the actual
answer further away. Finish the asked question first.

## Phase 4 — Shape

A context pack is a **briefing, not an archive**. Four principles:

- **Anchors over excerpts.** `auth/Filter.kt:88 — validates JWT, throws on
  expiry` beats pasting the method. Paste code only when the agent must
  reproduce exact syntax: signatures, schemas, config keys, error strings.
  Report findings as `path/to/file.ts:42` — those are clickable.
- **Label confidence.** Mark each claim `verified` (you read it), `inferred`
  (deduced from naming/structure), or `assumed`. Unlabelled inference is how
  hallucination enters downstream.
- **Position deliberately.** Attention is strongest at the start and end. Task
  and constraints go first, the bulk in the middle, the immediate next action
  last.
- **Order for cache reuse.** If the pack will be reused across turns, put
  stable material (conventions, schemas, architecture map) at the front and
  volatile material (current task, latest findings) at the back. A stable
  prefix is a cacheable prefix.

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

Drop empty sections rather than writing "N/A". For Micro-tier tasks, Objective
+ Findings + Next action is the whole pack.

## Self-check before handing off

- Could someone act correctly on this pack without opening any other file?
- Is anything in here that the task doesn't depend on? Cut it.
- Is any claim stated flatly that I actually inferred?
- Does every specific claim carry a `path:line` anchor?
- Is the pack within its tier's budget?

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
| Investigating an adjacent problem you spotted | One line at the end, after the answer |
| Regexing toward a dataflow answer | A Semgrep taint rule, or log it open |
| Inferring JVM conventions from sample files | Read the Konsist/ArchUnit tests — they're enforced |
| Silently guessing a gap | Log it under Open questions |
| One pack for a sprawling task | Split the task, one pack each |
| Re-reading a file already in context | Cite what you already have |

## Reference files

- `references/tool-cookbook.md` — flags and recipes for `rg`, `fd`, `ast-grep`,
  `jq`, `yq`, `tree`; the four `ast-grep` gotchas and per-language patterns;
  Semgrep taint mode; the fallback table; and the `.scripts/` convention for
  reusable searches.
- `references/discovery-recipes.md` — Phase 2 search patterns by question type
  (data flow, config resolution, error origin, convention discovery), plus the
  files that are almost never worth reading and a cost reference.
- `references/pack-templates.md` — Phase 4 variants: delta packs (task already in
  progress), handoff packs (another agent or a fresh session), review packs
  (evidence-first, for critique), and working sets for long-running loops.

Don't read a pager-backed command's output into context — use the `Read` tool for
files, and disable paging explicitly (`git --no-pager diff`) or it blocks forever.
