from __future__ import annotations

import dataclasses
import threading
import time
from urllib.parse import urlparse

from wechat_capture.model import CaptureCandidate


def _media_identity(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower(), parsed.path


class CaptureMatcher:
    def __init__(self, max_age_seconds: float) -> None:
        self.max_age_seconds = max_age_seconds
        self._lock = threading.RLock()
        self._feeds_by_media: dict[tuple[str, str], CaptureCandidate] = {}
        self._matched: dict[tuple[str, str], CaptureCandidate] = {}

    @property
    def feed_count(self) -> int:
        with self._lock:
            self._prune_locked(now=time.time())
            return len(self._feeds_by_media)

    def record_feed(self, candidate: CaptureCandidate) -> None:
        identity = _media_identity(candidate.media_url)
        if not identity[0] or not identity[1]:
            return
        with self._lock:
            self._prune_locked(now=candidate.observed_at)
            self._feeds_by_media[identity] = candidate

    def record_media_request(
        self,
        media_url: str,
        *,
        observed_at: float,
        request_headers: dict[str, str] | None = None,
    ) -> None:
        identity = _media_identity(media_url)
        with self._lock:
            self._prune_locked(now=observed_at)
            candidate = self._feeds_by_media.get(identity)
            if candidate is None:
                return
            self._matched[identity] = dataclasses.replace(
                candidate,
                media_url=media_url,
                request_headers=dict(request_headers or {}),
                observed_at=observed_at,
            )

    def recent_candidates(self) -> list[CaptureCandidate]:
        with self._lock:
            self._prune_locked(now=time.time())
            return sorted(
                self._matched.values(),
                key=lambda item: item.observed_at,
                reverse=True,
            )

    def _prune_locked(self, *, now: float) -> None:
        cutoff = now - self.max_age_seconds
        self._feeds_by_media = {
            identity: candidate
            for identity, candidate in self._feeds_by_media.items()
            if candidate.observed_at >= cutoff
        }
        self._matched = {
            identity: candidate
            for identity, candidate in self._matched.items()
            if candidate.observed_at >= cutoff
        }
