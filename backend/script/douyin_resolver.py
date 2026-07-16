from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import unquote

import requests

# 分享页对移动端 UA 会 SSR 注入公开数据，无需登录 Cookie
SHARE_PAGE_UA = (
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/17.0 Mobile/15E148 Safari/604.1"
)

ROUTER_DATA_RE = re.compile(
    r"window\._ROUTER_DATA\s*=\s*(\{.+)",
    re.DOTALL,
)
RENDER_DATA_RE = re.compile(
    r'<script id="RENDER_DATA" type="application/json">([^<]+)</script>',
)

# 抖音图文类型（分享页 SSR 内 aweme_type）
IMAGE_AWEME_TYPES = {2, 68}


@dataclass
class DouyinContentMeta:
    aweme_id: str
    title: str
    author: str
    source_url: str
    content_type: Literal["video", "image"] = "video"
    aweme_type: int | None = None
    download_url: str = ""
    cover_url: str | None = None
    image_urls: list[str] = field(default_factory=list)


# 兼容旧名
DouyinVideoMeta = DouyinContentMeta


class DouyinResolveError(Exception):
    pass


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update(
        {
            "User-Agent": SHARE_PAGE_UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
    )
    return s


def expand_share_url(share_text: str) -> str:
    """从分享文案或短链中提取抖音链接。"""
    text = share_text.strip()
    match = re.search(
        r"https?://(?:v\.douyin\.com|www\.douyin\.com|www\.iesdouyin\.com|m\.douyin\.com)[^\s\]]*",
        text,
    )
    if not match:
        raise DouyinResolveError("未在输入中找到抖音链接")
    return match.group(0).rstrip("/.,;)")


def normalize_to_share_page(url: str) -> str:
    """
    www.douyin.com/note|video 页面不含 SSR 数据，需转为 iesdouyin 分享页。
    """
    note = re.search(r"https?://(?:www\.)?douyin\.com/note/(\d+)", url)
    if note:
        return f"https://www.iesdouyin.com/share/note/{note.group(1)}/"
    video = re.search(r"https?://(?:www\.)?douyin\.com/video/(\d+)", url)
    if video:
        return f"https://www.iesdouyin.com/share/video/{video.group(1)}/"
    return url


def resolve_share_page(session: requests.Session, share_url: str) -> tuple[str, str]:
    """
    跟随短链到分享页。分享链接携带 region/u_code 等参数，相当于临时访问凭证。
    返回 (最终页面 URL, HTML)。
    """
    resp = session.get(share_url, allow_redirects=True, timeout=30)
    resp.raise_for_status()
    return str(resp.url), resp.text


def extract_aweme_id(page_url: str, html: str | None = None) -> str:
    patterns = [
        r"/video/(\d+)",
        r"/note/(\d+)",
        r"/share/video/(\d+)",
        r"/share/note/(\d+)",
        r"modal_id=(\d+)",
        r"item_ids=(\d+)",
        r'"aweme_id"\s*:\s*"?(\d+)"?',
        r'"itemId"\s*:\s*"?(\d+)"?',
    ]
    for pat in patterns:
        m = re.search(pat, page_url)
        if m:
            return m.group(1)
    if html:
        for pat in patterns:
            m = re.search(pat, html)
            if m:
                return m.group(1)
    raise DouyinResolveError(f"无法从分享页解析作品 ID: {page_url}")


def _parse_router_data(html: str) -> dict[str, Any] | None:
    m = ROUTER_DATA_RE.search(html)
    if not m:
        return None
    raw = m.group(1).split("</script>")[0].rstrip().rstrip(";")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return None


def _parse_render_data(html: str) -> dict[str, Any] | None:
    m = RENDER_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(unquote(m.group(1)))
    except json.JSONDecodeError:
        return None


def _find_item_list(obj: Any) -> list[dict[str, Any]]:
    """在嵌套 JSON 中定位 item_list[0]（视频/图文公开结构）。"""
    if isinstance(obj, dict):
        if "item_list" in obj and isinstance(obj["item_list"], list) and obj["item_list"]:
            first = obj["item_list"][0]
            if isinstance(first, dict) and (
                "aweme_id" in first or "video" in first or "images" in first
            ):
                return obj["item_list"]
        for v in obj.values():
            found = _find_item_list(v)
            if found:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _find_item_list(item)
            if found:
                return found
    return []


def _pick_url_from_image_node(img: dict[str, Any]) -> str | None:
    url_list = img.get("url_list") or []
    if url_list:
        return str(url_list[-1])
    download_list = img.get("download_url_list") or []
    if download_list:
        return str(download_list[-1])
    return None


def _extract_image_urls(item: dict[str, Any]) -> list[str]:
    """从 images / image_post_info 提取无水印图片 CDN 地址。"""
    urls: list[str] = []
    seen: set[str] = set()

    def add(u: str | None) -> None:
        if u and u not in seen:
            seen.add(u)
            urls.append(u)

    for img in item.get("images") or []:
        if isinstance(img, dict):
            add(_pick_url_from_image_node(img))

    post = item.get("image_post_info") or {}
    if isinstance(post, dict):
        for img in post.get("images") or []:
            if isinstance(img, dict):
                add(_pick_url_from_image_node(img))

    return urls


def _has_playable_video(item: dict[str, Any]) -> bool:
    video = item.get("video") or {}
    if not isinstance(video, dict):
        return False
    play_addr = video.get("play_addr") or video.get("playAddr") or {}
    if not isinstance(play_addr, dict):
        return False
    return bool(play_addr.get("uri") or play_addr.get("url_list"))


def is_image_note(item: dict[str, Any]) -> bool:
    aweme_type = item.get("aweme_type")
    if aweme_type in IMAGE_AWEME_TYPES:
        return True
    image_urls = _extract_image_urls(item)
    return bool(image_urls) and not _has_playable_video(item)


def _build_no_watermark_url(play_addr: dict[str, Any]) -> str:
    uri = play_addr.get("uri") or ""
    url_list = play_addr.get("url_list") or []

    if uri:
        return (
            f"https://aweme.snssdk.com/aweme/v1/play/"
            f"?video_id={uri}&ratio=720p&line=0"
        )

    if url_list:
        return str(url_list[0]).replace("playwm", "play")

    raise DouyinResolveError("分享页内嵌数据中未找到视频播放地址")


def _meta_from_aweme_item(item: dict[str, Any], source_url: str) -> DouyinContentMeta:
    aweme_id = str(item.get("aweme_id") or item.get("awemeId") or "")
    desc = (item.get("desc") or item.get("caption") or "").strip() or f"douyin_{aweme_id}"
    aweme_type = item.get("aweme_type")

    author = ""
    author_info = item.get("author") or {}
    if isinstance(author_info, dict):
        author = author_info.get("nickname") or author_info.get("unique_id") or ""

    if is_image_note(item):
        image_urls = _extract_image_urls(item)
        if not image_urls:
            raise DouyinResolveError("识别为图文，但未找到图片地址")
        return DouyinContentMeta(
            aweme_id=aweme_id,
            title=desc,
            author=author,
            source_url=source_url,
            content_type="image",
            aweme_type=aweme_type,
            download_url="",
            cover_url=image_urls[0],
            image_urls=image_urls,
        )

    video = item.get("video") or {}
    play_addr = video.get("play_addr") or video.get("playAddr") or {}
    if not isinstance(play_addr, dict):
        raise DouyinResolveError("视频节点缺少 play_addr")

    download_url = _build_no_watermark_url(play_addr)

    cover = None
    for key in ("cover", "origin_cover", "dynamic_cover"):
        cover_info = video.get(key) or {}
        if isinstance(cover_info, dict):
            covers = cover_info.get("url_list") or []
            if covers:
                cover = str(covers[0])
                break

    for br in video.get("bit_rate") or []:
        if not isinstance(br, dict):
            continue
        br_play = br.get("play_addr") or {}
        if isinstance(br_play, dict) and br_play.get("url_list"):
            u = str(br_play["url_list"][0])
            if "playwm" not in u and ("douyinvod" in u or "bytecdn" in u):
                download_url = u
                break

    return DouyinContentMeta(
        aweme_id=aweme_id,
        title=desc,
        author=author,
        source_url=source_url,
        content_type="video",
        aweme_type=aweme_type,
        download_url=download_url,
        cover_url=cover,
        image_urls=[],
    )


def parse_share_page_html(html: str, page_url: str, original_share: str) -> DouyinContentMeta:
    """
    从分享页 HTML 提取 SSR 注入的公开数据（_ROUTER_DATA / RENDER_DATA）。
    视频：play_addr → CDN；图文：desc + images[]，无需语音转写。
    """
    for parser in (_parse_router_data, _parse_render_data):
        payload = parser(html)
        if not payload:
            continue
        items = _find_item_list(payload)
        if items:
            meta = _meta_from_aweme_item(items[0], original_share)
            if not meta.aweme_id:
                meta = DouyinContentMeta(
                    aweme_id=extract_aweme_id(page_url, html),
                    title=meta.title,
                    author=meta.author,
                    source_url=original_share,
                    content_type=meta.content_type,
                    aweme_type=meta.aweme_type,
                    download_url=meta.download_url,
                    cover_url=meta.cover_url,
                    image_urls=meta.image_urls,
                )
            return meta

    raise DouyinResolveError(
        "分享页未找到内嵌公开数据（_ROUTER_DATA / RENDER_DATA）。请确认链接有效。"
    )


def resolve_douyin_share(share_text: str) -> DouyinContentMeta:
    """解析抖音分享链接（视频或图文）。"""
    session = _session()
    share_url = expand_share_url(share_text)
    fetch_url = normalize_to_share_page(share_url)
    page_url, html = resolve_share_page(session, fetch_url)
    return parse_share_page_html(html, page_url, share_url)
