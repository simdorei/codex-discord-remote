[CmdletBinding()]
param(
    [string]$RepoRoot,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Join-Path $PSScriptRoot '..\..\..'
}

$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$RestartMarker = Join-Path $RepoRoot '.codex_discord_bot.restart'
$Watchdog = Join-Path $RepoRoot 'codex-discord-watchdog.ps1'
$BridgePath = Join-Path $RepoRoot 'codex_desktop_bridge.py'

function Assert-CodexThreadsIdleForRestart {
    if (-not (Test-Path -LiteralPath $BridgePath)) {
        throw "Cannot verify Codex thread state before restart; bridge script not found: $BridgePath"
    }

    $bridgeOutput = & py -3 $BridgePath list --db-root --limit 0 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "Cannot verify Codex thread state before restart.`n$($bridgeOutput -join "`n")"
    }

    $busyLines = @()
    foreach ($line in $bridgeOutput) {
        if ($line -notmatch '\|') {
            continue
        }
        $parts = [string]$line -split '\|'
        if ($parts.Count -lt 3) {
            continue
        }
        $state = $parts[2].Trim()
        if ($state -and $state -ne 'idle') {
            $busyLines += ([string]$line).Trim()
        }
    }

    if ($busyLines.Count -gt 0) {
        throw "Refusing to restart Codex Discord bot because Codex threads are not idle.`n$($busyLines -join "`n")"
    }
}

if (-not (Test-Path -LiteralPath $Watchdog)) {
    throw "watchdog script not found: $Watchdog"
}

Assert-CodexThreadsIdleForRestart

if ($DryRun) {
    Write-Output "restart_check_ok"
    exit 0
}

'restart' | Set-Content -LiteralPath $RestartMarker -Encoding ascii
Write-Output "restart_marker_written: $RestartMarker"

& powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Watchdog
