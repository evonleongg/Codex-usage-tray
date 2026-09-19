[CmdletBinding()]
param(
    [switch]$NoStartup
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
$batch = Join-Path $root 'run.bat'
$icon = Join-Path $root 'assets\codex-usage.ico'
$pythonw = (Get-Command pythonw.exe -ErrorAction Stop).Source
$shell = New-Object -ComObject WScript.Shell

function Set-Shortcut {
    param(
        [string]$Path,
        [string]$TargetPath,
        [string]$Arguments = '',
        [string]$Description
    )
    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $TargetPath
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $root
    $shortcut.IconLocation = "$icon,0"
    $shortcut.Description = $Description
    $shortcut.Save()
}

Set-Shortcut -Path (Join-Path ([Environment]::GetFolderPath('Desktop')) 'Codex Usage Tray.lnk') `
    -TargetPath $batch -Description 'Open the Codex usage panel'
Set-Shortcut -Path (Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs\Codex Usage Tray.lnk') `
    -TargetPath $batch -Description 'Open the Codex usage panel'

if (-not $NoStartup) {
    Set-Shortcut -Path (Join-Path ([Environment]::GetFolderPath('Startup')) 'Codex Usage Tray.lnk') `
        -TargetPath $pythonw -Arguments ('"' + (Join-Path $root 'app.py') + '" --minimized') `
        -Description 'Start Codex Usage Tray minimized at sign-in'
}

Write-Host 'Desktop and Start menu shortcuts created.'
if (-not $NoStartup) { Write-Host 'Start at sign-in is enabled (runs minimized in the tray).' }
