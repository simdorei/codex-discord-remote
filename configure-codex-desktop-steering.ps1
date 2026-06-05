[CmdletBinding()]
param(
    [string]$CodexHome = $env:CODEX_HOME,
    [switch]$DryRun,
    [switch]$ShowContent
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($CodexHome)) {
    $CodexHome = Join-Path $HOME '.codex'
}

$CodexHome = [IO.Path]::GetFullPath($CodexHome)
$ConfigPath = Join-Path $CodexHome 'config.toml'

function Set-DesktopSteeringDefault {
    param([string]$Text)

    $lines = @()
    if (-not [string]::IsNullOrEmpty($Text)) {
        $lines = @($Text -split "`r?`n", -1)
        if ($lines.Count -gt 0 -and $lines[-1] -eq '') {
            if ($lines.Count -eq 1) {
                $lines = @()
            } else {
                $lines = $lines[0..($lines.Count - 2)]
            }
        }
    }

    $desktopStart = -1
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*\[desktop\]\s*$') {
            $desktopStart = $i
            break
        }
    }

    if ($desktopStart -lt 0) {
        if ($lines.Count -gt 0 -and -not [string]::IsNullOrWhiteSpace($lines[-1])) {
            $lines += ''
        }
        $lines += '[desktop]'
        $lines += 'followUpQueueMode = "steer"'
        return ($lines -join "`r`n") + "`r`n"
    }

    $desktopEnd = $lines.Count
    for ($i = $desktopStart + 1; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match '^\s*\[[^\]]+\]\s*$') {
            $desktopEnd = $i
            break
        }
    }

    for ($i = $desktopStart + 1; $i -lt $desktopEnd; $i++) {
        if ($lines[$i] -match '^\s*followUpQueueMode\s*=') {
            $lines[$i] = 'followUpQueueMode = "steer"'
            return ($lines -join "`r`n") + "`r`n"
        }
    }

    if ($desktopStart + 1 -ge $lines.Count) {
        $lines += 'followUpQueueMode = "steer"'
    } else {
        $before = $lines[0..$desktopStart]
        $after = $lines[($desktopStart + 1)..($lines.Count - 1)]
        $lines = @($before + 'followUpQueueMode = "steer"' + $after)
    }

    return ($lines -join "`r`n") + "`r`n"
}

$existing = ''
if (Test-Path -LiteralPath $ConfigPath) {
    $existing = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
}

$updated = Set-DesktopSteeringDefault -Text $existing

if ($DryRun) {
    Write-Output "Would update: $ConfigPath"
    Write-Output 'Would configure [desktop] followUpQueueMode = "steer"'
    if ($ShowContent) {
        Write-Output ''
        Write-Output $updated
    }
    exit 0
}

New-Item -ItemType Directory -Path $CodexHome -Force | Out-Null

if (Test-Path -LiteralPath $ConfigPath) {
    $stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
    $backupPath = "$ConfigPath.pre-steering-default-$stamp"
    Copy-Item -LiteralPath $ConfigPath -Destination $backupPath -Force
    Write-Output "Backup: $backupPath"
}

Set-Content -LiteralPath $ConfigPath -Value $updated -Encoding UTF8
Write-Output "Updated: $ConfigPath"
Write-Output 'Configured [desktop] followUpQueueMode = "steer"'
