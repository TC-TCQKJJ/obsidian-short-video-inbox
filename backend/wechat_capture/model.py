from __future__ import annotations

from dataclasses import dataclass, field
from time import time


@dataclass(frozen=True)
class CaptureCandidate:
    capture_id: str
    title: str
    author: str
    source_url: str
    media_url: str
    cover_url: str = ""
    duration_ms: int = 0
    decrypt_key: int = 0
    request_headers: dict[str, str] = field(default_factory=dict)
    observed_at: float = field(default_factory=time)
    is_active: bool = False

    def public_dict(self) -> dict:
        return {
            "capture_id": self.capture_id,
            "title": self.title,
            "author": self.author,
            "source_url": self.source_url,
            "cover_url": self.cover_url,
            "duration_ms": self.duration_ms,
            "observed_at": self.observed_at,
        }

    def to_private_dict(self) -> dict:
        return {
            **self.public_dict(),
            "media_url": self.media_url,
            "decrypt_key": self.decrypt_key,
            "request_headers": dict(self.request_headers),
        }
