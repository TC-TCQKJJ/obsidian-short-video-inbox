from __future__ import annotations

import gzip
import json
import logging
import zlib
from urllib.parse import urlsplit

from wechat_capture.feed_parser import parse_feed_objects
from wechat_capture.matcher import CaptureMatcher
from wechat_capture.redaction import redact_url
from wechat_capture.security import CapturePaths, is_allowed_host


MAX_RESPONSE_BODY_BYTES = 8 * 1024 * 1024
MEDIA_SUFFIXES = (".mp4", ".m3u8", ".flv")
MEDIA_PATH_NAMES = ("/stodownload",)
SAFE_REQUEST_HEADERS = ("User-Agent", "Referer", "Origin", "Range", "Accept")
CLASH_HEALTH_CHECKS = {("www.gstatic.com", "/generate_204")}


class PassiveWechatCaptureAddon:
    def __init__(
        self,
        matcher: CaptureMatcher,
        *,
        logger: logging.Logger | None = None,
        max_response_body_bytes: int = MAX_RESPONSE_BODY_BYTES,
    ) -> None:
        self.matcher = matcher
        self.logger = logger or logging.getLogger(__name__)
        self.max_response_body_bytes = max_response_body_bytes

    def request(self, flow) -> None:
        request = getattr(flow, "request", None)
        if request is None:
            return

        host = str(getattr(request, "host", "") or "")
        if _is_clash_health_check(host, getattr(request, "path", "")):
            return
        if not is_allowed_host(host):
            self.logger.debug("Blocked WeChat capture request for %s", redact_url(_request_url(request)))
            flow.kill()
            return

        self.record_request_event(
            _request_url(request),
            observed_at=float(getattr(request, "timestamp_start", 0.0) or 0.0),
            request_headers=_safe_request_headers(getattr(request, "headers", None)),
        )

    def record_request_event(
        self,
        url: str,
        *,
        observed_at: float,
        request_headers: dict[str, str] | None = None,
    ) -> None:
        parsed = urlsplit(url)
        if not is_allowed_host(parsed.hostname or ""):
            return
        if not _is_media_path(parsed.path):
            return

        self.matcher.record_media_request(
            url,
            observed_at=observed_at,
            request_headers=_safe_request_headers(request_headers),
        )

    def response(self, flow) -> None:
        request = getattr(flow, "request", None)
        response = getattr(flow, "response", None)
        if request is None or response is None:
            return

        host = str(getattr(request, "host", "") or "")
        if not is_allowed_host(host):
            return

        body = getattr(response, "content", None)
        if body is None:
            body = getattr(response, "raw_content", b"") or b""
        self.record_response_event(
            _request_url(request),
            response_headers=getattr(response, "headers", None),
            body=body,
        )

    def record_response_event(
        self,
        url: str,
        *,
        response_headers,
        body: bytes,
    ) -> None:
        parsed = urlsplit(url)
        if not is_allowed_host(parsed.hostname or ""):
            return

        if len(body) > self.max_response_body_bytes:
            self.logger.debug(
                "Ignored oversized WeChat response for %s",
                redact_url(url),
            )
            return

        body = _decode_content_encoding(response_headers, body)
        if not _looks_like_json(_header_value(response_headers, "Content-Type"), body):
            return

        try:
            payload = json.loads(body)
        except (TypeError, ValueError, UnicodeDecodeError):
            self.logger.debug(
                "Ignored undecodable JSON response for %s",
                redact_url(url),
            )
            return

        self.record_feed_payload(payload)

    def record_feed_payload(self, payload) -> None:
        for candidate in parse_feed_objects(payload):
            self.matcher.record_feed(candidate)


def build_proxy_options(paths: CapturePaths):
    from mitmproxy import options

    proxy_options = options.Options(
        listen_host="127.0.0.1",
        listen_port=2023,
        confdir=str(paths.mitm_dir),
        ssl_insecure=False,
    )
    _ensure_option(
        proxy_options,
        "block_global",
        bool,
        True,
        "Block connections from public IP addresses.",
    )
    _ensure_option(
        proxy_options,
        "connection_strategy",
        str,
        "eager",
        "Determine when upstream connections are established.",
        choices=("eager", "lazy"),
    )
    proxy_options.update(
        block_global=True,
        connection_strategy="eager",
    )
    return proxy_options


def _is_media_path(path: str) -> bool:
    clean_path = urlsplit(str(path or "")).path.lower()
    return clean_path.endswith(MEDIA_SUFFIXES + MEDIA_PATH_NAMES)


def _is_clash_health_check(host: str, path: str) -> bool:
    normalized_host = str(host or "").strip().rstrip(".").lower()
    clean_path = urlsplit(str(path or "")).path
    return (normalized_host, clean_path) in CLASH_HEALTH_CHECKS


def _request_url(request) -> str:
    return str(
        getattr(request, "pretty_url", None)
        or getattr(request, "url", None)
        or ""
    )


def _safe_request_headers(headers) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in SAFE_REQUEST_HEADERS:
        value = _header_value(headers, name)
        if value is not None:
            values[name] = value
    return values


def _header_value(headers, name: str) -> str | None:
    if headers is None:
        return None

    lowered_name = name.lower()
    for header_name, value in headers.items():
        if str(header_name).lower() == lowered_name:
            return str(value)
    return None


def _decode_content_encoding(headers, body: bytes) -> bytes:
    encoding = (_header_value(headers, "Content-Encoding") or "").lower()
    try:
        if "gzip" in encoding:
            return gzip.decompress(body)
        if "deflate" in encoding:
            return zlib.decompress(body)
        if "br" in encoding:
            import brotli

            return brotli.decompress(body)
    except (ImportError, OSError, ValueError, zlib.error):
        return body
    return body


def _looks_like_json(content_type: str | None, body: bytes) -> bool:
    if content_type and "json" in content_type.lower():
        return True

    stripped = body.lstrip()
    return stripped.startswith(b"{") or stripped.startswith(b"[")


def _ensure_option(proxy_options, name: str, typespec, default, help_text: str, *, choices=None) -> None:
    if name in proxy_options:
        return
    proxy_options.add_option(
        name,
        typespec,
        default,
        help_text,
        choices=choices,
    )
