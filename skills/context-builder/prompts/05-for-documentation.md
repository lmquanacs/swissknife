# For documentation

The only one that starts top-down rather than anchor-out, because there is no
anchor. The `[inferred]` labels matter more here than anywhere else — an inferred
claim that ships in a doc becomes something the next person trusts.

---

Use context-builder with top-down seeding — I have no anchor.

Document: <what — a module, a service, the public API, onboarding for <area>>
Audience: <new teammate / API consumer / future me>
Length target: <e.g. one page>

Orient first with the unfamiliar-repo sequence — tree, the directory histogram,
the manifest through jq/yq, recently-changed files — then anchor out from the
entry point. Budget: Deep, but stop early if the map converges sooner.

Read interfaces, types, and configs. Skip tests and implementation bodies unless
a behavior is documented nowhere else. Exception on JVM repos: Konsist or
ArchUnit tests are the architecture written as code — read them, and cite them
`[verified]`, because they're enforced rather than observed.

Give me back:
- a Map: `path` plus one line each, for every component that earns a mention
- the public surface: exact signatures pulled with ast-grep, not paraphrased
- configuration: the precedence order (default -> file -> env -> flag), which
  matters more than any individual value
- every claim labelled [verified] or [inferred] — I won't ship an [inferred]
  claim without checking it myself
- Open questions: what the code does not explain about itself

Then draft the doc, with every factual claim traceable to an anchor above.

---

**Write two files**, because they have different lifespans:

- `<topic>.md` — the doc itself, clean prose, no labels, ready to ship.
- `doc-result.md` — the evidence behind it: the map, the anchors, and every
  `[inferred]` claim I still need to verify before publishing. Delete this one
  once the doc is checked; keep it until then.
