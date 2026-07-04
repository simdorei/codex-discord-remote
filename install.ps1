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
$RequiredPythonMajor = 3
$RequiredPythonMinor = 12
$PortablePythonVersion = '3.12.1'
$PortablePythonDir = Join-Path $ScriptDir '.python-portable'
$PortablePythonExe = Join-Path $PortablePythonDir 'python.exe'
$PortablePythonZip = Join-Path $PortablePythonDir "python-${PortablePythonVersion}-embed-amd64.zip"
$PortablePythonUrl = "https://www.python.org/ftp/python/${PortablePythonVersion}/python-${PortablePythonVersion}-embed-amd64.zip"
$GetPipUrl = 'https://bootstrap.pypa.io/get-pip.py'
$GetPipPath = Join-Path $PortablePythonDir 'get-pip.py'

function Test-PythonCommand {
    param([string[]]$Command)

    $exe = $Command[0]
    $baseArgs = @()
    if ($Command.Count -gt 1) {
        $baseArgs = $Command[1..($Command.Count - 1)]
    }

    & $exe @baseArgs -c "import sys; raise SystemExit(0 if sys.version_info[:2] == ($RequiredPythonMajor, $RequiredPythonMinor) else 1)" >$null 2>$null
    return $LASTEXITCODE -eq 0
}

function Get-PythonExecutablePath {
    param([string[]]$Command)

    $exe = $Command[0]
    $baseArgs = @()
    if ($Command.Count -gt 1) {
        $baseArgs = $Command[1..($Command.Count - 1)]
    }

    $output = & $exe @baseArgs -c "import sys; print(sys.executable)"
    if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($output)) {
        throw "Python command failed while resolving sys.executable: $exe $($baseArgs -join ' ')"
    }
    return ([string]$output).Trim()
}

function Find-PythonCommand {
    if (-not [string]::IsNullOrWhiteSpace($PythonExe)) {
        $candidate = @($PythonExe)
        if (Test-PythonCommand -Command $candidate) {
            return $candidate
        }
        throw "PYTHON_EXE must point to Python 3.12.x: $PythonExe"
    }

    if (Test-Path -LiteralPath $PortablePythonExe) {
        $candidate = @($PortablePythonExe)
        if (Test-PythonCommand -Command $candidate) {
            return $candidate
        }
    }

    return @()
}

function Enable-PortablePythonSite {
    $pthFiles = @(Get-ChildItem -LiteralPath $PortablePythonDir -Filter 'python*._pth' -File)
    if ($pthFiles.Count -eq 0) {
        throw "Portable Python _pth file was not found in $PortablePythonDir."
    }
    $pthPath = $pthFiles[0].FullName
    $lines = @(Get-Content -LiteralPath $pthPath)
    $hasImportSite = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if (([string]$lines[$i]).Trim() -eq 'import site') {
            $hasImportSite = $true
            break
        }
        if (([string]$lines[$i]).Trim() -eq '#import site') {
            $lines[$i] = 'import site'
            $hasImportSite = $true
            break
        }
    }
    if (-not $hasImportSite) {
        $lines += 'import site'
    }
    Set-Content -LiteralPath $pthPath -Value $lines -Encoding ASCII
}

function Install-PortablePython312 {
    if ($DryRun) {
        Write-Host "Would download portable Python $PortablePythonVersion to $PortablePythonDir"
        return
    }
    Write-Host "Portable Python $PortablePythonVersion was not found. Downloading to $PortablePythonDir."
    New-Item -ItemType Directory -Force -Path $PortablePythonDir | Out-Null
    Invoke-WebRequest -Uri $PortablePythonUrl -OutFile $PortablePythonZip
    Expand-Archive -LiteralPath $PortablePythonZip -DestinationPath $PortablePythonDir -Force
    Remove-Item -LiteralPath $PortablePythonZip -Force
    Enable-PortablePythonSite
    Invoke-WebRequest -Uri $GetPipUrl -OutFile $GetPipPath
    & $PortablePythonExe $GetPipPath --no-warn-script-location
    if ($LASTEXITCODE -ne 0) {
        throw "get-pip.py failed for portable Python with exit code ${LASTEXITCODE}."
    }
    Remove-Item -LiteralPath $GetPipPath -Force
}

function Resolve-PythonCommand {
    $command = @(Find-PythonCommand)
    if ($command.Count -gt 0) {
        return $command
    }

    Install-PortablePython312
    if ($DryRun) {
        return @($PortablePythonExe)
    }
    $command = @(Find-PythonCommand)
    if ($command.Count -gt 0) {
        return $command
    }

    throw "Portable Python 3.12.x was not found after bootstrap: $PortablePythonExe"
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

function Set-EnvFileValue {
    param(
        [string]$Name,
        [string]$Value
    )

    $lines = @()
    if (Test-Path -LiteralPath $EnvPath) {
        $lines = @(Get-Content -LiteralPath $EnvPath)
    }

    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = [string]$lines[$i]
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $line.Contains('=')) {
            continue
        }
        $key, $oldValue = $line.Split('=', 2)
        $null = $oldValue
        if ($key.Trim() -eq $Name) {
            $lines[$i] = "$Name=$Value"
            $found = $true
            break
        }
    }

    if (-not $found) {
        $lines += "$Name=$Value"
    }

    Set-Content -LiteralPath $EnvPath -Value $lines -Encoding UTF8
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

    if ($DryRun) {
        Write-Output "Would set PYTHON_EXE to the resolved Python 3.12 executable in .env"
    } else {
        $pythonCommand = @(Resolve-PythonCommand)
        $pythonExePath = Get-PythonExecutablePath -Command $pythonCommand
        Set-EnvFileValue -Name 'PYTHON_EXE' -Value $pythonExePath
        Write-Output "Configured PYTHON_EXE=$pythonExePath"
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
