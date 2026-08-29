This case exercises the documented invocation end to end, including setup.

Pass if:
1. `search-ts-sources.py` was actually run — not simulated, not replaced with a
   hand-rolled `rg` pass.
2. If it first failed with `tree_sitter is required by this script`, the
   response ran `scripts/bootstrap.sh` and retried, exactly as SKILL.md says.
   Falling back to the search ladder instead is a fail: the skill documents
   bootstrap as the fix.
3. The reported reading list keeps the script's tiers and puts
   `src/config/settings.ts` at position 1 — the keyword `retrypolicy` is a
   fuzzy match for the `RetryPolicy` declared there. (On this fixture the
   script emits READ FIRST and THEN only; there is not enough here to fill a
   SKIM IF NEEDED tier, so its absence is not a failure.)
4. `src/http/client.ts` is in READ FIRST carrying its relation to
   `RetryPolicy`, and `billing/charge.ts` and `billing/refund.ts` are in the
   later tier, pulled in by the import edge rather than by a bare mention.

Fail if the script's output is paraphrased into a plain file list that drops the
tiers and the evidence columns, since those are what make stopping early safe.
