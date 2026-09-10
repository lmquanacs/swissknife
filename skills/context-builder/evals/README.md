# Evals for context-builder

Scoring this skill has two halves. The mechanical half — description length,
SKILL.md token weight, whether every shipped file is reachable, whether the
documented commands run — is `../scripts/score-skill.sh`, which exits
non-zero on the first thing it finds, so it can gate a commit. This
directory is the other half: does the skill actually change what the agent does,
and for the better.

## Running

```bash
claude plugin eval .            # from the skill directory
claude plugin eval . --case '04-*' --runs 5
```

`--ablation with-without` is the default whenever the plugin resolves, so each
case runs twice — once with the skill available, once without — and the number
that matters is the **delta**, not the absolute score. A case where both arms
score the same is a case where the skill earned nothing.

Graders under a case's `graders/` are markdown: each states its pass condition
and an LLM judge (haiku by default, `--judge-model` to change) applies it.
`skill-fired.md` in case 01 is marked `with-only` and is an indicator rather
than part of the score — it only confirms the with-plugin arm actually loaded
the skill.

Two things to confirm on the first successful run, because neither could be
verified while writing this:

- `plugin eval` is gated behind early access; on an account without it the
  command exits with `plugin eval is currently in early access` and nothing
  runs. Everything here is inert until that clears.
- The alternative case form is `case.yaml` (it carries `runs`, tags, timeouts
  and `scaffold_script`). These cases use the `prompt.md` + `graders/*.md` form
  instead, which needs no schema. If the frontmatter keys in
  `01-fires-on-anchor-out/graders/skill-fired.md` are rejected, that grader is
  the only thing to fix.

## The cases, and what each one is protecting

| Case | Failure it catches |
|---|---|
| `01-fires-on-anchor-out` | The skill doesn't trigger on the bug hunt it was written for, or it finds one retry loop and stops |
| `02-stays-quiet-when-not-needed` | Over-triggering. A named file and a named edit should not buy a discovery round — a skill that fires here costs more than it saves |
| `03-pack-fidelity` | Packs without `path:line` anchors or confidence labels, and packs that archive instead of brief |
| `04-stop-discipline` | The hardest instruction in the skill to make stick: stop after three rounds and ask a question that carries the search, rather than inventing the thing the user named |
| `05-reading-list-script` | The bundled scripts being documented but not runnable — this case fails if `bootstrap.sh` isn't run when the parsers are missing |
| `06-budget-adherence` | The two phases nothing else grades: a budget tier stated but not held, and findings reported with no ledger saying which questions are still open |

01 and 02 are a pair: a description tuned until 01 passes will eventually break
02. Read their scores together, never one alone.

02 and 06 are the other pair, and they pull in opposite directions: 02 fails an
agent that spends a discovery round it didn't need, 06 fails one that reads
everything rather than bounding the job. A change that improves either one at
the other's expense has moved the problem, not fixed it.

## Fixture

`fixtures/shop` is a four-file TypeScript repo with one planted bug — a retry
loop in `billing/charge.ts` nested inside `fetchWithRetry`'s own retry loop, so
three attempts become nine charges. It is deliberately small enough that a
brute-force "read everything" run also gets the right answer on case 01. That is
the point: on a fixture this size the with/without delta shows up in the token
count and the anchor discipline, not in whether the answer was reachable.

`fixtures/shop` contains no circuit breaker, which is what case 04 tests.

`fixtures/inventory` is the Python counterpart used by case 06 and by
`score-skill.sh`: nine sources under `src/inventory` with the same planted
double-retry bug, plus the two edges a regex cannot produce — `ChargeProcessor`
subclassing `BaseProcessor` across a relative import, and the `@audited`
decorator. A query that silently stops matching would still print a plausible
reading list, so those two edges are asserted by name in the mechanical score.
