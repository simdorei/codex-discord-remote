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

$EnvPath = Join-Path $RepoRoot '.env'
$DiscordApplicationMeUrl = 'https://discord.com/api/v10/applications/@me'
$DefaultPermissionBits = @(
    [int64]64,           # Add Reactions
    [int64]1024,         # View Channels
    [int64]2048,         # Send Messages
    [int64]16384,        # Embed Links
    [int64]32768,        # Attach Files
    [int64]65536,        # Read Message History
    [int64]2147483648,   # Use Application Commands
    [int64]17179869184,  # Manage Threads
    [int64]34359738368,  # Create Public Threads
    [int64]274877906944  # Send Messages in Threads
)

function Get-DefaultBotPermissions {
    $permissions = [int64]0
    foreach ($bit in $DefaultPermissionBits) {
        $permissions = $permissions -bor $bit
    }
    return $permissions
}

function New-DiscordBotInviteUrl {
    param(
        [string]$ClientId,
        [int64]$Permissions
    )

    $scope = [uri]::EscapeDataString('bot applications.commands')
    return "https://discord.com/oauth2/authorize?client_id=$ClientId&scope=$scope&permissions=$Permissions"
}

function ConvertFrom-SecureInput {
    param([securestring]$SecureString)

    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($SecureString)
    try {
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}

function Set-EnvFileValue {
    param(
        [string]$Path,
        [string]$Name,
        [string]$Value
    )

    if ($Value -match "[`r`n]") {
        throw "$Name cannot contain a newline."
    }

    $newline = [Environment]::NewLine
    $lines = @()
    if (Test-Path -LiteralPath $Path) {
        $content = [IO.File]::ReadAllText($Path)
        if ($content.Contains("`r`n")) {
            $newline = "`r`n"
        } elseif ($content.Contains("`n")) {
            $newline = "`n"
        }

        $rawLines = $content -split "\r?\n"
        if ($rawLines.Count -eq 1 -and $rawLines[0] -eq '') {
            $lines = @()
        } elseif ($rawLines.Count -gt 0 -and $rawLines[-1] -eq '') {
            if ($rawLines.Count -eq 1) {
                $lines = @()
            } else {
                $lines = @($rawLines[0..($rawLines.Count - 2)])
            }
        } else {
            $lines = @($rawLines)
        }
    }

    $found = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = [string]$lines[$i]
        $trimmed = $line.Trim()
        if (-not $trimmed -or $trimmed.StartsWith('#') -or -not $trimmed.Contains('=')) {
            continue
        }

        $key = $trimmed.Split('=', 2)[0]
        if ($key -eq $Name) {
            $lines[$i] = "$Name=$Value"
            $found = $true
            break
        }
    }

    if (-not $found) {
        $lines += "$Name=$Value"
    }

    [IO.File]::WriteAllText($Path, ([string]::Join($newline, $lines) + $newline))
}

function Get-DiscordApplicationMe {
    param([string]$BotToken)

    try {
        return Invoke-RestMethod -Method Get -Uri $DiscordApplicationMeUrl -Headers @{ Authorization = "Bot $BotToken" }
    } catch {
        throw "Discord bot token check failed. Copy the token again from Discord Developer Portal. Details: $($_.Exception.Message)"
    }
}

$permissions = Get-DefaultBotPermissions

if ($DryRun) {
    Write-Output 'Dry run: no token was requested and .env was not changed.'
    Write-Output "Would save DISCORD_BOT_TOKEN to: $EnvPath"
    Write-Output 'Invite link:'
    Write-Output (New-DiscordBotInviteUrl -ClientId $BotId -Permissions $permissions)
    exit 0
}

Write-Output 'Paste the Discord bot token. Input is hidden.'
$secureToken = Read-Host -Prompt 'Discord bot token' -AsSecureString
if ($secureToken.Length -eq 0) {
    throw 'Discord bot token is empty.'
}

$token = (ConvertFrom-SecureInput -SecureString $secureToken).Trim()
if ([string]::IsNullOrWhiteSpace($token)) {
    throw 'Discord bot token is empty.'
}

$application = Get-DiscordApplicationMe -BotToken $token
if ([string]::IsNullOrWhiteSpace($application.id)) {
    throw 'Discord token check succeeded, but Discord did not return an application id.'
}

Set-EnvFileValue -Path $EnvPath -Name 'DISCORD_BOT_TOKEN' -Value $token

Write-Output "Discord bot token saved to: $EnvPath"
Write-Output "Application: $($application.name) ($($application.id))"
Write-Output 'Invite link:'
Write-Output (New-DiscordBotInviteUrl -ClientId $application.id -Permissions $permissions)
Write-Output 'Open the invite link, choose your Discord server, and authorize the bot.'
Write-Output 'The invite link adds the bot to a server. Channel access still depends on Discord channel permissions and the channel IDs in .env.'
Write-Output 'Next: copy server/channel IDs into .env, restart Codex, then run .\codex-discord-bot.cmd'
