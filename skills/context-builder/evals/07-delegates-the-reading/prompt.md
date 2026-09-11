This repo is at `fixtures/warehouse`.

Something is decrementing a SKU's on-hand count twice for a single order, so
stock goes negative under normal traffic. Audit every path that can mutate stock
and tell me which combination double-counts.

Do the reading with a subagent rather than opening the files yourself. Report the
cause with evidence.
