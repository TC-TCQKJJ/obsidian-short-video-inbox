from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import zhconv


@dataclass
class TranscriptResult:
    text: str
    segments: list[dict]


def to_simplified_chinese(text: str) -> str:
    """Whisper 中文常输出繁体，统一转为简体。"""
    return zhconv.convert(text, "zh-cn")


def transcribe_audio(
    audio_path: Path,
    *,
    model_size: str = "small",
    device: str = "auto",
    compute_type: str = "default",
    language: str = "zh",
) -> TranscriptResult:
    """
    使用本地 faster-whisper 转写（无需任何云端 API）。
    首次运行会自动下载模型权重。
    """
    from faster_whisper import WhisperModel

    model = WhisperModel(
        model_size,
        device=device,
        compute_type=compute_type,
    )
    segments_iter, info = model.transcribe(
        str(audio_path),
        language=language,
        vad_filter=True,
        beam_size=5,
    )

    lines: list[str] = []
    segment_records: list[dict] = []
    for seg in segments_iter:
        text = to_simplified_chinese(seg.text.strip())
        if not text:
            continue
        lines.append(text)
        segment_records.append(
            {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": text,
            }
        )

    full_text = to_simplified_chinese("\n".join(lines))
    return TranscriptResult(text=full_text, segments=segment_records)
