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

    $portablePython = Join-Path $RepoRoot '.python-portable\python.exe'
    if (Test-Path -LiteralPath $portablePython) {
        return @($portablePython)
    }

    throw 'Portable Python 3.12 was not found. Run .\install.ps1 first to download it and pin PYTHON_EXE.'
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
