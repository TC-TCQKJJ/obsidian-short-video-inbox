from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from script.paths import OUTPUT_DIR


@dataclass(frozen=True)
class Settings:
    output_dir: Path = OUTPUT_DIR
    whisper_model: str = "small"
    whisper_device: str = "auto"
    whisper_compute_type: str = "default"
    audio_format: str = "wav"
    audio_sample_rate: int = 16000
    skip_transcribe: bool = False
    transcription_engine: str = "whisper"
    doubao_api_key: str = ""
    doubao_resource_id: str = "volc.seedasr.auc"
    whisper_fallback: bool = True
