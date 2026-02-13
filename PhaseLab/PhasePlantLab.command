#!/bin/zsh
set -euo pipefail

cd "$(dirname "$0")"

typeset -a CANDIDATES=(
  "/Library/Frameworks/Python.framework/Versions/Current/bin/python3"
  "/opt/homebrew/bin/python3"
  "/usr/local/bin/python3"
)

PYTHON_BIN=""
for candidate in "${CANDIDATES[@]}"; do
  if [[ -x "$candidate" ]]; then
    PYTHON_BIN="$candidate"
    break
  fi
done

if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    found="$(command -v python3)"
    if [[ "$found" != *"/Applications/Xcode.app/"* ]]; then
      PYTHON_BIN="$found"
    fi
  fi
fi

if [[ -z "$PYTHON_BIN" ]]; then
  echo "No compatible Python 3 found. Install python.org Python 3.12+."
  read -r -n 1 -s "?Press any key to close..."
  exit 1
fi

export TK_SILENCE_DEPRECATION=1
exec "$PYTHON_BIN" "./phaseplant_lab_gui.py"
