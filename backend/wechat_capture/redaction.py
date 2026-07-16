from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


_REDACTED = "<redacted>"
_SECRET_HEADER_NAMES = {"authorization", "cookie", "referer"}


def redact_url(url: str) -> str:
    parts = urlsplit(str(url or ""))
    return urlunsplit(
        (
            parts.scheme,
            parts.netloc,
            parts.path,
            _REDACTED if parts.query else "",
            "",
        )
    )


def redact_headers(headers) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for name, value in _header_items(headers):
        redacted[name] = _REDACTED if _is_secret_header(name) else str(value)
    return redacted


def _header_items(headers) -> list[tuple[str, str]]:
    if headers is None:
        return []

    items = None
    try:
        items = headers.items(multi=True)
    except TypeError:
        items = headers.items()
    except AttributeError:
        items = headers.items()

    return [(str(name), str(value)) for name, value in items]


def _is_secret_header(name: str) -> bool:
    normalized = str(name or "").strip().lower()
    return normalized in _SECRET_HEADER_NAMES or (
        normalized.startswith("x-") and "token" in normalized
    )
