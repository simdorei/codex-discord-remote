[CmdletBinding()]
param(
    [string]$RepoRoot,
    [switch]$SkipUnitTests
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Join-Path $PSScriptRoot '..\..\..'
}

$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)

Push-Location $RepoRoot
try {
    git diff --check

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -DryRun -SkipDependencies -SkipEnvFile

    & py -3 -m py_compile `
        codex_app_server_transport.py `
        codex_desktop_bridge.py `
        codex_discord_bot.py `
        codex_discord_help.py `
        codex_discord_runner.py `
        codex_discord_steering.py `
        codex_discord_store.py `
        tests\test_codex_discord_bot.py `
        tests\test_mirror_sync_cleanup.py

    if (-not $SkipUnitTests) {
        & py -3 -m unittest tests.test_codex_discord_bot tests.test_mirror_sync_cleanup
    }
} finally {
    Pop-Location
}
