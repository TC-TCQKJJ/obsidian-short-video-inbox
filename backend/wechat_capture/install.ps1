$ErrorActionPreference = "Stop"

$backendRoot = Split-Path -Parent $PSScriptRoot
$backendPython = Join-Path (Split-Path -Parent $backendRoot) ".venv\Scripts\python.exe"
$captureRoot = Join-Path $env:LOCALAPPDATA "Xiaolou\WechatCapture"
$capturePython = Join-Path $captureRoot "venv\Scripts\python.exe"
$requirements = Join-Path $PSScriptRoot "requirements.txt"

if (-not (Test-Path -LiteralPath $backendPython)) {
    throw "Backend Python was not found: $backendPython"
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

Write-Host "Installed the Xiaolou capture CA in CurrentUser\Root."
Write-Host "CA thumbprint: $thumbprint"
Write-Host "Capture proxy: 127.0.0.1:2023"
Write-Host "Next: add clash-extend-script.js to Clash, then run start.ps1."
