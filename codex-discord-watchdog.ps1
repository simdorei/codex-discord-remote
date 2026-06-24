[CmdletBinding()]
param(
    [switch]$DryRun,
    [switch]$LogHealthy,
    [switch]$CheckRestartReady,
    [int]$RestartQuietSeconds = 90,
    [int]$RestartWaitTimeoutSeconds = 900
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$BotScript = Join-Path $ScriptDir 'codex_discord_bot.py'
$BridgePath = Join-Path $ScriptDir 'codex_desktop_bridge.py'
$RuntimeLockPath = Join-Path $ScriptDir '.codex_discord_bot.runtime.lock'
$RestartRequestPath = Join-Path $ScriptDir '.codex_discord_bot.restart'
$RestartClaimPath = Join-Path $ScriptDir ".codex_discord_bot.restart.claimed.$PID"
$StopRequestPath = Join-Path $ScriptDir '.codex_discord_bot.stop'
$DisablePath = Join-Path $ScriptDir '.codex_discord_bot.disabled'
$HeadlessLauncher = Join-Path $ScriptDir 'codex-discord-bot-headless.vbs'
$LauncherLogPath = Join-Path $ScriptDir 'discord_launcher.log'
. (Join-Path $ScriptDir 'codex-discord-watchdog-runtime.ps1')
. (Join-Path $ScriptDir 'codex-discord-watchdog-restart-runtime.ps1')

if (-not (Test-Path -LiteralPath $BotScript)) {
    Write-LauncherLog "watchdog_error reason=bot_script_missing script=$BotScript"
    exit 1
}

if ($CheckRestartReady) {
    Assert-CodexThreadsQuietForRestart
    Write-Output "restart_check_ok"
    exit 0
}

if (Test-Path -LiteralPath $StopRequestPath) {
    if ($DryRun) {
        Write-Output "stop_requested"
        exit 0
    }
    Write-LauncherLog "watchdog_stop_requested marker=$StopRequestPath"
    Remove-Item -LiteralPath $StopRequestPath -Force -ErrorAction SilentlyContinue
    Stop-RuntimeBotProcess
    exit 0
}

if (Test-Path -LiteralPath $DisablePath) {
    if ($DryRun) {
        Write-Output "disabled"
    } elseif ($LogHealthy) {
        Write-LauncherLog "watchdog_disabled marker=$DisablePath"
    }
    exit 0
}

if (Test-Path -LiteralPath $RestartRequestPath) {
    if ($DryRun) {
        Write-Output "restart_requested"
        exit 0
    }
    $claimedRestartPath = Claim-RestartRequest
    if (-not $claimedRestartPath) {
        if (Test-BotProcessAlive) {
            exit 0
        }
    }
    try {
        Wait-CodexThreadsQuietForRestart
    } catch {
        Remove-Item -LiteralPath $claimedRestartPath -Force -ErrorAction SilentlyContinue
        Write-LauncherLog "watchdog_restart_refused error=$($_.Exception.Message)"
        throw
    }
    try {
        Write-LauncherLog "watchdog_restart_requested marker=$claimedRestartPath"
        Stop-RuntimeBotProcess
    } finally {
        Remove-Item -LiteralPath $claimedRestartPath -Force -ErrorAction SilentlyContinue
    }
}

if (Test-BotProcessAlive) {
    if ($LogHealthy) {
        Write-LauncherLog "watchdog_ok script=$BotScript"
    }
    if ($DryRun) {
        Write-Output "running"
    }
    exit 0
}

if (-not (Test-Path -LiteralPath $HeadlessLauncher)) {
    Write-LauncherLog "watchdog_error reason=headless_launcher_missing launcher=$HeadlessLauncher"
    exit 1
}

if ($DryRun) {
    Write-Output "would_start"
    exit 0
}

Write-LauncherLog "watchdog_start_missing script=$BotScript launcher=$HeadlessLauncher"
Start-Process -FilePath 'wscript.exe' -ArgumentList @("`"$HeadlessLauncher`"") -WindowStyle Hidden
exit 0
