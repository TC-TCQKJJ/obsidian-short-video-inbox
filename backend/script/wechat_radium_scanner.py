from __future__ import annotations

import html
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse


CHANNELS_URL_RE = re.compile(
    rb"https://(?:channels\.weixin\.qq\.com/(?:web/pages/(?:feed|live)|finder-preview/pages/sph)\?[^\s\"'<>\\{}\x00]+|weixin\.qq\.com/sph/[^\s\"'<>\\{}\x00]+)",
    re.I,
)
MEDIA_URL_RE = re.compile(
    rb"https?://[^\s\"'<>\\{}\x00]+?\.(?:mp4|m3u8|flv)(?:\?[^\s\"'<>\\{}\x00]*)?",
    re.I,
)
WECHAT_MEDIA_HOST_SUFFIXES = ("qq.com", "wxlivecdn.com")
ANCHOR_QUERY_KEYS = {
    "feed_id",
    "feedid",
    "object_id",
    "objectid",
    "oid",
    "nonceid",
    "mid",
    "id",
    "exportkey",
}
SKIP_SUFFIXES = {".wxapkg", ".js", ".css", ".png", ".jpg", ".jpeg", ".webp", ".gif"}
SKIP_NAMES = {
    "Favicons",
    "Favicons-journal",
    "History",
    "History-journal",
    "Visited Links",
    "Cookies",
    "Cookies-journal",
}
ANCHOR_PROFILE_WINDOW_SECONDS = 30 * 60


@dataclass(frozen=True)
class WechatRadiumLink:
    url: str
    path: str
    modified_at: float
    size: int
    offset: int = 0


def default_radium_root() -> Path:
    appdata = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    return appdata / "Tencent" / "xwechat" / "radium"


def _normalize_url(raw: bytes) -> str:
    value = raw.decode("utf-8", errors="ignore")
    value = html.unescape(value)
    for _ in range(2):
        previous = value
        value = unquote(value)
        if value == previous:
            break
    return value.rstrip(".,;)]")


def _normalize_text(value: str) -> str:
    normalized = html.unescape(value)
    for _ in range(2):
        previous = normalized
        normalized = unquote(normalized)
        if normalized == previous:
            break
    return normalized


def _decode_file_text(data: bytes) -> str:
    return _normalize_text(data.decode("utf-8", errors="ignore"))


def _anchor_terms(anchor_url: str | None) -> list[str]:
    if not anchor_url:
        return []

    normalized = _normalize_text(anchor_url).rstrip(".,;)]")
    terms: list[str] = []

    def add(term: str) -> None:
        term = term.strip()
        if len(term) >= 4 and term not in terms:
            terms.append(term)

    add(normalized)
    parsed = urlparse(normalized)
    for segment in parsed.path.split("/"):
        segment = unquote(segment)
        if re.fullmatch(r"[A-Za-z0-9_-]{8,}", segment):
            add(segment)
    for part in parsed.query.split("&"):
        key, separator, value = part.partition("=")
        if not separator:
            continue
        key = unquote(key).lower()
        value = unquote(value)
        if key not in ANCHOR_QUERY_KEYS or not value:
            continue
        add(f"{key}={value}")
        if len(value) >= 10:
            add(value)
    return terms


def _contains_anchor(data: bytes, anchor_terms: list[str]) -> bool:
    if not anchor_terms:
        return False
    text = _decode_file_text(data)
    return any(term in text for term in anchor_terms)


def _profile_root(path: Path) -> str:
    parts = path.parts
    for index, part in enumerate(parts):
        if part == "profiles" and index + 1 < len(parts):
            return str(Path(*parts[: index + 2]))
    return str(path.parent)


def _is_wechat_media_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in WECHAT_MEDIA_HOST_SUFFIXES)


def _should_scan(path: Path, *, since: float, max_size: int) -> bool:
    try:
        stat = path.stat()
    except OSError:
        return False
    if stat.st_mtime < since or stat.st_size <= 0 or stat.st_size > max_size:
        return False
    if (
        path.name.upper() == "LOCK"
        or path.name in SKIP_NAMES
        or path.suffix.lower() in SKIP_SUFFIXES
    ):
        return False
    return True


def find_recent_wechat_channels_links(
    root: Path | None = None,
    *,
    minutes: int = 240,
    limit: int = 10,
    max_size: int = 20 * 1024 * 1024,
) -> list[WechatRadiumLink]:
    scan_root = root or default_radium_root()
    if not scan_root.is_dir():
        return []

    since = time.time() - minutes * 60
    matches: list[WechatRadiumLink] = []
    seen: set[str] = set()
    for path in scan_root.rglob("*"):
        if not path.is_file() or not _should_scan(path, since=since, max_size=max_size):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        matches_in_file = [
            (_normalize_url(match.group(0)), match.start())
            for match in CHANNELS_URL_RE.finditer(data)
        ]
        if not matches_in_file:
            continue
        stat = path.stat()
        for url, offset in matches_in_file:
            if url in seen:
                continue
            seen.add(url)
            matches.append(
                WechatRadiumLink(
                    url=url,
                    path=str(path),
                    modified_at=stat.st_mtime,
                    size=stat.st_size,
                    offset=offset,
                )
            )

    matches.sort(key=lambda item: (item.modified_at, item.offset), reverse=True)
    return matches[:limit]


def find_recent_wechat_channels_media_urls(
    root: Path | None = None,
    *,
    minutes: int = 240,
    limit: int = 10,
    max_size: int = 20 * 1024 * 1024,
    anchor_url: str | None = None,
    require_anchor: bool = False,
) -> list[WechatRadiumLink]:
    scan_root = root or default_radium_root()
    if not scan_root.is_dir():
        return []

    since = time.time() - minutes * 60
    matches: list[WechatRadiumLink] = []
    anchored_matches: list[WechatRadiumLink] = []
    profile_matches: list[tuple[float, WechatRadiumLink]] = []
    seen: set[str] = set()
    anchor_terms = _anchor_terms(anchor_url)
    anchor_profiles: dict[str, float] = {}
    if anchor_terms:
        for path in scan_root.rglob("*"):
            if not path.is_file() or not _should_scan(path, since=since, max_size=max_size):
                continue
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if _contains_anchor(data, anchor_terms):
                profile = _profile_root(path)
                stat = path.stat()
                anchor_profiles[profile] = max(anchor_profiles.get(profile, 0), stat.st_mtime)

    for path in scan_root.rglob("*"):
        if not path.is_file() or not _should_scan(path, since=since, max_size=max_size):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        anchor_matched = _contains_anchor(data, anchor_terms)
        profile_anchor_mtime = anchor_profiles.get(_profile_root(path), 0)
        matches_in_file = []
        for match in MEDIA_URL_RE.finditer(data):
            url = _normalize_url(match.group(0))
            if _is_wechat_media_url(url):
                matches_in_file.append((url, match.start()))
        if not matches_in_file:
            continue
        stat = path.stat()
        for url, offset in matches_in_file:
            if url in seen:
                continue
            seen.add(url)
            item = WechatRadiumLink(
                url=url,
                path=str(path),
                modified_at=stat.st_mtime,
                size=stat.st_size,
                offset=offset,
            )
            if anchor_matched:
                anchored_matches.append(item)
            elif profile_anchor_mtime and abs(stat.st_mtime - profile_anchor_mtime) <= ANCHOR_PROFILE_WINDOW_SECONDS:
                profile_matches.append((abs(stat.st_mtime - profile_anchor_mtime), item))
            matches.append(item)

    if anchored_matches:
        anchored_matches.sort(key=lambda item: (item.modified_at, item.offset), reverse=True)
        return anchored_matches[:limit]
    if profile_matches:
        profile_matches.sort(key=lambda pair: (pair[0], -pair[1].modified_at, -pair[1].offset))
        return [item for _, item in profile_matches[:limit]]
    if require_anchor:
        return []
    matches.sort(key=lambda item: (item.modified_at, item.offset), reverse=True)
    return matches[:limit]
