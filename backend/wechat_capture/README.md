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
`CurrentUser\Root`. It never prints the token or private key.

## Clash

Add `clash-extend-script.js` as a Clash extension script and reload the Clash
configuration. It creates `ChannelsCapture` at `127.0.0.1:2023` and routes only
the listed exact/suffix domains. It intentionally does not route all of
`qq.com`.

## Start

Keep the existing local backend available on `127.0.0.1:5050`, then run:

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

- Proxy listener: localhost only
- No page injection or automated WeChat interaction
- No whole-video file is created
- Signed media URLs and request headers are DPAPI encrypted at rest
- Non-allowlisted hosts are rejected
- Local API calls require the shared token
