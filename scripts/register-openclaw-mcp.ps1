param(
    [switch]$SkipSkill
)

$ErrorActionPreference = "Stop"

$installRoot = Join-Path $env:LOCALAPPDATA "Xiaolou\DouyinCapture"
$python = Join-Path $installRoot ".venv\Scripts\python.exe"
$server = Join-Path $installRoot "backend\mcp_server.py"
$skill = Join-Path (Split-Path -Parent $PSScriptRoot) "agent-tools\xiaolou-wechat-capture"
$tools = @(
    "wechat_capture_status",
    "wechat_capture_list",
    "wechat_capture_start",
    "wechat_capture_get",
    "wechat_capture_read_transcript"
) -join ","

if (-not (Test-Path -LiteralPath $python)) {
    throw "Installed backend Python was not found. Run scripts\install-backend.ps1 first."
}
if (-not (Test-Path -LiteralPath $server)) {
    throw "Installed MCP server was not found. Run scripts\install-backend.ps1 first."
}

$openclaw = Get-Command openclaw -ErrorAction SilentlyContinue
if (-not $openclaw) {
    throw "OpenClaw CLI was not found on PATH."
}

& $openclaw.Source mcp add xiaolou-wechat-capture `
    --command $python `
    --arg $server `
    --include $tools
if ($LASTEXITCODE -ne 0) {
    throw "OpenClaw could not register the MCP server."
}

& $openclaw.Source mcp doctor xiaolou-wechat-capture --probe
if ($LASTEXITCODE -ne 0) {
    throw "OpenClaw registered the server, but its live MCP probe failed."
}

if (-not $SkipSkill) {
    & $openclaw.Source skills install $skill --global
    if ($LASTEXITCODE -ne 0) {
        throw "The MCP server works, but the optional OpenClaw skill install failed."
    }
}

Write-Host "OpenClaw can now use the Xiaolou WeChat capture MCP tools."
