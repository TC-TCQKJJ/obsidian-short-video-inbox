param(
    [Parameter(Mandatory = $true)]
    [string]$ShortcutPath,
    [Parameter(Mandatory = $true)]
    [string]$TargetPath,
    [Parameter(Mandatory = $true)]
    [string]$Arguments,
    [Parameter(Mandatory = $true)]
    [string]$WorkingDirectory
)

$ErrorActionPreference = "Stop"

$shortcutPath = [IO.Path]::GetFullPath($ShortcutPath)
if (-not $shortcutPath.EndsWith(".lnk", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Shortcut path must end with .lnk"
}

$parent = Split-Path -Parent $shortcutPath
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "Shortcut parent directory was not found: $parent"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = $TargetPath
$shortcut.Arguments = $Arguments
$shortcut.WorkingDirectory = $WorkingDirectory
$shortcut.Save()
