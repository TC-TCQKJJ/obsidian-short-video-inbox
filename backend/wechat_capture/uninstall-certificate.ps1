$ErrorActionPreference = "Stop"

$backendRoot = Split-Path -Parent $PSScriptRoot
$capturePython = Join-Path $env:LOCALAPPDATA "Xiaolou\WechatCapture\venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $capturePython)) {
    throw "Capture environment is not installed."
}

$env:PYTHONPATH = $backendRoot
& $capturePython -c @"
from wechat_capture.security import CapturePaths, uninstall_current_user_ca
removed = uninstall_current_user_ca(CapturePaths())
print("Removed the recorded CurrentUser certificate." if removed else "No recorded certificate was found.")
"@
if ($LASTEXITCODE -ne 0) {
    throw "Certificate removal failed."
}
