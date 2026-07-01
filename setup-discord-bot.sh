#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -n "${PYTHON_EXE:-}" ]; then
  python_exe=$PYTHON_EXE
elif command -v python3 >/dev/null 2>&1; then
  python_exe=python3
elif command -v python >/dev/null 2>&1; then
  python_exe=python
else
  echo "ERROR: Python was not found. Install Python 3.11+ or set PYTHON_EXE." >&2
  exit 1
fi

exec "$python_exe" "$script_dir/setup_discord_bot.py" --repo-root "$script_dir" "$@"
