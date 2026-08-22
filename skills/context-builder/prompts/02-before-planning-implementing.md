# Before planning and implementing

The "am I duplicating something" question is the one that pays for this prompt
outright — it is also the one that never gets asked without prompting.

---

Use context-builder, then plan. Don't write code in this turn.

Goal: <the change, in one sentence>
Constraints I already know: <versions, what must not break, style rules>

Discovery must answer at minimum:
- Is there existing code that already does this, that I would be duplicating?
- What is the convention here for <the kind of thing being added>?
  Look at three similar files, not one — one file might be the outlier.
  On a JVM repo, check for Konsist or ArchUnit tests first — if the conventions
  are already enforced as tests, those are the answer, and my change keeps them
  green.
- What is the blast radius of touching <symbol or module>?
- Where do the tests for this area live?

Budget: Standard. Raise to Deep only if the blast radius count justifies it.

Give me back a standard pack — Objective, Constraints, Map, Findings with
`path:line`, Open questions, Not included — and then a numbered plan where
every step cites an anchor from the Findings. Flag any step that rests on an
[inferred] claim rather than a [verified] one.

---

**Write both to `plan-result.md`** — the pack first, the plan after it. That file
is what I'll hand back to you when we start implementing, so it has to stand
alone without this conversation.
