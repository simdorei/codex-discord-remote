[CmdletBinding()]
param(
    [ValidateRange(1, 65535)]
    [int]$Port = 9222
)

$ErrorActionPreference = "Stop"

function Test-LoopbackPort {
    param([int]$TargetPort)

    $client = [System.Net.Sockets.TcpClient]::new()
    try {
        $client.Connect("127.0.0.1", $TargetPort)
        return $client.Connected
    }
    catch [System.Net.Sockets.SocketException] {
        return $false
    }
    finally {
        $client.Dispose()
    }
}

if (Get-Process -Name "ChatGPT" -ErrorAction SilentlyContinue) {
    throw "ChatGPT/Codex is already running. Fully close the app, then run this launcher again."
}

if (Test-LoopbackPort -TargetPort $Port) {
    throw "Local port $Port is already in use. Choose another port and use the same value in CHATGPT_APP_CDP_URL."
}

$package = Get-AppxPackage -Name "OpenAI.Codex" |
    Sort-Object Version -Descending |
    Select-Object -First 1
if ($null -eq $package) {
    throw "The ChatGPT/Codex desktop app package is not installed."
}

$executable = Join-Path $package.InstallLocation "app\ChatGPT.exe"
if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "ChatGPT.exe was not found inside the installed app package."
}

$arguments = @(
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=$Port"
)
$process = Start-Process -FilePath $executable -ArgumentList $arguments -PassThru
if ($process.HasExited) {
    throw "ChatGPT/Codex exited before local inspection became ready."
}

$deadline = [DateTime]::UtcNow.AddSeconds(15)
while ([DateTime]::UtcNow -lt $deadline) {
    if (Test-LoopbackPort -TargetPort $Port) {
        Write-Host "ChatGPT/Codex started with local inspection at http://127.0.0.1:$Port"
        exit 0
    }
    Start-Sleep -Milliseconds 250
}

throw "ChatGPT/Codex started, but local inspection port $Port did not become ready within 15 seconds."
