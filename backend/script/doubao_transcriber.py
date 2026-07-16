from __future__ import annotations

import json
import time
import uuid

import requests

from script.transcriber import TranscriptResult, to_simplified_chinese

SUBMIT_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/submit"
QUERY_URL = "https://openspeech.bytedance.com/api/v3/auc/bigmodel/query"
DEFAULT_RESOURCE_ID = "volc.seedasr.auc"
SUCCESS_CODE = "20000000"
PROCESSING_CODES = {"20000001", "20000002"}


def _headers(
    api_key: str,
    task_id: str,
    *,
    submit: bool,
    resource_id: str,
) -> dict[str, str]:
    headers = {
        "Content-Type": "application/json",
        "X-Api-Key": api_key,
        "X-Api-Resource-Id": resource_id or DEFAULT_RESOURCE_ID,
        "X-Api-Request-Id": task_id,
    }
    if submit:
        headers["X-Api-Sequence"] = "-1"
    return headers


def _status_code(response: requests.Response) -> str:
    return response.headers.get("X-Api-Status-Code", "")


def _raise_api_error(
    response: requests.Response,
    stage: str,
    *,
    resource_id: str,
) -> None:
    code = _status_code(response)
    message = response.headers.get("X-Api-Message", "")
    detail = f"{stage} failed: HTTP {response.status_code}, resource_id={resource_id}"
    if code or message:
        detail += f", code={code or 'unknown'}, message={message or 'unknown'}"
    raise RuntimeError(detail)


def _build_context(title: str, author: str, cover_url: str | None) -> str:
    context_data: list[dict[str, str]] = [
        {
            "text": (
                "这是抖音短视频的中文口语。场景是男朋友用比赛解说、评书、"
                "文学比喻解说女朋友化妆。可能涉及遮瑕、粉底、腮红、修容、"
                "高光、眼妆等词语。"
            )
        }
    ]
    if title:
        context_data.insert(0, {"text": f"视频标题：{title}"})
    if author:
        context_data.insert(1, {"text": f"作者：{author}"})
    if cover_url:
        context_data.append({"image_url": cover_url})
    return json.dumps(
        {"context_type": "dialog_ctx", "context_data": context_data},
        ensure_ascii=False,
    )


def transcribe_audio_url(
    audio_url: str,
    *,
    api_key: str,
    title: str = "",
    author: str = "",
    cover_url: str | None = None,
    resource_id: str = DEFAULT_RESOURCE_ID,
    timeout_seconds: int = 600,
) -> TranscriptResult:
    task_id = str(uuid.uuid4())
    payload = {
        "user": {"uid": "xiaolou-obsidian"},
        "audio": {
            "format": "mp3",
            "url": audio_url,
        },
        "request": {
            "model_name": "bigmodel",
            "enable_itn": True,
            "enable_punc": True,
            "enable_ddc": False,
            "show_utterances": True,
            "corpus": {
                "context": _build_context(title, author, cover_url),
            },
        },
    }

    submit = requests.post(
        SUBMIT_URL,
        headers=_headers(
            api_key,
            task_id,
            submit=True,
            resource_id=resource_id,
        ),
        json=payload,
        timeout=30,
    )
    if submit.status_code < 200 or submit.status_code >= 300:
        _raise_api_error(submit, "Doubao submit", resource_id=resource_id)
    submit_code = _status_code(submit)
    if submit_code and submit_code != SUCCESS_CODE:
        _raise_api_error(submit, "Doubao submit", resource_id=resource_id)

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        query = requests.post(
            QUERY_URL,
            headers=_headers(
                api_key,
                task_id,
                submit=False,
                resource_id=resource_id,
            ),
            json={},
            timeout=30,
        )
        if query.status_code < 200 or query.status_code >= 300:
            _raise_api_error(query, "Doubao query", resource_id=resource_id)

        code = _status_code(query)
        if code in PROCESSING_CODES or not query.content:
            time.sleep(1)
            continue
        if code and code != SUCCESS_CODE:
            _raise_api_error(query, "Doubao query", resource_id=resource_id)

        body = query.json()
        result = body.get("result") or {}
        utterances = result.get("utterances") or []
        segments: list[dict] = []
        lines: list[str] = []
        for utterance in utterances:
            text = to_simplified_chinese(str(utterance.get("text") or "").strip())
            if not text:
                continue
            lines.append(text)
            segments.append(
                {
                    "start": round(float(utterance.get("start_time") or 0) / 1000, 2),
                    "end": round(float(utterance.get("end_time") or 0) / 1000, 2),
                    "text": text,
                }
            )

        full_text = to_simplified_chinese(
            "\n".join(lines) or str(result.get("text") or "").strip()
        )
        if not full_text:
            raise RuntimeError("Doubao returned an empty transcript")
        return TranscriptResult(text=full_text, segments=segments)

    raise TimeoutError("Doubao transcription timed out")
