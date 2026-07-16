from __future__ import annotations

import re
from pathlib import Path


def sanitize_filename(name: str, max_len: int = 80) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\n\r\t]', "_", name).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    if not cleaned:
        cleaned = "untitled"
    if len(cleaned) > max_len:
        cleaned = cleaned[:max_len].rstrip()
    return cleaned


def build_output_dir(base: Path, aweme_id: str, title: str) -> Path:
    folder_name = f"{aweme_id}_{sanitize_filename(title)}"
    return base / folder_name
