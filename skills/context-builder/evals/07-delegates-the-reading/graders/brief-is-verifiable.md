Rung 7 is the only rung that does not cost 10x the one below — it costs a cold
start — so the whole of its value is in the brief. SKILL.md names three things a
delegated read must carry, and this grader checks all three reached the subagent.

Pass requires all of:

1. **Questions, not a topic.** The subagent's prompt carries specific context
   questions with checkable answers — "which functions call `adjustStock` on the
   order path?", "does `reserveStock` decrement as well as hold?" — rather than
   "look into the stock bug" or "investigate `fixtures/warehouse`". A brief that
   delegates the framing along with the reading has skipped Phase 1.

2. **A named bound.** The brief states a tier, a file count, or an explicit list
   of files. A subagent turned loose on 30 files with no bound reproduces the
   cost the rung exists to avoid, one window over.

3. **The Findings format, required explicitly.** The brief asks for
   `[verified] <claim> — path:line`, or names the confidence labels and the
   anchor form. Asking a subagent to "summarise what you find" fails this: prose
   comes back unverifiable, and checking it costs more than reading the files
   would have.

And the answer itself must hold:

4. The cause names both decrements — `reserveStock` decrementing under
   `decrementOnReserve` in `src/inventory/reserve.ts`, and `onOrderPlaced`
   calling `adjustStock` again in `src/events/handlers.ts` — traced to
   `defaultReservationPolicy` in `src/config/limits.ts` rather than asserted.
   Both are cited with `path:line` anchors.

Fail on any of:

- the main agent re-reading files the subagent already anchored. Phase 3 is
  explicit: re-anchor, don't re-read — a file summarised and then reopened is
  paid for twice, and the delegation bought nothing;
- findings relayed without anchors or confidence labels, so the saving in tokens
  was taken as a loss in verifiability;
- naming only one of the two decrements. `admin/backfill.ts` and
  `reporting/stocklevels.ts` also touch `adjustStock` and are the give-aways for
  an audit that swept the repo without following the order path.
