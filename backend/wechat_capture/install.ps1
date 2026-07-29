$ErrorActionPreference = "Stop"

$backendRoot = Split-Path -Parent $PSScriptRoot
$backendPython = Join-Path (Split-Path -Parent $backendRoot) ".venv\Scripts\python.exe"
$captureRoot = Join-Path $env:LOCALAPPDATA "Xiaolou\WechatCapture"
$capturePython = Join-Path $captureRoot "venv\Scripts\python.exe"
$requirements = Join-Path $PSScriptRoot "requirements.txt"
$startScript = Join-Path $PSScriptRoot "start.ps1"
$helperSource = Join-Path $backendRoot "wechat_process_capture\dist\wechat-process-capture.exe"
$helperTarget = Join-Path $captureRoot "bin\wechat-process-capture.exe"
$desktopShortcut = Join-Path ([Environment]::GetFolderPath("Desktop")) "微信视频号捕获.lnk"

if (-not (Test-Path -LiteralPath $backendPython)) {
    throw "Backend Python was not found: $backendPython"
}
if (-not (Test-Path -LiteralPath $helperSource)) {
    throw "The source-built process capture helper was not found: $helperSource"
}

New-Item -ItemType Directory -Force -Path $captureRoot | Out-Null
$currentSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
& icacls.exe $captureRoot /inheritance:r /grant:r "*${currentSid}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restrict the capture directory ACL."
}
if (Get-ChildItem -LiteralPath $captureRoot -Force -ErrorAction SilentlyContinue) {
    & icacls.exe (Join-Path $captureRoot "*") /reset /T /C | Out-Null
}

if (-not (Test-Path -LiteralPath $capturePython)) {
    & $backendPython -m venv (Join-Path $captureRoot "venv")
}

& $capturePython -m pip install --disable-pip-version-check -r $requirements
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the isolated capture requirements."
}

$env:PYTHONPATH = $backendRoot
& $capturePython -m wechat_capture.host --init
if ($LASTEXITCODE -ne 0) {
    throw "Failed to initialize the capture host."
}

& icacls.exe (Join-Path $captureRoot "*") /reset /T /C | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restrict the generated capture files."
}
& icacls.exe $captureRoot /inheritance:r /grant:r "*${currentSid}:(OI)(CI)F" | Out-Null
if ($LASTEXITCODE -ne 0) {
    throw "Failed to restrict the capture directory ACL."
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $helperTarget) | Out-Null
Copy-Item -Force -LiteralPath $helperSource -Destination $helperTarget

$result = & $capturePython -c @"
from wechat_capture.security import CapturePaths, install_current_user_ca
print(install_current_user_ca(CapturePaths()))
"@
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install the CurrentUser root certificate."
}

$thumbprint = ($result | Select-Object -Last 1).Trim()
if ($thumbprint -notmatch "^[0-9A-F]{40}$") {
    throw "Certificate installation did not return a valid thumbprint."
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($desktopShortcut)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$startScript`""
$shortcut.WorkingDirectory = $backendRoot
$shortcut.Save()

Write-Host "Installed the Xiaolou capture CA in CurrentUser\Root."
Write-Host "CA thumbprint: $thumbprint"
Write-Host "Process helper SHA256: $((Get-FileHash -Algorithm SHA256 -LiteralPath $helperTarget).Hash)"
Write-Host "Capture bridge: 127.0.0.1:2024"
Write-Host "Desktop shortcut: $desktopShortcut"
Write-Host "Next: open the desktop shortcut and approve the process-driver prompt."
