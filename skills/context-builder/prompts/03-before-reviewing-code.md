# Before reviewing code

A diff read in isolation is the main source of confident-but-wrong review
findings. This ordering forces the caller context to be gathered before any
judgement is allowed.

---

Use context-builder in review-pack mode: evidence before judgement.

Under review: <PR number, branch, or `git diff main...HEAD`>
What it claims to do: <the PR description, one line>
I care most about: <correctness / performance / security / API surface / all>

Start from `git diff --stat` and `git log --oneline -8` on the touched paths.
For every changed file, find its callers before judging the change. Budget: Standard.

Give me back, in this order:
1. What changed — the diff summarized, no opinions yet
2. The context each change lands in — `path:line` anchors for callers,
   contracts, and the existing conventions the change should be matching
3. Only then, findings: each one [verified], anchored, and stated as a
   concrete failure scenario (input -> wrong output). No speculative findings.
4. Open questions — things that need the author, not more searching.

---

**Write the review to `review-result.md`** — all four sections above, findings
ordered most-severe first, every one carrying a `path:line` anchor. If nothing
survived verification, say so explicitly rather than padding the file.
