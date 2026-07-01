#!/usr/bin/env sh
set -eu

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
python_exe=${PYTHON_EXE:-}
codex_exe=${CODEX_EXE:-}
skip_dependencies=0
skip_env_file=0
skip_steering_config=0
skip_codex_plugin=0
dry_run=0

usage() {
  echo "Usage: ./install.sh [--python-exe PATH] [--codex-exe PATH] [--skip-dependencies] [--skip-env-file] [--skip-steering-config] [--skip-codex-plugin] [--dry-run]" >&2
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --python-exe)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      python_exe=$2
      shift 2
      ;;
    --codex-exe)
      [ "$#" -ge 2 ] || { usage; exit 2; }
      codex_exe=$2
      shift 2
      ;;
    --skip-dependencies)
      skip_dependencies=1
      shift
      ;;
    --skip-env-file)
      skip_env_file=1
      shift
      ;;
    --skip-steering-config)
      skip_steering_config=1
      shift
      ;;
    --skip-codex-plugin)
      skip_codex_plugin=1
      shift
      ;;
    --dry-run)
      dry_run=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      usage
      exit 2
      ;;
  esac
done

resolve_python() {
  if [ -n "$python_exe" ]; then
    printf '%s\n' "$python_exe"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  elif command -v python >/dev/null 2>&1; then
    command -v python
  else
    echo "Python was not found. Install Python 3.11+ or set PYTHON_EXE." >&2
    exit 1
  fi
}

run_python() {
  py=$(resolve_python)
  if [ "$dry_run" -eq 1 ]; then
    echo "Would run: $py $*"
    return 0
  fi
  "$py" "$@"
}

get_env_value() {
  name=$1
  env_path="$script_dir/.env"
  [ -f "$env_path" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      ''|\#*) continue ;;
      "$name="*)
        value=${line#*=}
        value=${value%\"}
        value=${value#\"}
        printf '%s\n' "$value"
        return 0
        ;;
    esac
  done < "$env_path"
}

resolve_codex() {
  if [ -n "$codex_exe" ]; then
    printf '%s\n' "$codex_exe"
    return 0
  fi
  env_codex=$(get_env_value CODEX_EXE || true)
  if [ -n "$env_codex" ]; then
    printf '%s\n' "$env_codex"
    return 0
  fi
  if command -v codex >/dev/null 2>&1; then
    command -v codex
    return 0
  fi
  echo "Codex CLI was not found. Set CODEX_EXE or install/enable the codex command." >&2
  return 1
}

run_codex() {
  exe=$(resolve_codex)
  if [ "$dry_run" -eq 1 ]; then
    echo "Would run: $exe $*"
    return 0
  fi
  "$exe" "$@"
}

if [ "$skip_dependencies" -eq 0 ]; then
  [ -f "$script_dir/requirements.txt" ] || { echo "requirements.txt was not found: $script_dir/requirements.txt" >&2; exit 1; }
  echo "Installing Python dependencies from requirements.txt"
  run_python -m pip install -r "$script_dir/requirements.txt"
fi

if [ "$skip_env_file" -eq 0 ]; then
  if [ -f "$script_dir/.env" ]; then
    echo ".env already exists: $script_dir/.env"
  elif [ -f "$script_dir/.env.example" ]; then
    if [ "$dry_run" -eq 1 ]; then
      echo "Would create: $script_dir/.env from .env.example"
    else
      cp "$script_dir/.env.example" "$script_dir/.env"
      echo "Created: $script_dir/.env"
    fi
  else
    echo ".env.example was not found; skipping .env creation."
  fi
fi

echo "Discovering Codex Desktop executable."
run_python "$script_dir/codex_desktop_bridge.py" discover_codex

if [ "$skip_steering_config" -eq 1 ]; then
  echo "Skipping steering config: installer no longer changes Codex Desktop follow-up mode."
fi

if [ "$skip_codex_plugin" -eq 1 ]; then
  echo "Skipping Codex plugin install."
else
  [ -f "$script_dir/.agents/plugins/marketplace.json" ] || { echo "Codex plugin marketplace was not found: $script_dir/.agents/plugins/marketplace.json" >&2; exit 1; }
  echo "Installing Codex plugin marketplace from this repository."
  if run_codex plugin marketplace add "$script_dir" && run_codex plugin add codex-discord-remote@codex-discord-remote; then
    :
  else
    echo "Codex plugin install skipped."
    echo "Bot setup can continue. Install the Codex plugin later after the codex command is available."
  fi
fi

echo "Install complete."
echo "Setup required: run ./setup-discord-bot.sh and paste the Discord bot token when prompted."
echo "After setup, restart Codex so bundled skills reload, then run ./codex-discord-bot.sh or the platform launcher."
