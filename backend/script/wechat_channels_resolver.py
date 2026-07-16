from __future__ import annotations

import hashlib
import html
import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qs, unquote, urlparse

import requests


WECHAT_CHANNELS_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

WECHAT_CHANNELS_URL_RE = re.compile(
    r"https?://(?:channels\.weixin\.qq\.com|finder\.video\.qq\.com|"
    r"weixin110\.qq\.com|mp\.weixin\.qq\.com|weixin\.qq\.com/sph)[^\s\]]*",
    re.I,
)

MEDIA_URL_RE = re.compile(
    r"https?(?::|%3A)(?:/|%2F){2}[^\s\"'<>\\]+?"
    r"(?:\.mp4|\.m3u8|\.flv)(?:[^\s\"'<>\\]*)?",
    re.I,
)

MEDIA_KEY_RE = re.compile(
    r'"(?:playUrl|play_url|mediaUrl|media_url|videoUrl|video_url|'
    r'liveUrl|live_url|replayUrl|replay_url|url)"\s*:\s*"([^"]+)"',
    re.I,
)
WECHAT_MEDIA_HOST_SUFFIXES = ("qq.com", "wxlivecdn.com")


@dataclass
class WechatChannelsContentMeta:
    aweme_id: str
    title: str
    author: str
    source_url: str
    content_type: Literal["video", "image"] = "video"
    aweme_type: int | None = None
    download_url: str = ""
    cover_url: str | None = None
    image_urls: list[str] = field(default_factory=list)
    platform: str = "wechat_channels"


class WechatChannelsResolveError(Exception):
    pass


def is_wechat_channels_share(text: str) -> bool:
    value = str(text)
    return bool(WECHAT_CHANNELS_URL_RE.search(value)) or is_wechat_media_url(value)


def extract_wechat_channels_url(share_text: str) -> str:
    value = str(share_text).strip()
    match = WECHAT_CHANNELS_URL_RE.search(value)
    if not match:
        match = MEDIA_URL_RE.search(value)
        if not match:
            raise WechatChannelsResolveError("No WeChat Channels URL found in input")
        media_url = _decode_url(match.group(0)).rstrip("/.,;)]")
        if not _is_wechat_media_host(media_url):
            raise WechatChannelsResolveError("No WeChat Channels URL found in input")
        return media_url
    return match.group(0).rstrip("/.,;)]")


def _session() -> requests.Session:
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": WECHAT_CHANNELS_UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return session


def _decode_url(value: str) -> str:
    decoded = html.unescape(value).strip()
    for _ in range(3):
        previous = decoded
        decoded = unquote(decoded)
        try:
            decoded = json.loads(f'"{decoded}"')
        except json.JSONDecodeError:
            decoded = decoded.replace("\\/", "/").replace("\\u0026", "&")
        if decoded == previous:
            break
    return decoded


def _is_wechat_media_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == suffix or host.endswith(f".{suffix}") for suffix in WECHAT_MEDIA_HOST_SUFFIXES)


def is_wechat_media_url(text: str) -> bool:
    match = MEDIA_URL_RE.search(str(text))
    if not match:
        return False
    return _is_wechat_media_host(_decode_url(match.group(0)))


def _candidate_media_urls(html_text: str) -> list[str]:
    candidates: list[str] = []
    seen: set[str] = set()

    def add(raw: str) -> None:
        url = _decode_url(raw)
        if not re.search(r"\.(?:mp4|m3u8|flv)(?:\?|$)", url, re.I):
            return
        if url in seen:
            return
        seen.add(url)
        candidates.append(url)

    for match in MEDIA_KEY_RE.finditer(html_text):
        add(match.group(1))
    for match in MEDIA_URL_RE.finditer(html_text):
        add(match.group(0))
    return candidates


def _extract_meta_content(html_text: str, key: str) -> str:
    pattern = re.compile(
        rf'<meta[^>]+(?:property|name)=["\']{re.escape(key)}["\'][^>]+content=["\']([^"\']+)["\']',
        re.I,
    )
    match = pattern.search(html_text)
    return html.unescape(match.group(1)).strip() if match else ""


def _extract_title(html_text: str) -> str:
    title = _extract_meta_content(html_text, "og:title")
    if title:
        return title
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, re.I | re.S)
    if match:
        return re.sub(r"\s+", " ", html.unescape(match.group(1))).strip()
    return "微信视频号视频"


def _extract_author(html_text: str) -> str:
    for key in ("nickname", "nickName", "author", "finderNickname"):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', html_text, re.I)
        if match:
            return html.unescape(_decode_url(match.group(1)))
    return ""


def _extract_cover(html_text: str) -> str | None:
    cover = _extract_meta_content(html_text, "og:image")
    if cover:
        return cover
    for key in ("coverUrl", "cover_url", "thumbUrl", "thumb_url", "image"):
        match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', html_text, re.I)
        if match:
            return _decode_url(match.group(1))
    return None


def _id_from_url(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    for key in (
        "feed_id",
        "feedid",
        "object_id",
        "objectid",
        "exportkey",
        "mid",
        "id",
        "nonceid",
    ):
        values = query.get(key)
        if values and values[0]:
            return re.sub(r"\W+", "", values[0])[:48]
    path_match = re.search(r"/([A-Za-z0-9_-]{8,})(?:/)?$", parsed.path)
    if path_match:
        return path_match.group(1)[:48]
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def parse_wechat_channels_html(
    html_text: str,
    page_url: str,
    original_share: str,
) -> WechatChannelsContentMeta:
    candidates = _candidate_media_urls(html_text)
    if not candidates:
        raise WechatChannelsResolveError(
            "WeChat Channels media URL was not exposed in the share page. "
            "This link may need a browser-assisted resolver."
        )
    return WechatChannelsContentMeta(
        aweme_id=_id_from_url(page_url or original_share),
        title=_extract_title(html_text),
        author=_extract_author(html_text),
        source_url=original_share,
        download_url=candidates[0],
        cover_url=_extract_cover(html_text),
    )


def resolve_wechat_channels_share(share_text: str) -> WechatChannelsContentMeta:
    share_url = extract_wechat_channels_url(share_text)
    if re.search(r"\.(?:mp4|m3u8|flv)(?:\?|$)", share_url, re.I):
        return WechatChannelsContentMeta(
            aweme_id=_id_from_url(share_url),
            title="微信视频号视频",
            author="",
            source_url=share_url,
            download_url=share_url,
        )

    response = _session().get(share_url, allow_redirects=True, timeout=30)
    response.raise_for_status()
    return parse_wechat_channels_html(response.text, str(response.url), share_url)
