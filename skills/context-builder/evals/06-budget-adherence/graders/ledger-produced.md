Phase 3 calls the ledger the step whose absence makes packs "both incomplete and
bloated", and no other case grades it.

Pass if the response shows its questions carrying a resolution status — a
ledger table, or per-question lines marked answered / partial / open — with
evidence attached to the answered ones as `path:line`.

An explicit open question is a pass, not a gap: whether retry config is meant to
be per-endpoint at the call site or in `RetryPolicy` itself is not decidable
from this fixture, and saying so beats inventing a design.

Fail if the response reports findings with no notion of which of its own
questions remain unresolved, or if it never framed questions to begin with and
went straight to reading.
