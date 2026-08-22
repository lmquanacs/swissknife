# Before reviewing code

A diff read in isolation is the main source of confident-but-wrong review
findings. This ordering forces the caller context to be gathered before any
judgement is allowed.

Comments come out in [Conventional Comments](https://conventionalcomments.org/)
format — `<label> [decorations]: <subject>`. The label does real work beyond
tidiness: it forces a decision about *what kind* of feedback each comment is,
and `question:` gives uncertainty somewhere to go that isn't a padded `issue:`.
Blocking versus non-blocking stops being tone the author has to infer, and the
output stays greppable.

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
3. Only then the review comments, in Conventional Comments format
4. Open questions — things that need the author, not more searching

Format every comment as:

    <label> [decorations]: <subject>

    [discussion]

- **label** — one of `praise`, `nitpick`, `suggestion`, `issue`, `todo`,
  `question`, `thought`, `chore`, `note`. Use `typo`, `polish` or `quibble` if
  one of them fits better.
- **subject** — the point itself, one line.
- **decorations** — parenthesised, comma-separated. Always carry `(blocking)` or
  `(non-blocking)`; add a topic decoration such as `(security)`, `(test)`,
  `(perf)`, `(ux)` where it classifies further. Keep the list short — a comment
  wearing four decorations has stopped being readable.
- **discussion** — optional, but required on anything `(blocking)`: the why, and
  what resolving it looks like.

Label rules I care about:
- `issue:` — only for a problem you can state as a concrete failure: specific
  input or state -> wrong output. Pair it with a `suggestion:` for the fix.
- `question:` — when you suspect a problem but cannot demonstrate it. Do not
  promote a suspicion to `issue:` to make it land harder; that is how reviews
  lose credibility.
- `nitpick:`, `thought:`, `note:` — non-blocking by nature. Never mark them blocking.
- `praise:` — at least one, and only where it is sincere. Skip it rather than
  manufacture it.
- Every comment carries a `path:line` anchor.

Ground each comment in something you actually read. If a claim rests on
inference rather than a file you opened, it is a `question:`, not an `issue:`.

---

**Write the review to `review-result.md`**, in this structure:

- **Summary** — what changed, one paragraph, no opinions
- **Context** — the anchors gathered in step 2
- **Blocking** — every `(blocking)` comment, most severe first
- **Non-blocking** — the rest, grouped by label
- **Open questions** — what needs the author rather than more searching

If nothing blocking survived verification, say so plainly at the top rather than
promoting a nitpick to fill the space.
