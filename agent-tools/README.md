# Agent tools

The backend exposes five local MCP tools for OpenClaw, Hermes Agent, and other
MCP clients:

- `wechat_capture_status`
- `wechat_capture_list`
- `wechat_capture_start`
- `wechat_capture_get`
- `wechat_capture_read_transcript`

The MCP server uses stdio and connects only to `http://127.0.0.1:5050`. It reads
the existing local authentication token itself. API keys, cookies, certificate
private keys, signed media URLs, request headers, and local output paths are not
part of the tool schemas or results.

## Prerequisites

1. Install the backend with `scripts\install-backend.ps1`.
2. Install and start the Windows WeChat capture helper.
3. Start Obsidian so the local backend is listening on port `5050`.

The installed MCP command is:

```text
command: %LOCALAPPDATA%\Xiaolou\DouyinCapture\.venv\Scripts\python.exe
argument: %LOCALAPPDATA%\Xiaolou\DouyinCapture\backend\mcp_server.py
```

## OpenClaw

Register the MCP server and its operating skill in one step:

```powershell
.\scripts\register-openclaw-mcp.ps1
```

The script runs the equivalent of:

```powershell
$python = Join-Path $env:LOCALAPPDATA "Xiaolou\DouyinCapture\.venv\Scripts\python.exe"
$server = Join-Path $env:LOCALAPPDATA "Xiaolou\DouyinCapture\backend\mcp_server.py"
openclaw mcp add xiaolou-wechat-capture --command $python --arg $server `
  --include "wechat_capture_status,wechat_capture_list,wechat_capture_start,wechat_capture_get,wechat_capture_read_transcript"
openclaw mcp doctor xiaolou-wechat-capture --probe
openclaw skills install .\agent-tools\xiaolou-wechat-capture --global
```

Use a normal `coding` or `messaging` tool profile. OpenClaw's `minimal` profile
does not expose configured MCP tools.

## Hermes Agent

Add the server to `~/.hermes/config.yaml`, replacing the two environment
placeholders with their expanded absolute paths:

```yaml
mcp_servers:
  xiaolou_wechat_capture:
    command: "<LOCALAPPDATA>\\Xiaolou\\DouyinCapture\\.venv\\Scripts\\python.exe"
    args:
      - "<LOCALAPPDATA>\\Xiaolou\\DouyinCapture\\backend\\mcp_server.py"
```

Restart Hermes and verify that the five tools appear. The `SKILL.md` in
`agent-tools/xiaolou-wechat-capture` is optional operating guidance for clients
that support Agent Skills.

## Host boundary

The first release is intentionally Windows-local. If the agent runs on macOS,
do not expose port `5050` on the LAN. Run the MCP process on the Windows capture
host through an authenticated remote-process channel such as OpenClaw Nodes or
SSH stdio after separately hardening that channel.
