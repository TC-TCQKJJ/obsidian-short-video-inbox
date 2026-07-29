# Security policy

## Supported versions

Security fixes are provided for the latest release.

## Reporting a vulnerability

Please use
[GitHub private vulnerability reporting](https://github.com/TC-TCQKJJ/obsidian-short-video-inbox/security/advisories/new).
Do not include certificates, private keys, signed media URLs, cookies, tokens,
or private note contents in a public issue.

## Trust boundaries

- The Obsidian plugin and Python backend run as the current Windows user.
- The default backend listens only on `127.0.0.1:5050`.
- The optional WeChat helper requires elevation to start its process driver.
- Installation generates a unique local CA and installs only its public
  certificate into `CurrentUser\Root`.
- The helper attaches only to `WeChatAppEx.exe` and decrypts only the documented
  WeChat Channels allowlist.
- Capture jobs and signed media details are protected with Windows DPAPI.
- No raw MP4 is retained by the normal processing pipeline.

The helper instruments named WeChat Finder functions in scripts served by
`res.wx.qq.com` to obtain the minimum decoded feed metadata required for the
current video. It does not export cookies, automate WeChat, or load remote
third-party scripts.

## Removing the local CA

Run:

```powershell
& "$env:LOCALAPPDATA\Xiaolou\DouyinCapture\backend\wechat_capture\uninstall-certificate.ps1"
```

The script removes only the exact thumbprint recorded during installation.

## Known residual risk

SunnyNet process-driver mode owns an internal relay listener. The application
bridge is restricted to authenticated loopback traffic, but constraining every
driver-owned relay bind address remains a hardening item.
