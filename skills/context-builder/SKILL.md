---
name: context-builder
description: Systematically discover, verify, and shape the minimum context needed before acting on a task. Use this whenever a task requires understanding code, repos, docs, or file sets you haven't read yet — bug hunts, "how does X work", cross-file refactors, code review, writing a brief or handoff for another agent, or any request where you would otherwise start opening files at random. Also use when the user mentions context engineering, context window, token budget, prompt caching, cost per task, or asks why an agent's answer was wrong or expensive. Prefer this skill over ad-hoc file reading any time the task touches more than two files.
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

Searching is cheap; reading is expensive. A `grep` over a repo costs a few
hundred tokens and tells you which of 400 files matter. Reading four wrong
files costs 20k and tells you nothing. **Always spend more on search than you
think you need, so you can spend less on reads.**

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

Climb the ladder. Each rung is roughly 10× the cost of the one below it, so
only go up when the rung below has been exhausted.

1. **Structure** — `git ls-files`, `tree -L 2`, directory listing. Gives you
   the shape of the world for ~200 tokens.
2. **Paths** — glob on names. `**/*repository*`, `**/*.config.*`. Names encode
   intent; use them before content.
3. **Content search** — `rg -l <symbol>` to find *which* files, then `rg -n -C3`
   to see *where* and roughly what. Often this alone answers a question with no
   file read at all.
4. **Ranged reads** — read the 40 lines around the hit, not the 900-line file.
5. **Full reads** — reserved for files that are both small and central
   (interfaces, configs, schemas, the one class the task is about).

**Seed strategy.** Pick based on what the task gives you:

- Task names a symbol, error string, endpoint, or file → **anchor-out**. Grep
  the exact string, land on it, expand outward through callers and callees.
  This is the default and it is much cheaper than browsing.
- Task is vague or the domain is unfamiliar → **top-down**. README, entry
  point, config, directory structure — build a map first, then anchor.

**Follow references one hop at a time.** From a landed anchor, the useful
neighbours are: the interface/type it implements, its direct caller, and its
configuration. Not its tests, not its siblings, not the whole package.

See `references/discovery-recipes.md` for concrete search patterns by question
type (data flow, config resolution, error origin, convention discovery) and for
the list of files that are almost never worth reading.

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
   won't help — mark it as an open question in the pack instead.
2. **What did this round newly expose?** New identifiers, a config key, an
   interface you didn't know existed. Chase one *only if a live question
   depends on it.* Curiosity is how packs get to 40k tokens.
3. **Am I saturating?** If a round changed no statuses, further reads in that
   direction are dead weight. Change direction or stop.

**Stop conditions** — stop at whichever comes first:

- All questions answered.
- A round produced no status change (saturation).
- The budget tier is spent.

Two to three rounds is normal. Beyond three, you are usually re-reading things
you already understand.

**An open question is a legitimate output.** "I could not determine X; the
likely place is Y" is far more useful than a confident guess, and it costs
almost nothing. Never fill a gap with plausible invention.

## Phase 4 — Shape

A context pack is a **briefing, not an archive**. Four principles:

- **Anchors over excerpts.** `auth/Filter.kt:88 — validates JWT, throws on
  expiry` beats pasting the method. Paste code only when the agent must
  reproduce exact syntax: signatures, schemas, config keys, error strings.
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

`references/pack-templates.md` has the variants: **delta packs** (for a task
already in progress), **handoff packs** (for another agent or a fresh session),
and **review packs** (evidence-first, for critique tasks).

## Self-check before handing off

- Could someone act correctly on this pack without opening any other file?
- Is anything in here that the task doesn't depend on? Cut it.
- Is any claim stated flatly that I actually inferred?
- Does every specific claim carry a `path:line` anchor?
- Is the pack within its tier's budget?

## Anti-patterns

| Instead of | Do this |
|---|---|
| Reading files to "get oriented" | Grep for the task's own words first |
| Reading the whole file | Read the range the hit is in |
| Reading tests to learn the API | Read the interface; tests are 5× the tokens |
| Pasting large excerpts | Anchor + one-line claim |
| Chasing every new identifier | Chase only what an open question depends on |
| Silently guessing a gap | Log it under Open questions |
| One pack for a sprawling task | Split the task, one pack each |
| Re-reading a file already in context | Cite what you already have |
