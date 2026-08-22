# Before refactoring

Refactors fail on the sites nobody found. Counting first is what stops the
budget being spent reading site 4 of 60, and `ast-grep` is non-negotiable here —
a regex misses calls split across lines and matches them inside comments.

---

Use context-builder before any edit.

Refactor: <from X to Y — rename, extract, change a signature, replace a pattern>
Scope: <whole repo / this package / these paths>

Count before you read:
- `rg -lw '<symbol>' | wc -l` for the blast radius as a number
- `rg -cw '<symbol>'` for where it concentrates
- `ast-grep` for the structural sites — not regex, so multi-line calls aren't
  missed and matches inside comments and strings aren't counted

If the blast radius is over ~15 files, stop and give me the number instead of
reading them all — that count is itself the finding. Budget: Deep.

Give me back:
- the blast radius count and the per-file concentration
- every site grouped by the kind of change it needs — mechanical / needs
  thought / ambiguous — each as a `path:line` anchor
- the interface or contract that pins the current shape, quoted exactly
- what test coverage already exists over the affected sites
- Open questions for any site you cannot classify

Then propose an edit order, safest first. Don't start editing.

---

**Write the plan to `refactor-result.md`** — the blast radius count, the three
site groups as a checklist I can tick through, and the edit order. Keep it
current as edits land, so the file doubles as the progress tracker.
