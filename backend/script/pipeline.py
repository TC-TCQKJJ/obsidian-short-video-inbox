from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from script.audio_extractor import extract_audio
from script.config import Settings
from script.doubao_transcriber import transcribe_audio_url
from script.douyin_resolver import DouyinContentMeta, resolve_douyin_share
from script.downloader import download_file
from script.fns_audio_host import host_audio
from script.transcriber import to_simplified_chinese, transcribe_audio
from script.utils import build_output_dir
from script.wechat_channels_resolver import (
    is_wechat_channels_share,
    resolve_wechat_channels_share,
)


def resolve_content_meta(share_text: str) -> DouyinContentMeta:
    """仅通过分享页 SSR 公开数据解析，不依赖登录 Cookie。"""
    if is_wechat_channels_share(share_text):
        return resolve_wechat_channels_share(share_text)
    return resolve_douyin_share(share_text)


def _write_transcript_files(
    out_dir: Path,
    meta: DouyinContentMeta,
    body_text: str,
    *,
    segments: list[dict] | None = None,
    whisper_model: str | None = None,
    transcription_engine: str | None = None,
    transcription_error: str | None = None,
    extra_files: dict[str, str] | None = None,
) -> None:
    transcript_path = out_dir / "transcript.txt"
    transcript_json_path = out_dir / "transcript_segments.json"
    meta_path = out_dir / "meta.json"

    type_label = "图文" if meta.content_type == "image" else "视频"
    header_lines = [
        f"类型: {type_label}",
        f"标题: {to_simplified_chinese(meta.title)}",
        f"作者: {to_simplified_chinese(meta.author or '未知')}",
        f"作品ID: {meta.aweme_id}",
        f"来源: {meta.source_url}",
        f"处理时间: {datetime.now(timezone.utc).astimezone().isoformat()}",
        "",
        "--- 文案 ---",
        "",
    ]
    transcript_path.write_text(
        "\n".join(header_lines) + body_text + "\n",
        encoding="utf-8",
    )
    transcript_json_path.write_text(
        json.dumps(
            {
                "meta": asdict(meta),
                "segments": segments or [],
                "full_text": body_text,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    files = {"transcript": transcript_path.name, **(extra_files or {})}
    meta_path.write_text(
        json.dumps(
            {
                **asdict(meta),
                "processed_at": datetime.now(timezone.utc).isoformat(),
                "whisper_model": whisper_model,
                "transcription_engine": transcription_engine,
                "transcription_error": transcription_error,
                "files": files,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _process_image_note(
    meta: DouyinContentMeta,
    out_dir: Path,
    cfg: Settings,
) -> None:
    """图文：直接从 desc 提取配文字案，并下载图片。"""
    images_dir = out_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)

    urls_path = out_dir / "image_urls.txt"
    urls_path.write_text("\n".join(meta.image_urls) + "\n", encoding="utf-8")

    for i, url in enumerate(meta.image_urls, start=1):
        ext = ".jpg"
        if ".png" in url.lower():
            ext = ".png"
        elif ".webp" in url.lower():
            ext = ".webp"
        dest = images_dir / f"{i:02d}{ext}"
        if not dest.exists():
            download_file(url, dest)

    body = to_simplified_chinese(meta.title)
    _write_transcript_files(
        out_dir,
        meta,
        body,
        extra_files={
            "images_dir": images_dir.name,
            "image_urls": urls_path.name,
        },
    )


def _process_video(
    meta: DouyinContentMeta,
    out_dir: Path,
    cfg: Settings,
) -> None:
    video_path = out_dir / "video.mp4"
    audio_path = out_dir / f"audio.{cfg.audio_format}"
    download_url_path = out_dir / "download_url.txt"

    download_url_path.write_text(meta.download_url + "\n", encoding="utf-8")

    audio_only_source = (
        ".m3u8" in meta.download_url.lower()
        or getattr(meta, "platform", "douyin") == "wechat_channels"
    )
    if not audio_only_source and not video_path.exists():
        download_file(meta.download_url, video_path)

    if not audio_path.exists():
        audio_source = meta.download_url if audio_only_source else video_path
        extract_audio(
            audio_source,
            audio_path,
            sample_rate=cfg.audio_sample_rate,
            audio_format=cfg.audio_format,
        )

    if cfg.skip_transcribe:
        _write_transcript_files(
            out_dir,
            meta,
            "",
            transcription_engine="none",
            extra_files={
                **({} if audio_only_source else {"video": video_path.name}),
                "audio": audio_path.name,
                "download_url": download_url_path.name,
            },
        )
        return

    engine = cfg.transcription_engine
    transcription_error = None
    if engine == "doubao":
        if not cfg.doubao_api_key:
            raise ValueError("Doubao API key is required")
        try:
            with host_audio(audio_path) as audio_url:
                result = transcribe_audio_url(
                    audio_url,
                    api_key=cfg.doubao_api_key,
                    title=meta.title,
                    author=meta.author,
                    cover_url=meta.cover_url,
                    resource_id=cfg.doubao_resource_id,
                )
        except Exception as error:
            if not cfg.whisper_fallback:
                raise
            transcription_error = str(error)
            engine = "whisper"
            result = transcribe_audio(
                audio_path,
                model_size=cfg.whisper_model,
                device=cfg.whisper_device,
                compute_type=cfg.whisper_compute_type,
            )
    else:
        engine = "whisper"
        result = transcribe_audio(
            audio_path,
            model_size=cfg.whisper_model,
            device=cfg.whisper_device,
            compute_type=cfg.whisper_compute_type,
        )

    _write_transcript_files(
        out_dir,
        meta,
        result.text,
        segments=result.segments,
        whisper_model=cfg.whisper_model if engine == "whisper" else None,
        transcription_engine=engine,
        transcription_error=transcription_error,
        extra_files={
            **({} if audio_only_source else {"video": video_path.name}),
            "audio": audio_path.name,
            "download_url": download_url_path.name,
        },
    )


def process_douyin_share(
    share_text: str,
    settings: Settings | None = None,
) -> Path:
    """
    完整流水线：
    - 视频：下载 → 抽音频 → Whisper 转写
    - 图文：提取 desc 配文字案 + 下载图片（无需 Whisper）
    """
    cfg = settings or Settings()
    cfg.output_dir.mkdir(parents=True, exist_ok=True)

    meta = resolve_content_meta(share_text)
    out_dir = build_output_dir(cfg.output_dir, meta.aweme_id, meta.title)
    out_dir.mkdir(parents=True, exist_ok=True)

    if meta.content_type == "image":
        _process_image_note(meta, out_dir, cfg)
    else:
        _process_video(meta, out_dir, cfg)

    return out_dir


# 兼容旧调用名
resolve_video_meta = resolve_content_meta
