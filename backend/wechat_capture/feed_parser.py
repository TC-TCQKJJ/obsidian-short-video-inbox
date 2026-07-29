from __future__ import annotations

import json
from urllib.parse import urlencode, urlsplit

from wechat_capture.model import CaptureCandidate
from wechat_capture.security import is_allowed_host, is_media_host


MEDIA_SUFFIXES = (".mp4", ".m3u8", ".flv")
MEDIA_PATH_NAMES = ("/stodownload",)


def parse_feed_objects(payload) -> list[CaptureCandidate]:
    items: list[CaptureCandidate] = []
    seen: set[tuple[str, str]] = set()
    _visit_node(payload, items, seen)
    return items


def _visit_node(node, items: list[CaptureCandidate], seen: set[tuple[str, str]]) -> None:
    if isinstance(node, dict):
        candidate = _candidate_from_node(node)
        if candidate is not None:
            identity = (candidate.capture_id, candidate.media_url)
            if identity not in seen:
                seen.add(identity)
                items.append(candidate)
        for value in node.values():
            _visit_node(value, items, seen)
        return

    if isinstance(node, list):
        for value in node:
            _visit_node(value, items, seen)


def _candidate_from_node(node: dict) -> CaptureCandidate | None:
    normalized = _normalized_candidate_from_node(node)
    if normalized is not None:
        return normalized

    object_id = str(node.get("id") or node.get("objectId") or "").strip()
    description = _object_description(node.get("objectDesc"))
    media_items = description.get("media") or []
    media, media_url = _select_media(media_items)

    if (
        not object_id
        or not isinstance(description, dict)
        or not isinstance(media, dict)
        or not media_url
    ):
        return None

    contact = node.get("contact") or {}
    title = str(description.get("description") or "").strip()
    author = str(node.get("nickname") or "").strip()
    if isinstance(contact, dict):
        author = author or str(
            contact.get("nickname") or contact.get("nickName") or ""
        ).strip()

    return CaptureCandidate(
        capture_id=object_id,
        title=title,
        author=author,
        source_url=_build_source_url(object_id, node),
        media_url=media_url,
        cover_url=str(media.get("coverUrl") or "").strip(),
        duration_ms=_to_int(media.get("duration")),
        decrypt_key=_to_int(media.get("decodeKey")),
    )


def _select_media(media_items) -> tuple[dict, str]:
    if not isinstance(media_items, list):
        return {}, ""
    for media in media_items:
        if not isinstance(media, dict):
            continue
        base_url = str(media.get("url") or "").strip()
        token = str(media.get("urlToken") or "").strip()
        if not base_url or not token:
            continue
        media_url = f"{base_url}{token}"
        if _is_allowed_media_url(media_url):
            return media, media_url
    return {}, ""


def _normalized_candidate_from_node(node: dict) -> CaptureCandidate | None:
    schema = node.get("schema")
    if schema not in {"xiaolou_capture_v1", "xiaolou_capture_active_v1"}:
        return None

    capture_id = str(node.get("capture_id") or "").strip()
    media_url = str(node.get("media_url") or "").strip()
    context_texts = _normalized_context_texts(node.get("context_texts"))
    is_active = schema == "xiaolou_capture_active_v1"
    if is_active:
        if not _is_allowed_media_url(media_url):
            media_url = ""
        if not capture_id and not media_url and not context_texts:
            return None
    elif not capture_id:
        return None
    elif not _is_allowed_media_url(media_url):
        return None

    return CaptureCandidate(
        capture_id=capture_id,
        title=str(node.get("title") or "视频号视频").strip(),
        author=str(node.get("author") or "").strip(),
        source_url=_build_source_url(
            capture_id,
            {"nonceId": node.get("nonce_id")},
        ),
        media_url=media_url,
        cover_url=str(node.get("cover_url") or "").strip(),
        duration_ms=_to_int(node.get("duration_ms")),
        decrypt_key=_to_int(node.get("decrypt_key")),
        is_active=is_active,
        context_texts=context_texts,
    )


def _normalized_context_texts(value) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()

    texts: list[str] = []
    for item in value[:32]:
        if not isinstance(item, str):
            continue
        text = " ".join(item.split())[:4096]
        if len(text) < 2 or text in texts:
            continue
        texts.append(text)
    return tuple(texts)


def _is_allowed_media_url(raw_url: str) -> bool:
    parsed = urlsplit(raw_url)
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if not is_allowed_host(parsed.hostname or ""):
        return False
    path = parsed.path.lower()
    return path.endswith(MEDIA_SUFFIXES + MEDIA_PATH_NAMES) or (
        is_media_host(parsed.hostname or "") and path not in {"", "/"}
    )


def _object_description(value) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {}
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _build_source_url(object_id: str, node: dict) -> str:
    query = {"feed_id": object_id}
    nonce = str(node.get("objectNonceId") or node.get("nonceId") or "").strip()
    if nonce:
        query["nonce"] = nonce
    return f"https://channels.weixin.qq.com/web/pages/feed?{urlencode(query)}"


def _to_int(value) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return 0
