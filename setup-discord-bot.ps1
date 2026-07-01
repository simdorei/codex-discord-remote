[CmdletBinding()]
param(
    [string]$RepoRoot = $PSScriptRoot,
    [switch]$DryRun,
    [string]$BotId = '123456789012345678'
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $RepoRoot = (Get-Location).Path
}

$SetupScript = Join-Path $RepoRoot 'setup_discord_bot.py'

function Resolve-PythonCommand {
    if (-not [string]::IsNullOrWhiteSpace($env:PYTHON_EXE)) {
        return @($env:PYTHON_EXE)
    }

    $pyLauncher = Get-Command py -ErrorAction SilentlyContinue
    if ($pyLauncher -ne $null) {
        return @($pyLauncher.Source, '-3')
    }

    $python = Get-Command python -ErrorAction SilentlyContinue
    if ($python -ne $null) {
        return @($python.Source)
    }

    throw 'Python was not found. Install Python 3.11+ or set PYTHON_EXE.'
}

$command = @(Resolve-PythonCommand)
$exe = $command[0]
$baseArgs = @()
if ($command.Count -gt 1) {
    $baseArgs = $command[1..($command.Count - 1)]
}

$scriptArgs = @($SetupScript, '--repo-root', $RepoRoot, '--bot-id', $BotId)
if ($DryRun) {
    $scriptArgs += '--dry-run'
}

& $exe @baseArgs @scriptArgs
if ($LASTEXITCODE -ne 0) {
    throw "Discord bot setup failed with exit code ${LASTEXITCODE}: $exe $($baseArgs + $scriptArgs -join ' ')"
}
