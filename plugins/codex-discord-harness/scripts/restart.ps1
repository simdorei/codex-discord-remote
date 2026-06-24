[CmdletBinding()]
param(
    [string]$RepoRoot,
    [switch]$DryRun,
    [switch]$Immediate,
    [int]$DelaySeconds = 10,
    [int]$QuietSeconds = 90,
    [int]$WaitTimeoutSeconds = 900
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

if ($DryRun) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Watchdog `
        -CheckRestartReady `
        -RestartQuietSeconds $QuietSeconds `
        -RestartWaitTimeoutSeconds 0
    exit $LASTEXITCODE
}

'restart' | Set-Content -LiteralPath $RestartMarker -Encoding ascii
Write-Output "restart_marker_written: $RestartMarker"

if ($Immediate) {
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $Watchdog `
        -RestartQuietSeconds $QuietSeconds `
        -RestartWaitTimeoutSeconds $WaitTimeoutSeconds
    exit $LASTEXITCODE
}

$escapedWatchdog = $Watchdog.Replace("'", "''")
$command = "& { Start-Sleep -Seconds $DelaySeconds; & '$escapedWatchdog' -RestartQuietSeconds $QuietSeconds -RestartWaitTimeoutSeconds $WaitTimeoutSeconds }"
Start-Process -FilePath 'powershell.exe' `
    -ArgumentList @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-Command', $command) `
    -WindowStyle Hidden
Write-Output "restart_deferred: delay_seconds=$DelaySeconds quiet_seconds=$QuietSeconds wait_timeout_seconds=$WaitTimeoutSeconds"
