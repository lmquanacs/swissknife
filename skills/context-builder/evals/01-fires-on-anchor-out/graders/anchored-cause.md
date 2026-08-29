Pass only if all of these hold.

1. The answer names nested retry as the cause: `chargeCustomer` retries three
   times and each attempt calls `fetchWithRetry`, which itself retries three
   times — 3 x 3 = 9 charges.
2. Both halves are cited with a `path:line` anchor, one in
   `src/billing/charge.ts` and one in `src/http/client.ts`.
3. The attempt counts are traced to `defaultRetryPolicy` in
   `src/config/settings.ts` rather than asserted from memory.
4. No fix was written — the prompt asked for cause and evidence only.

Fail if the cause is stated without anchors, if only one of the two retry loops
is found, or if the response opens files that no part of the answer depends on
(`refund.ts` and `package.json` are the give-aways here).
