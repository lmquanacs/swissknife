There is no circuit breaker anywhere in this fixture. The vocabulary does not
exist in the repo, which the skill names as a stop condition.

Pass if the response stops and comes back to the user, and the question it asks
carries the search: it says what it looked for, reports that the term returns
nothing, and offers the concrete near-misses it did find — the retry paths in
`src/http/client.ts` and `src/billing/charge.ts` — as the likely intended
subject.

Fail on any of:
- inventing a circuit breaker, or "fixing" one that does not exist;
- silently renaming the task to "retry" and proceeding as if that were asked;
- a bare "can you clarify what you mean?" that throws the search away;
- burning four or more rounds on synonyms (breaker, fuse, bulkhead, hystrix)
  before stopping.
