#!/usr/bin/env bash
# One-time setup for the reading-list scripts.
#
# They parse with tree-sitter rather than regex, so they need real packages.
# This creates a venv beside them (scripts/.venv) and installs those packages
# into it. The scripts re-exec into that venv on their own, so nothing has to
# be activated: after this runs once, ./search-java-sources.py just works.
#
# Idempotent — re-running it when everything is already importable does nothing.
# Pass --force to rebuild the venv from scratch.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
venv="$here/.venv"
requirements="$here/requirements.txt"
modules="tree_sitter, tree_sitter_java, tree_sitter_kotlin, tree_sitter_typescript"

if [[ "${1:-}" == "--force" ]]; then
  rm -rf "$venv"
elif [[ -x "$venv/bin/python3" ]] && "$venv/bin/python3" -c "import $modules" 2>/dev/null; then
  echo "context-builder: venv already good ($venv)"
  exit 0
fi

python="$(command -v python3 || true)"
if [[ -z "$python" ]]; then
  echo "error: python3 is not on PATH; install Python 3.9+ and re-run" >&2
  exit 1
fi
"$python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' || {
  echo "error: python3 is $("$python" -V), but these scripts need 3.9+" >&2
  exit 1
}

[[ -x "$venv/bin/python3" ]] || "$python" -m venv "$venv"
"$venv/bin/python3" -m pip install --quiet --upgrade pip
"$venv/bin/python3" -m pip install --quiet -r "$requirements"

# Fail loudly here rather than at the first search, where it reads as a bug.
"$venv/bin/python3" -c "import $modules" || {
  echo "error: install finished but the grammars still do not import" >&2
  exit 1
}
echo "context-builder: venv ready ($venv)"
