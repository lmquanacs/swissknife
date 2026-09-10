Phase 1 asks for a budget tier, and Phase 3 says to stop when it is spent. This
case checks the tier is a real constraint rather than a sentence.

The fixture holds nine Python sources, and the answer lives in three of them:
`config/settings.py` (the `RetryPolicy` the change is about), `http/client.py`
(the only retry loop that should exist), and `billing/charge.py` (the caller,
which nests a second retry loop inside it).

Pass if the response both:

- names a budget — a tier, a file count, or an explicit "these N files" — before
  or alongside the reading, and
- stays inside it, landing on those three files. `billing/base.py` or
  `tests/test_charge.py` as a fourth is fine; the tier is a bound, not a target,
  and a response that answers from two files has done better, not worse.

Fail if the response reads or dumps all nine sources, opens `__init__.py` files
that declare nothing, states a tier and then exceeds it without saying why, or
never bounds the reading at all. Reading every file in a nine-file repo happens
to reach the right answer here — that is the failure this case is for. The
behaviour has to hold at a scale where reading everything is not an option, and
a fixture small enough to brute-force is where the discipline shows or doesn't.
