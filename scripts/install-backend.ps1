param(
    [string]$InstallRoot = (Join-Path $env:LOCALAPPDATA "Xiaolou\DouyinCapture")
)

$ErrorActionPreference = "Stop"

$repositoryRoot = Split-Path -Parent $PSScriptRoot
$sourceBackend = Join-Path $repositoryRoot "backend"
$targetBackend = Join-Path $InstallRoot "backend"
$venv = Join-Path $InstallRoot ".venv"
$venvPython = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path -LiteralPath $sourceBackend)) {
    throw "Backend source was not found: $sourceBackend"
}

$pythonCommand = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCommand) {
    throw "Python 3.10 or newer is required and must be available on PATH."
}

New-Item -ItemType Directory -Force -Path $targetBackend | Out-Null
Get-ChildItem -Force -LiteralPath $sourceBackend | ForEach-Object {
    Copy-Item -Recurse -Force -LiteralPath $_.FullName -Destination $targetBackend
}

if (-not (Test-Path -LiteralPath $venvPython)) {
    & $pythonCommand.Source -m venv $venv
}

& $venvPython -m pip install --disable-pip-version-check -r (Join-Path $targetBackend "requirements.txt")
if ($LASTEXITCODE -ne 0) {
    throw "Failed to install backend dependencies."
}

Write-Host "Backend installed in $targetBackend"
Write-Host "The Obsidian plugin will start it on 127.0.0.1:5050 when needed."
Write-Host "The optional WeChat capture helper and its certificate are not installed automatically."
