Score the pack against the skill's own Phase 4 rules.

Pass requires all of:
1. Sections present and non-empty: Objective, Map, Findings, Next action.
   Empty sections should be dropped rather than filled with "N/A".
2. Every specific claim in Findings carries a `path:line` anchor.
3. Every claim is labelled `[verified]` or `[inferred]`, and `[inferred]` ones
   state what the inference rests on.
4. The pack names both retry sites (`http/client.ts`, `billing/charge.ts`) and
   the `RetryPolicy` contract in `config/settings.ts` — a pack that misses the
   second retry site has failed the task it was written for.
5. Excerpts, if any, are the smallest span that carries the meaning. Pasting a
   whole file fails.

The pack is a briefing, not an archive: a correct-but-bloated pack that reprints
the source of all four files should not pass.
