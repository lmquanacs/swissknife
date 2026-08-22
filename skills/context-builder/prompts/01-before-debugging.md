# Before debugging

Keeps the investigation from starting at the fix. The key constraint is the last
line: a cause proposed before the ledger exists is a guess wearing evidence.

---

Use context-builder before touching anything.

Symptom: <what happens — paste the error verbatim if there is one>
Expected: <what should happen instead>
Repro: <steps, or the name of the failing test>
Where I think it lives (may be wrong): <path or subsystem, or "no idea">

Frame the questions first, then discover. Budget: Standard.
Search the literal error string before anything else, and walk the value
backward to its write site rather than forward from the symptom.
If the trail turns into "does this value actually reach that call", stop
regexing and write a throwaway `semgrep` taint rule scoped to the package. If
semgrep isn't installed, say so and leave that question open.

Give me back:
- a ledger: question / status / evidence
- findings as `path:line` anchors, each labelled [verified] or [inferred]
- the 2-3 most likely causes, ranked, each tied to a specific anchor
- anything you could not determine, under Open questions

Do not propose a fix in this turn, and do not open files that no question needs.

---

**Write the results to `debug-result.md`** — the ledger, the findings, and the
ranked causes. I want it on disk before we discuss the fix, so the investigation
survives a `/clear` and the fix turn starts from evidence rather than memory.
