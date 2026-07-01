#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
script="$script_dir/codex_discord_bot.py"
env_file="$script_dir/.env"
lock_dir="$script_dir/.codex_discord_bot.lock"
pid_file="$lock_dir/launcher.pid"
log_path=${CODEX_DISCORD_LOG_PATH:-"$script_dir/codex_discord_bot.log"}
launcher_log_path="$script_dir/discord_launcher.log"
python_exe=${PYTHON_EXE:-}

log_launcher() {
  message=$1
  timestamp=$(date '+%Y-%m-%dT%H:%M:%S')
  printf '[%s] %s\n' "$timestamp" "$message" >> "$launcher_log_path"
}

load_env_value() {
  name=$1
  [ -f "$env_file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "$name="*)
        value=${line#*=}
        value=${value%\"}
        value=${value#\"}
        printf '%s\n' "$value"
        return 0
        ;;
    esac
  done < "$env_file"
}

resolve_python() {
  if [ -n "$python_exe" ]; then
    printf '%s\n' "$python_exe"
  else
    env_python=$(load_env_value PYTHON_EXE || true)
    if [ -n "$env_python" ]; then
      printf '%s\n' "$env_python"
    elif command -v python3 >/dev/null 2>&1; then
      command -v python3
    elif command -v python >/dev/null 2>&1; then
      command -v python
    else
      echo "ERROR: Python executable not found." >&2
      exit 1
    fi
  fi
}

pid_alive() {
  pid=$1
  [ -n "$pid" ] || return 1
  kill -0 "$pid" >/dev/null 2>&1
}

if [ ! -f "$script" ]; then
  echo "ERROR: Script not found: $script" >&2
  exit 1
fi

echo
echo "Codex Discord frontend bridge is starting."
echo "Script: $script"
echo "Log:    $log_path"
echo
log_launcher "visible_start script=$script log=$log_path"

if mkdir "$lock_dir" 2>/dev/null; then
  :
else
  existing_pid=""
  [ -f "$pid_file" ] && existing_pid=$(cat "$pid_file")
  if pid_alive "$existing_pid"; then
    echo "Codex Discord bot is already running."
    echo "Log: $log_path"
    log_launcher "already_running pid=$existing_pid script=$script log=$log_path"
    exit 0
  fi
  echo "Removing stale Codex Discord bot lock."
  rm -rf "$lock_dir"
  mkdir "$lock_dir"
fi

cleanup() {
  rm -rf "$lock_dir"
}
trap cleanup EXIT INT TERM
printf '%s\n' "$$" > "$pid_file"

py=$(resolve_python)
log_launcher "run python_exe=$py script=$script"
set +e
"$py" "$script" "$@"
exit_code=$?
set -e
log_launcher "exit code=$exit_code script=$script"
exit "$exit_code"
