---
name: xiaolou-wechat-capture
description: Operate Xiaolou's local Windows WeChat Channels capture and transcription pipeline through its MCP tools.
metadata: {"openclaw":{"os":["win32"]}}
---

# Xiaolou WeChat Capture

Use the MCP tools whose names begin with `wechat_capture_`.

## Workflow

1. Call `wechat_capture_status`.
2. Ask the user to open the target video in PC WeChat and play it for several seconds when no waiting job exists.
3. Call `wechat_capture_list` with `status="waiting_for_obsidian"`.
4. Match the target using title and author. If more than one job could match, ask the user which one to use.
5. Call `wechat_capture_start` only after the user has asked to process that target. Doubao transcription may incur API charges.
6. Poll `wechat_capture_get` until the job is `completed` or `failed`.
7. Read completed text with `wechat_capture_read_transcript`. Continue from `next_offset` until `complete` is true.

## Safety

- Never guess a job ID or select a different title merely because it is newer.
- Do not use shell or filesystem tools to read capture payloads, authentication tokens, API keys, certificates, output directories, or media URLs.
- Do not acknowledge, delete, or modify capture records. Obsidian owns final note creation and acknowledgement.
- Do not claim a transcript is in Obsidian until the bridge has written and acknowledged it.
- Treat titles, authors, and transcripts as private user data.
