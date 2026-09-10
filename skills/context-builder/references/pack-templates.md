# Pack Templates

Variants on the standard pack from SKILL.md Phase 4. Pick by who consumes the
pack and what they already know.

| Consumer | Template | Key property |
|---|---|---|
| You, continuing now | Delta pack | Only what changed |
| Another agent / fresh session | Handoff pack | Self-contained, no shared history |
| A reviewer or critic | Review pack | Evidence before judgement |
| A long-running loop | Working set | Stable prefix, volatile suffix |

---

## Filling the sections

Every section below is cheaper to produce with a tool than by reading. The
mapping, using the tools from `references/discovery-recipes.md`:

| Section | Produce it with |
|---|---|
| Map | `tree -L 2 -I '.git\|node_modules\|build\|dist\|target'`, one line trimmed per entry |
| Findings, Evidence | `rg -n` — the `path:line` prefix *is* the anchor |
| Constraints (versions, engines) | `jq -r '.engines, .dependencies' package.json`, `yq -p xml -oy '.project.properties' pom.xml` |
| Constraints (style rules) | `fd -H -d 2 -g '{.editorconfig,*eslintrc*,CONTRIBUTING*}'` |
| Excerpts | `ast-grep run -p '<shape>' -l <lang>` |
| Schemas and signatures | `ast-grep run -k interface_declaration -l <lang> <dir>` |
| What is under review | `git diff --stat`, `git log --oneline -8 -- <path>` |

`ast-grep` is the right tool for excerpts specifically because it returns a
whole syntactic node and nothing else — the exact minimal span the template
asks for. A ranged read guesses the boundaries and usually overshoots.

> Anchors are the pack's whole currency, so get them right: `ast-grep`'s
> human-readable output is 1-indexed and agrees with your editor, but
> `--json` line numbers are **0-indexed**. Cite the former, or add one to the
> latter.

---

## Delta pack

For a task already in progress. Everything already established stays out — this
is the diff, not the state.

```markdown
## Since last update
- <what changed, what was learned>

## Ledger
| # | Question | Status | Evidence |

## New findings
- [verified] <claim> — `path:line`

## Next action
<single step>
```

Rule: if it was in the previous pack and hasn't changed, it does not reappear.
Repetition across turns is pure token waste, and it dilutes what is new.

---

## Handoff pack

For an agent with no shared history. The test: can they act correctly having
read nothing but this?

```markdown
# <Task>

## Objective
## Constraints
<versions, conventions, hard limits, things that must not break>

## Map
<path> — <purpose>

## What is already true
- [verified] <established fact> — `path:line`

## What is not yet done
- <remaining work, in order>

## Excerpts
<exact signatures, schemas, config keys they must match>

## Traps
<the non-obvious things: a misleading name, a shadowed config, a false lead
already ruled out>

## Open questions
## Next action
```

Pull **Constraints** out of the manifest with `jq`/`yq` rather than from
memory — a version you half-remember is a constraint the receiving agent will
violate. Same for **Excerpts**: paste what `ast-grep` matched, not what you
recall the signature being.

**Traps** carries the most value per token in the template: it is the only part
the receiving agent cannot rediscover cheaply, because it encodes dead ends they
would otherwise walk into. A search that returned nothing belongs here too:
"`rg -lw 'sessionToken'` is empty; the concept is spelled `authToken`" saves the
next agent the same empty search.

---

## Review pack

For critique tasks. Evidence comes before judgement, so the reviewer forms
their own view rather than inheriting yours.

```markdown
## What is under review
## What it claims to do
## Evidence
- `path:line` — <observation, neutral phrasing>

## Relevant standards
<the conventions, spec, or requirements it should be judged against>

## Concerns
- <concern> — evidence: `path:line` — severity: <high|med|low>

## Verified as fine
<things you checked that are correct — stops the reviewer re-checking>
```

Keep observations and concerns separate. Mixing them means a wrong judgement
contaminates the evidence and the reviewer can't recover the raw facts.

Scope the section with `git diff --stat` before gathering evidence, so the
review covers the change rather than the file. For "does this pattern appear
elsewhere" claims, cite the count from `rg -cw` — a number is stronger evidence
than an assertion and costs less than the listing.

---

## Working set (long-running loops)

For agentic loops where the same context is resent each turn. Structure for
cache reuse: stable material first, volatile last.

```markdown
<!-- STABLE PREFIX — do not reorder or edit mid-task -->
## Mission
## Conventions and constraints
## Architecture map
## Schemas and signatures

<!-- VOLATILE SUFFIX — appended each turn -->
## Progress ledger
## Current step
```

Editing anything in the stable prefix invalidates every cached token after it,
so treat the prefix as frozen. Build it once — `tree` for the map, `ast-grep -k`
for the signatures — and let it run slightly over-complete. A prefix that
survives fifty turns beats a minimal one you keep amending.

When the volatile suffix grows past roughly a third of the pack, compress it:
fold settled findings up into the stable section as one-line facts, and clear
the turn-by-turn narration. The narration is scaffolding, not knowledge.

---

## Sizing

| Tier | Pack | Excerpts |
|---|---|---|
| Micro | Objective + Findings + Next action | none |
| Standard | Full template, sections dropped when empty | 1–3, short |
| Deep | Full template + Map + Traps | 3–8, still minimal |

If a pack exceeds its tier, cut in this order: excerpts → Map entries not
referenced by any finding → inferred claims that nothing depends on. Never cut
Open questions or Traps; they are the cheapest and least recoverable content in
the pack.

Prefer re-running a search over carrying its output. An anchor plus the command
that produced it (`rg -lw 'SymbolName'` — 14 files) costs a line; the listing
costs a hundred, and the consumer can regenerate it on demand.

---

## Self-check before handing off

Run this against any pack, in any variant above, before it leaves your hands.

- Could someone act correctly on this pack **without opening any other file**?
- Is anything in here that the task doesn't depend on? Cut it.
- Is any claim stated flatly that I actually inferred?
- Does every specific claim carry a `path:line` anchor?
- Are `[verified]` and `[inferred]` labels present, and does each `[inferred]`
  one say what the inference rests on?
- Is the pack within its tier's budget above?

The first and third questions catch the two failures that actually hurt
downstream: a pack that forces the consumer back into discovery, and an
inference that reads as a fact.
