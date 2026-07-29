$ErrorActionPreference = "Stop"

$backendRoot = Split-Path -Parent $PSScriptRoot
$startScript = Join-Path $PSScriptRoot "start.ps1"
$shortcutName = (-join [char[]](0x5FAE, 0x4FE1, 0x89C6, 0x9891, 0x53F7, 0x6355, 0x83B7)) + ".lnk"
$shortcutPath = Join-Path ([Environment]::GetFolderPath("Desktop")) $shortcutName
$shortcutPath = [IO.Path]::GetFullPath($shortcutPath)
if (-not $shortcutPath.EndsWith(".lnk", [StringComparison]::OrdinalIgnoreCase)) {
    throw "Shortcut path must end with .lnk"
}

$parent = Split-Path -Parent $shortcutPath
if (-not (Test-Path -LiteralPath $parent -PathType Container)) {
    throw "Shortcut parent directory was not found: $parent"
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($shortcutPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
$shortcut.WorkingDirectory = $backendRoot
$shortcut.Save()
