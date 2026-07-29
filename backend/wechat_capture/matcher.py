from __future__ import annotations

import dataclasses
import logging
import threading
import time
from urllib.parse import urlparse

from wechat_capture.model import CaptureCandidate


def _media_identity(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    return (parsed.hostname or "").lower(), parsed.path


class CaptureMatcher:
    def __init__(
        self,
        max_age_seconds: float,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.max_age_seconds = max_age_seconds
        self.logger = logger or logging.getLogger(__name__)
        self._lock = threading.RLock()
        self._feeds_by_media: dict[tuple[str, str], CaptureCandidate] = {}
        self._recent_media: dict[
            tuple[str, str],
            tuple[str, dict[str, str], float],
        ] = {}
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
            observation = self._recent_media.get(identity)
            if observation is not None:
                media_url, request_headers, observed_at = observation
                self._matched[identity] = dataclasses.replace(
                    candidate,
                    media_url=media_url,
                    request_headers=request_headers,
                    observed_at=observed_at,
                )

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
            headers = dict(request_headers or {})
            self._recent_media[identity] = (media_url, headers, observed_at)
            candidate = self._feeds_by_media.get(identity)
            if candidate is None:
                return
            self._matched[identity] = dataclasses.replace(
                candidate,
                media_url=media_url,
                request_headers=headers,
                observed_at=observed_at,
            )

    def recent_candidates(self) -> list[CaptureCandidate]:
        with self._lock:
            self._prune_locked(now=time.time())
            candidates = sorted(
                self._matched.values(),
                key=lambda item: item.observed_at,
                reverse=True,
            )
            if not candidates and (self._feeds_by_media or self._recent_media):
                feed_paths = {identity[1] for identity in self._feeds_by_media}
                media_paths = {identity[1] for identity in self._recent_media}
                feed_names = {_path_name(path) for path in feed_paths}
                media_names = {_path_name(path) for path in media_paths}
                self.logger.info(
                    "WeChat candidate match diagnostics: feeds=%d media=%d "
                    "exact=%d path=%d name=%d",
                    len(self._feeds_by_media),
                    len(self._recent_media),
                    len(self._feeds_by_media.keys() & self._recent_media.keys()),
                    len(feed_paths & media_paths),
                    len((feed_names - {""}) & (media_names - {""})),
                )
            return candidates

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
        self._recent_media = {
            identity: observation
            for identity, observation in self._recent_media.items()
            if observation[2] >= cutoff
        }


def _path_name(path: str) -> str:
    return path.rstrip("/").rsplit("/", 1)[-1].lower()
