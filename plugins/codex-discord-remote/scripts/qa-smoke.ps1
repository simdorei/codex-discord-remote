[CmdletBinding()]
param(
    [string]$RepoRoot,
    [switch]$SkipUnitTests
)

$ErrorActionPreference = 'Stop'

function Invoke-NativeChecked {
    param(
        [Parameter(Mandatory = $true)]
        [scriptblock]$Command
    )

    $previousErrorActionPreference = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $Command
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "Native command failed with exit code $exitCode."
    }
}

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Join-Path $PSScriptRoot '..\..\..'
}

$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)

Push-Location $RepoRoot
try {
    Invoke-NativeChecked { git diff --check }

    Invoke-NativeChecked { powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -DryRun -SkipDependencies -SkipEnvFile }

    $GitBash = $env:OMO_CODEX_GIT_BASH_PATH
    if ([string]::IsNullOrWhiteSpace($GitBash)) {
        $DefaultGitBash = 'C:\Program Files\Git\bin\bash.exe'
        if (Test-Path -LiteralPath $DefaultGitBash) {
            $GitBash = $DefaultGitBash
        }
    }
    if (-not [string]::IsNullOrWhiteSpace($GitBash)) {
        Invoke-NativeChecked { & $GitBash -lc 'bash -n install.sh setup-discord-bot.sh codex-discord-bot.sh && bash install.sh --dry-run --skip-dependencies --skip-env-file --skip-codex-plugin && bash setup-discord-bot.sh --dry-run' }
    }

    $CompileTrackedPython = @'
import py_compile
import subprocess
import sys

result = subprocess.run(
    ["git", "ls-files", "*.py"],
    text=True,
    capture_output=True,
    check=True,
)
for path in result.stdout.splitlines():
    try:
        py_compile.compile(path, doraise=True)
    except Exception as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        raise SystemExit(1)
'@
    $CompileScript = [IO.Path]::ChangeExtension([IO.Path]::GetTempFileName(), '.py')
    try {
        Set-Content -LiteralPath $CompileScript -Value $CompileTrackedPython -Encoding utf8
        Invoke-NativeChecked { & py -3 $CompileScript }
    } finally {
        if (Test-Path -LiteralPath $CompileScript) {
            Remove-Item -LiteralPath $CompileScript -Force
        }
    }

    if (-not $SkipUnitTests) {
        Invoke-NativeChecked { & py -3 -m unittest tests.test_setup_discord_bot tests.test_codex_desktop_bridge_macos_input tests.test_codex_discord_button_qa_cases tests.test_codex_discord_button_qa_lifecycle_cases tests.test_codex_discord_button_qa_persistent_cases tests.test_codex_discord_button_qa_steer_case tests.test_codex_discord_busy tests.test_codex_discord_busy_choice_queue_action tests.test_codex_discord_busy_choice_steer_failure tests.test_codex_discord_busy_choice_steer_result tests.test_codex_discord_component_view_state tests.test_codex_desktop_bridge_command_ask tests.test_codex_desktop_bridge_command_ask_edge tests.test_codex_desktop_bridge_final_answer tests.test_codex_desktop_bridge_list_settings tests.test_codex_discord_delivery tests.test_codex_discord_persistent_busy_choice tests.test_codex_discord_persistent_busy_choice_queue tests.test_codex_discord_persistent_busy_queue tests.test_codex_discord_persistent_busy_choice_steer tests.test_codex_discord_persistent_busy_steer_action tests.test_codex_discord_persistent_busy_steer_busy_failure tests.test_codex_discord_persistent_busy_steer_result tests.test_codex_discord_persistent_busy_steer_runner tests.test_codex_discord_persistent_interactions tests.test_codex_discord_prefix_approval_commands tests.test_codex_discord_prefix_archive_commands tests.test_codex_discord_prefix_mirror_commands tests.test_codex_discord_prefix_new_command tests.test_codex_discord_prefix_dispatch tests.test_codex_discord_prefix_prompt_commands tests.test_codex_discord_prefix_qa_command tests.test_codex_discord_prefix_queue_commands tests.test_codex_discord_prefix_steer_command tests.test_codex_discord_prefix_status_commands tests.test_codex_discord_prompt_busy_result tests.test_codex_discord_prompt_flow tests.test_codex_discord_prompt_mapped_delivery tests.test_codex_discord_prompt_pending_delivery tests.test_codex_discord_prompt_retry_attempt tests.test_codex_discord_prompt_retry_exhausted tests.test_codex_discord_prompt_retry_suppression tests.test_codex_discord_prompt_stream_attempt tests.test_codex_discord_prompt_stream_result tests.test_codex_discord_prompt_stream_suppression tests.test_codex_discord_prompt_transport tests.test_codex_discord_session_mirror_archive tests.test_codex_discord_session_mirror_channels tests.test_codex_discord_session_mirror_commit tests.test_codex_discord_session_mirror_cursor tests.test_codex_discord_session_mirror_event_reader tests.test_codex_discord_session_mirror_item_delivery tests.test_codex_discord_session_mirror_item_sender tests.test_codex_discord_session_mirror_items tests.test_codex_discord_session_mirror_output_targets tests.test_codex_discord_slash_commands tests.test_codex_discord_slash_prompt_commands tests.test_codex_discord_slash_runtime_commands tests.test_codex_discord_stale_busy_components tests.test_codex_discord_mirror_channels tests.test_codex_discord_bot tests.test_mirror_sync_cleanup }
    }
} finally {
    Pop-Location
}
