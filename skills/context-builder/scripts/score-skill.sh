#!/usr/bin/env bash
# Mechanical half of scoring this skill: everything checkable without a model.
# The other half — does the skill change what the agent does — is ../evals/.
#
# Exit 0 if every check passes, 1 otherwise, so it can gate a commit.
set -uo pipefail

skill="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$skill"
fails=0

check() { # check <label> <condition-exit-code> <detail>
  if [[ "$2" -eq 0 ]]; then
    printf '  ok    %-28s %s\n' "$1" "$3"
  else
    printf '  FAIL  %-28s %s\n' "$1" "$3"
    fails=$((fails + 1))
  fi
}

echo "context-builder — mechanical score"

# --- discoverability: the description is the whole trigger surface, and it is capped
desc_len=$(awk '/^description:/{sub(/^description: /,""); print}' SKILL.md | tr -d '\n' | wc -c | tr -d ' ')
[[ "$desc_len" -le 1024 ]]; check "description <= 1024 chars" $? "$desc_len chars, $((1024 - desc_len)) spare"

# --- weight: the body loads in full every time the skill fires
body_chars=$(awk 'NR>1 && /^---$/{f=1;next} f' SKILL.md | wc -c | tr -d ' ')
body_tokens=$((body_chars / 4))
[[ "$body_tokens" -le 5000 ]]; check "SKILL.md body <= 5k tokens" $? "~${body_tokens} tokens"

# --- wiring: every path SKILL.md names must exist
missing=$(rg -o '(references|scripts|prompts)/[A-Za-z0-9._-]+' SKILL.md | sort -u \
  | while read -r p; do [[ -e "$p" ]] || echo "$p"; done)
[[ -z "$missing" ]]; check "referenced paths resolve" $? "${missing:-all present}"

# --- wiring, the other direction: a shipped file nothing points at is dead weight.
# Reachable means some other shipped text names it: SKILL.md for anything the
# agent is meant to use, or bootstrap/evals for the meta-tooling around it.
orphans=$(fd -t f . references scripts prompts 2>/dev/null \
  | while read -r p; do
      stem=$(basename "$p"); stem=${stem%.*}
      # every other shipped text, so a file mentioning itself doesn't count
      index=$(printf '%s\n' SKILL.md scripts/*.sh evals/README.md | grep -vxF "$p")
      grep -qF "$stem" $index || echo "$p"
    done)
[[ -z "$orphans" ]]; check "no unreachable resources" $? "${orphans:-all reachable}"

# --- executability: the documented invocation has to work from a cold start
bad_shebang=$(head -1 scripts/search-*.py | rg -v '^(==>|$)' | rg -v 'python3$' || true)
[[ -z "$bad_shebang" ]]; check "scripts shebang python3" $? "${bad_shebang:-all python3}"

nonexec=$(fd -t f -e py -e sh . scripts | while read -r p; do [[ -x "$p" ]] || echo "$p"; done)
[[ -z "$nonexec" ]]; check "scripts executable" $? "${nonexec:-all +x}"

if [[ -x scripts/.venv/bin/python3 ]] \
   && scripts/.venv/bin/python3 -c 'import tree_sitter, tree_sitter_java, tree_sitter_kotlin, tree_sitter_typescript, tree_sitter_python' 2>/dev/null; then
  check "parser deps installed" 0 "scripts/.venv"
else
  check "parser deps installed" 1 "run scripts/bootstrap.sh"
fi

# --- the end-to-end claim: a fuzzy keyword lands on the right file.
# One fixture per grammar family, because a broken query fails silently — it
# matches nothing rather than erroring, and the list still prints.
if out=$(./scripts/search-ts-sources.py retrypolicy evals/fixtures/shop 2>&1); then
  grep -q 'READ FIRST' <<<"$out" && grep -q 'settings.ts' <<<"$out"
  check "reading list smoke test (ts)" $? "fuzzy 'retrypolicy' -> settings.ts"
else
  check "reading list smoke test (ts)" 1 "script exited non-zero"
fi

if out=$(./scripts/search-python-sources.py retrypolicy evals/fixtures/inventory 2>&1); then
  grep -q 'READ FIRST' <<<"$out" && grep -q 'settings.py' <<<"$out"
  check "reading list smoke test (py)" $? "fuzzy 'retrypolicy' -> settings.py"
else
  check "reading list smoke test (py)" 1 "script exited non-zero"
fi

# The Python edges a regex could not produce: a subclass across a relative
# import, and a decorator reference. Guards the query, not just the ranking.
if out=$(./scripts/search-python-sources.py BaseProcessor evals/fixtures/inventory 2>&1); then
  grep -q 'subclasses BaseProcessor' <<<"$out" && grep -q 'decorated by audited' <<<"$out"
  check "python structural edges" $? "subclasses + decorated by"
else
  check "python structural edges" 1 "script exited non-zero"
fi

echo
if [[ "$fails" -eq 0 ]]; then
  echo "all mechanical checks passed — behaviour is scored by: claude plugin eval ."
else
  echo "$fails check(s) failed"
fi
exit $(( fails > 0 ? 1 : 0 ))
