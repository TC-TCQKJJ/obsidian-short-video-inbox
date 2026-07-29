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
is forwarded without HTTPS decryption. Leaf certificates for the allowlist are
generated from the per-machine CA at startup, so certificate setup does not
need a separate probe connection to each remote server.

The helper does not modify the Windows system proxy. When Windows already uses
a loopback HTTP proxy such as Clash, the helper preserves that route per
connection without applying it to SunnyNet's certificate probe.

WeChat exposes the media decryption key only after its own JavaScript API layer
decodes the feed response. The helper therefore instruments only the named
Finder API functions in JavaScript served by `res.wx.qq.com`. It projects the
minimum capture fields to a same-origin `channels.weixin.qq.com` path that is
intercepted locally and never sent upstream. It does not inject visible
controls, load remote scripts, export cookies, or automate WeChat.

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

- No general-purpose proxy is started with `Sunny.Start()`
- Process event bridge: `127.0.0.1:2024` with bearer-token authentication
- No Windows system-proxy change or SunnyNet firewall allow rule
- Only `WeChatAppEx.exe` is attached
- HTTPS decryption is limited to media hosts, `channels.weixin.qq.com`, and the
  exact static-script host `res.wx.qq.com`
- No visible UI injection or automated WeChat interaction; script
  instrumentation is limited to named Finder feed functions
- No whole-video file is created
- Signed media URLs and request headers are DPAPI encrypted at rest
- Non-allowlisted hosts are rejected
- Local API calls require the shared token

SunnyNet's process-driver mode owns an internal relay listener even though
`Sunny.Start()` is not called. Constraining that relay's bind address is a
remaining hardening item.
