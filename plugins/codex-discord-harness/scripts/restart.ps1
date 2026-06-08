[CmdletBinding()]
param(
    [string]$RepoRoot
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Join-Path $PSScriptRoot '..\..\..'
}

$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$RestartMarker = Join-Path $RepoRoot '.codex_discord_bot.restart'
$Watchdog = Join-Path $RepoRoot 'codex-discord-watchdog.ps1'

if (-not (Test-Path -LiteralPath $Watchdog)) {
    throw "watchdog script not found: $Watchdog"
}

'restart' | Set-Content -LiteralPath $RestartMarker -Encoding ascii
Write-Output "restart_marker_written: $RestartMarker"

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Watchdog

