#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)

if [ -n "${PYTHON_EXE:-}" ]; then
  set -- "$PYTHON_EXE" "$script_dir/setup_discord_bot.py" --repo-root "$script_dir" "$@"
elif case "$(uname -s 2>/dev/null || true)" in MINGW*|MSYS*|CYGWIN*) true ;; *) false ;; esac && command -v py >/dev/null 2>&1; then
  set -- py -3 "$script_dir/setup_discord_bot.py" --repo-root "$script_dir" "$@"
elif command -v python3 >/dev/null 2>&1; then
  set -- python3 "$script_dir/setup_discord_bot.py" --repo-root "$script_dir" "$@"
elif command -v python >/dev/null 2>&1; then
  set -- python "$script_dir/setup_discord_bot.py" --repo-root "$script_dir" "$@"
else
  echo "ERROR: Python was not found. Install Python 3.11+ or set PYTHON_EXE." >&2
  exit 1
fi

exec "$@"
