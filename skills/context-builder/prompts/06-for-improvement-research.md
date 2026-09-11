# For improvement research

The other five start from something given — a symptom, a goal, a diff, a rename,
a topic. This one starts from an empty hand, which is exactly why it's the
easiest to answer with advice that was true before the repo was opened. The
constraint doing the work is the counting rule: a candidate you can't attach a
number and an anchor to is an opinion, and opinions don't get a row.

---

Use context-builder in survey mode. Don't edit anything this turn.

Area: <repo, package, or paths — narrow it; "the whole codebase" is not a scope>
Looking for: <duplication / dead code / test gaps / performance / error handling
  / API surface / correctness risk / all>
Already known bad: <so you don't spend the budget re-finding it>

Rank before you read. The sweep is cheap, so it goes wide; the reads are
expensive, so only the top handful of candidates earn one.

Ground every candidate in one of three countable signals, not in best practice:

- **Self-disagreement** — the repo does the same thing two ways. Count both
  (`rg -c`, or `ast-grep` once the pattern is code-shaped); the minority form is
  the candidate and the majority form is the target. "37 files do X, 3 do Y" is
  a finding. "Y is bad practice" is not.
- **Churn** — `git --no-pager log --format= --name-only -n 200 | sort | uniq -c |
  sort -rn | head -20`. Improvement pays off where the code keeps being touched.
  Keep `--no-pager` on every git command or it blocks forever.
- **Repetition** — the same shape in N places, found structurally with
  `ast-grep`. N is the finding.

If the area is Java, Kotlin, Python or TS/JS and the theme has a name — `retry`,
`cache`, `validate` — run the bundled reading-list script on it rather than
spending a round guessing synonyms.

Budget: Deep, breadth-first. Stop when a round surfaces no new candidate — not
when you run out of searches you could still run.

Give me back a table, best payoff first, every row carrying:

- the candidate, one line
- evidence: a count *and* a `path:line` anchor — no count, no row
- effort: the blast radius as a number, from `rg -lw '<symbol>' | wc -l`
- what it costs to leave alone — "nothing, it's just untidy" is a legitimate
  answer and I'd rather read it than a manufactured risk
- [verified] or [inferred], same as any pack

Then, below the table:

- **Not worth it** — what you found and are deliberately not proposing, one line
  each. Worth as much as the table: it stops the next pass re-finding them.
- **Open questions** — anything whose answer depends on intent rather than
  evidence: whether a behavior is deliberate, which of two designs is wanted.
  Mine to answer, not yours to guess.

Rules I'll hold you to:

- An [inferred] candidate ranks below every [verified] one, whatever its
  apparent payoff.
- If a finding would read identically against any repo in this language, cut it.
  That's the tell that it came from training rather than from this codebase.
- No fixes this turn. A ranked list I can choose from beats a patch I have to
  audit.

---

**Write the survey to `improvement-result.md`** — the table, the Not-worth-it
list, and the open questions. Put `git rev-parse --short HEAD` at the top:
rankings go stale as the code moves, and a row whose anchor no longer resolves
is one to re-verify, not to act on.
