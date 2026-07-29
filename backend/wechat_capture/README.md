# WeChat Channels Safe Capture

This local helper observes only the narrow WeChat Channels host allowlist,
matches the video currently played in PC WeChat, and submits audio-only work to
the existing local backend after `Ctrl+Alt+S` confirmation.

## Install

Run in PowerShell as the normal Windows user:

```powershell
& "$env:LOCALAPPDATA\Xiaolou\DouyinCapture\backend\wechat_capture\install.ps1"
```

The installer creates an isolated environment, generates a unique local CA and
token, restricts their ACL, and installs only the public CA into
`CurrentUser\Root`. It never prints the token or private key. The process
helper is built from source after removing SunnyNet's built-in shared CA and
private key, and the resulting binary is scanned for that key before release.
It also creates a `微信视频号捕获` desktop shortcut.

## Process capture

The UI starts the source-built process helper with a Windows elevation prompt.
The helper loads this machine's CA, attaches only to `WeChatAppEx.exe`, and
decrypts only the narrow WeChat Channels host allowlist. Other WeChat traffic
is forwarded without HTTPS decryption.

The helper does not start SunnyNet's public proxy listener, modify the Windows
system proxy, or use Clash/TUN.

## Start

Keep the existing local backend available on `127.0.0.1:5050`, then open the
`微信视频号捕获` desktop shortcut. The equivalent PowerShell command is:

```powershell
& "$env:LOCALAPPDATA\Xiaolou\DouyinCapture\backend\wechat_capture\start.ps1"
```

Open the target in PC WeChat and let it play for a few seconds. Press
`Ctrl+Alt+S`, verify title/author/duration/cover, then choose `确认保存`.
Playback can be paused or closed after the confirmation.

If another application already owns `Ctrl+Alt+S`, the host shows one warning
and uses `Ctrl+Alt+Shift+S` for that run.

## Remove The CA

```powershell
& "$env:LOCALAPPDATA\Xiaolou\DouyinCapture\backend\wechat_capture\uninstall-certificate.ps1"
```

The script removes only the exact thumbprint recorded during installation from
`CurrentUser\Root`.

## Security Boundaries

- No proxy listener is started
- Process event bridge: `127.0.0.1:2024` with bearer-token authentication
- No SunnyNet proxy listener or firewall allow rule
- Only `WeChatAppEx.exe` is attached
- No page injection or automated WeChat interaction
- No whole-video file is created
- Signed media URLs and request headers are DPAPI encrypted at rest
- Non-allowlisted hosts are rejected
- Local API calls require the shared token
