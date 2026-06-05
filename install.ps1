[CmdletBinding()]
param(
    [string]$PythonExe = $env:PYTHON_EXE,
    [switch]$SkipDependencies,
    [switch]$SkipEnvFile,
    [switch]$SkipSteeringConfig,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RequirementsPath = Join-Path $ScriptDir 'requirements.txt'
$EnvExamplePath = Join-Path $ScriptDir '.env.example'
$EnvPath = Join-Path $ScriptDir '.env'
$SteeringConfigScript = Join-Path $ScriptDir 'configure-codex-desktop-steering.ps1'

function Resolve-PythonCommand {
    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        return @($PythonExe)
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

function Invoke-Python {
    param([string[]]$Arguments)

    $command = @(Resolve-PythonCommand)
    $exe = $command[0]
    $baseArgs = @()
    if ($command.Count -gt 1) {
        $baseArgs = $command[1..($command.Count - 1)]
    }

    if ($DryRun) {
        Write-Output "Would run: $exe $($baseArgs + $Arguments -join ' ')"
        return
    }

    & $exe @baseArgs @Arguments
}

if (-not $SkipDependencies) {
    if (-not (Test-Path -LiteralPath $RequirementsPath)) {
        throw "requirements.txt was not found: $RequirementsPath"
    }
    Write-Output "Installing Python dependencies from requirements.txt"
    Invoke-Python -Arguments @('-m', 'pip', 'install', '-r', $RequirementsPath)
}

if (-not $SkipEnvFile) {
    if (Test-Path -LiteralPath $EnvPath) {
        Write-Output ".env already exists: $EnvPath"
    } elseif (Test-Path -LiteralPath $EnvExamplePath) {
        if ($DryRun) {
            Write-Output "Would create: $EnvPath from .env.example"
        } else {
            Copy-Item -LiteralPath $EnvExamplePath -Destination $EnvPath
            Write-Output "Created: $EnvPath"
        }
    } else {
        Write-Output ".env.example was not found; skipping .env creation."
    }
}

if (-not $SkipSteeringConfig) {
    if (-not (Test-Path -LiteralPath $SteeringConfigScript)) {
        throw "Steering config script was not found: $SteeringConfigScript"
    }
    Write-Output "Configuring Codex Desktop follow-up mode: steer"
    if ($DryRun) {
        & $SteeringConfigScript -DryRun
    } else {
        & $SteeringConfigScript
    }
}

Write-Output 'Install complete.'
Write-Output 'Next: edit .env, then run .\codex-discord-bot.cmd'
