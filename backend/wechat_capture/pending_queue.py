from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from script.windows_dpapi import protect_bytes, unprotect_bytes
from wechat_capture.model import CaptureCandidate


class PendingCaptureQueue:
    def __init__(
        self,
        path: Path,
        *,
        max_items: int = 20,
        protect=protect_bytes,
        unprotect=unprotect_bytes,
    ) -> None:
        self.path = Path(path)
        self.max_items = max_items
        self._protect = protect
        self._unprotect = unprotect
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def items(self) -> list[CaptureCandidate]:
        return self._read()

    def add(self, candidate: CaptureCandidate) -> None:
        self.enqueue(candidate)

    def enqueue(self, candidate: CaptureCandidate) -> None:
        items = [item for item in self._read() if item.capture_id != candidate.capture_id]
        items.append(candidate)
        self._write(items[-self.max_items :])

    def flush(self, submitter: Callable[[CaptureCandidate], bool]) -> None:
        remaining: list[CaptureCandidate] = []
        for item in self._read():
            try:
                if submitter(item):
                    continue
            except Exception:
                pass
            remaining.append(item)
        self._write(remaining)

    def _read(self) -> list[CaptureCandidate]:
        if not self.path.exists():
            return []
        try:
            plaintext = self._unprotect(self.path.read_bytes())
            payload = json.loads(plaintext.decode("utf-8"))
            return [_candidate_from_dict(item) for item in payload]
        except Exception:
            self._quarantine_corrupt_file()
            return []

    def _write(self, items: list[CaptureCandidate]) -> None:
        payload = json.dumps(
            [item.to_private_dict() for item in items],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        ciphertext = self._protect(payload)
        temp_path = self.path.with_name(f"{self.path.name}.tmp")
        temp_path.write_bytes(ciphertext)
        temp_path.replace(self.path)

    def _quarantine_corrupt_file(self) -> None:
        if not self.path.exists():
            return
        corrupt_path = self.path.with_name(
            f"{self.path.name}.corrupt-{time.time_ns()}"
        )
        self.path.replace(corrupt_path)


def _candidate_from_dict(value: dict) -> CaptureCandidate:
    return CaptureCandidate(
        capture_id=str(value["capture_id"]),
        title=str(value["title"]),
        author=str(value["author"]),
        source_url=str(value["source_url"]),
        media_url=str(value["media_url"]),
        cover_url=str(value.get("cover_url", "")),
        duration_ms=int(value.get("duration_ms", 0)),
        decrypt_key=int(value.get("decrypt_key", 0)),
        request_headers={
            str(name): str(header_value)
            for name, header_value in dict(value.get("request_headers", {})).items()
        },
        observed_at=float(value.get("observed_at", 0.0)),
    )
