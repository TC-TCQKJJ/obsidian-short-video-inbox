$ErrorActionPreference = "Stop"

$backendRoot = Split-Path -Parent $PSScriptRoot
$capturePython = Join-Path $env:LOCALAPPDATA "Xiaolou\WechatCapture\venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $capturePython)) {
    throw "Capture environment is not installed. Run install.ps1 first."
}

$env:PYTHONPATH = $backendRoot
& $capturePython -m wechat_capture.host
