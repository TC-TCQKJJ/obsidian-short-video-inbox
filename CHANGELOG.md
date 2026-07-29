# Changelog

All notable changes to this project are documented here.

## 0.6.0 - 2026-07-29

- Added the Windows WeChat Channels process-capture helper.
- Generate a unique per-installation CA instead of using SunnyNet's shared CA.
- Remove SunnyNet's embedded default private key from source builds and verify
  that the release binary does not contain it.
- Limit process attachment, HTTPS decryption, host matching, headers, local
  APIs, and persisted queue data to the capture workflow.
- Match the visible WeChat Channels video instead of accepting preloaded feeds.
- Added an authenticated loopback bridge, DPAPI-protected pending queue, capture
  confirmation UI, regression tests, and Windows build provenance.
- Package the Obsidian plugin, local backend, and verified helper in one Windows
  release archive.

## 0.5.1 - 2026-07-16

- Initial public release for Douyin and WeChat Channels transcription into an
  Obsidian inbox.
