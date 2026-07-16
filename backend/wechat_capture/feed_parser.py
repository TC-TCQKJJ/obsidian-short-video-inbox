from __future__ import annotations

from urllib.parse import urlencode

from wechat_capture.model import CaptureCandidate


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
    object_id = str(node.get("id") or node.get("objectId") or "").strip()
    description = node.get("objectDesc") or {}
    media_items = description.get("media") or []
    media = media_items[0] if media_items else {}
    base_url = str(media.get("url") or "").strip()
    token = str(media.get("urlToken") or "").strip()

    if (
        not object_id
        or not isinstance(description, dict)
        or not isinstance(media, dict)
        or not base_url
        or not token
    ):
        return None

    contact = node.get("contact") or {}
    title = str(description.get("description") or "").strip()
    author = ""
    if isinstance(contact, dict):
        author = str(contact.get("nickname") or contact.get("nickName") or "").strip()

    media_url = f"{base_url}{token}"
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
