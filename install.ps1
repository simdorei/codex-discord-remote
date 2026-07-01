[CmdletBinding()]
param(
    [string]$PythonExe = $env:PYTHON_EXE,
    [string]$CodexExe = $env:CODEX_EXE,
    [switch]$SkipDependencies,
    [switch]$SkipEnvFile,
    [switch]$SkipSteeringConfig,
    [switch]$SkipCodexPlugin,
    [switch]$DryRun
)

$ErrorActionPreference = 'Stop'

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RequirementsPath = Join-Path $ScriptDir 'requirements.txt'
$EnvExamplePath = Join-Path $ScriptDir '.env.example'
$EnvPath = Join-Path $ScriptDir '.env'
$PluginMarketplacePath = Join-Path $ScriptDir '.agents\plugins\marketplace.json'
$PluginRef = 'codex-discord-remote@codex-discord-remote'

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
    if ($LASTEXITCODE -ne 0) {
        throw "Python command failed with exit code ${LASTEXITCODE}: $exe $($baseArgs + $Arguments -join ' ')"
    }
}

function Get-EnvFileValue {
    param([string]$Name)

    if (-not (Test-Path -LiteralPath $EnvPath)) {
        return ''
    }
    foreach ($line in Get-Content -LiteralPath $EnvPath) {
        $trimmed = ([string]$line).Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#')) {
            continue
        }
        $key, $value = $trimmed.Split('=', 2)
        if ($key -eq $Name) {
            return $value.Trim().Trim('"')
        }
    }
    return ''
}

function Resolve-CodexCommand {
    if (-not [string]::IsNullOrWhiteSpace($CodexExe)) {
        return $CodexExe
    }

    $envCodexExe = Get-EnvFileValue -Name 'CODEX_EXE'
    if (-not [string]::IsNullOrWhiteSpace($envCodexExe)) {
        return $envCodexExe
    }

    $codex = Get-Command codex -ErrorAction SilentlyContinue
    if ($codex -ne $null) {
        return $codex.Source
    }

    throw 'Codex CLI was not found. Set CODEX_EXE or install/enable the codex command.'
}

function Invoke-Codex {
    param([string[]]$Arguments)

    $exe = Resolve-CodexCommand
    if ($DryRun) {
        Write-Output "Would run: $exe $($Arguments -join ' ')"
        return
    }

    & $exe @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Codex CLI failed with exit code ${LASTEXITCODE}: $exe $($Arguments -join ' ')"
    }
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

Write-Output 'Discovering Codex Desktop executable.'
Invoke-Python -Arguments @('codex_desktop_bridge.py', 'discover_codex')

if ($SkipSteeringConfig) {
    Write-Output 'Skipping steering config: installer no longer changes Codex Desktop follow-up mode.'
}

if ($SkipCodexPlugin) {
    Write-Output 'Skipping Codex plugin install.'
} else {
    if (-not (Test-Path -LiteralPath $PluginMarketplacePath)) {
        throw "Codex plugin marketplace was not found: $PluginMarketplacePath"
    }
    try {
        Write-Output 'Installing Codex plugin marketplace from this repository.'
        Invoke-Codex -Arguments @('plugin', 'marketplace', 'add', $ScriptDir)
        Write-Output "Installing Codex plugin: $PluginRef"
        Invoke-Codex -Arguments @('plugin', 'add', $PluginRef)
    } catch {
        Write-Output "Codex plugin install skipped: $($_.Exception.Message)"
        Write-Output 'Bot setup can continue. Install the Codex plugin later after the codex command is available.'
    }
}

Write-Output 'Install complete.'
Write-Output 'Setup required: run .\setup-discord-bot.ps1 and paste the Discord bot token when prompted.'
Write-Output 'After setup, restart Codex so bundled skills reload, then run .\codex-discord-bot.cmd'
