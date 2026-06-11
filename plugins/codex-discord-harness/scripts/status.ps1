[CmdletBinding()]
param(
    [string]$RepoRoot
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = Join-Path $PSScriptRoot '..\..\..'
}

$RepoRoot = [IO.Path]::GetFullPath($RepoRoot)
$RuntimeLock = Join-Path $RepoRoot '.codex_discord_bot.runtime.lock'
$RestartMarker = Join-Path $RepoRoot '.codex_discord_bot.restart'
$StopMarker = Join-Path $RepoRoot '.codex_discord_bot.stop'
$LogPath = Join-Path $RepoRoot 'codex_discord_bot.log'
$LauncherLogPath = Join-Path $RepoRoot 'discord_launcher.log'
$BridgePath = Join-Path $RepoRoot 'codex_desktop_bridge.py'

Write-Output "repo: $RepoRoot"

if (Test-Path -LiteralPath (Join-Path $RepoRoot '.git')) {
    git -C $RepoRoot status --short --branch
}

if (Test-Path -LiteralPath $RuntimeLock) {
    $pidText = (Get-Content -LiteralPath $RuntimeLock -Raw).Trim()
    Write-Output "runtime_lock_pid: $pidText"
    if ($pidText -match '^\d+$') {
        $process = Get-Process -Id ([int]$pidText) -ErrorAction SilentlyContinue
        if ($process) {
            Write-Output "bot_process: running pid=$($process.Id) name=$($process.ProcessName) started=$($process.StartTime)"
        } else {
            Write-Output 'bot_process: not found'
        }
    }
} else {
    Write-Output 'runtime_lock_pid: missing'
}

if (Test-Path -LiteralPath $RestartMarker) {
    Write-Output "restart_marker: present path=$RestartMarker"
} else {
    Write-Output 'restart_marker: missing'
}

if (Test-Path -LiteralPath $StopMarker) {
    Write-Output "stop_marker: present path=$StopMarker"
} else {
    Write-Output 'stop_marker: missing'
}

if (Test-Path -LiteralPath $LogPath) {
    Write-Output ''
    Write-Output 'recent_log:'
    Get-Content -LiteralPath $LogPath -Tail 40
} else {
    Write-Output 'recent_log: missing'
}

if (Test-Path -LiteralPath $LauncherLogPath) {
    Write-Output ''
    Write-Output 'launcher_log:'
    Get-Content -LiteralPath $LauncherLogPath -Tail 20
} else {
    Write-Output 'launcher_log: missing'
}

if (Test-Path -LiteralPath $BridgePath) {
    Write-Output ''
    Write-Output 'bridge_threads:'
    & py -3 $BridgePath list --db-root --limit 8
}
