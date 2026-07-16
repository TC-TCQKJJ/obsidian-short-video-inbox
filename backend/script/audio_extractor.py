from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class FFmpegNotFoundError(Exception):
    pass


def ensure_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise FFmpegNotFoundError(
            "未找到 ffmpeg。请先安装：macOS 可执行 `brew install ffmpeg`"
        )
    return path


def extract_audio(
    video_path: Path,
    audio_path: Path,
    *,
    sample_rate: int = 16000,
    audio_format: str = "wav",
) -> Path:
    return convert_audio(
        video_path,
        audio_path,
        sample_rate=sample_rate,
        audio_format=audio_format,
    )


def convert_audio(
    source_path: Path,
    audio_path: Path,
    *,
    sample_rate: int = 16000,
    audio_format: str = "mp3",
) -> Path:
    ensure_ffmpeg()
    audio_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(source_path),
        "-map",
        "0:a:0?",
        "-vn",
        "-acodec",
        "pcm_s16le" if audio_format == "wav" else "libmp3lame",
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-map_metadata",
        "-1",
    ]
    if audio_format != "wav":
        cmd.extend(["-b:a", "64k"])
    cmd.append(str(audio_path))
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 抽音频失败:\n{proc.stderr}")
    return audio_path
