from __future__ import annotations

import os
import re
from pathlib import Path
from urllib.parse import urlparse

import requests


BACKEND_URL = "http://127.0.0.1:5050"
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
URL_PATTERN = re.compile(r"https?://\S+", re.IGNORECASE)
JOB_STATUSES = {
    "waiting_for_obsidian",
    "processing",
    "completed",
    "failed",
}
TRANSCRIPTION_ENGINES = {"doubao", "whisper"}
WHISPER_MODELS = {"tiny", "base", "small", "medium", "large-v3"}


class CaptureBackendError(RuntimeError):
    """Safe error returned to an agent-facing tool."""


def default_auth_token_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise CaptureBackendError("LOCALAPPDATA is unavailable on this host")
    return Path(local_app_data) / "Xiaolou" / "WechatCapture" / "auth-token"


class WechatCaptureClient:
    def __init__(
        self,
        *,
        backend_url: str = BACKEND_URL,
        token_path: Path | None = None,
        session=None,
    ) -> None:
        self.backend_url = _validate_backend_url(backend_url)
        self.token_path = Path(token_path) if token_path else default_auth_token_path()
        self.session = session or requests.Session()

    def status(self) -> dict:
        health = self._request("GET", "/api/health", authenticated=False)
        jobs = self._request("GET", "/api/wechat-capture/jobs").get("jobs", [])
        counts = {status: 0 for status in sorted(JOB_STATUSES)}
        for job in jobs:
            status = str(job.get("status") or "")
            if status in counts:
                counts[status] += 1
        return {
            "backend": "ready",
            "doubao_api_key_configured": bool(health.get("api_key_configured")),
            "job_counts": counts,
            "unacknowledged_jobs": len(jobs),
        }

    def list_jobs(self, *, status: str = "", limit: int = 10) -> dict:
        normalized_status = str(status or "").strip()
        if normalized_status and normalized_status not in JOB_STATUSES:
            raise CaptureBackendError(
                f"Unsupported job status: {_safe_text(normalized_status)}"
            )
        bounded_limit = _bounded_int(limit, minimum=1, maximum=50, name="limit")
        jobs = self._request("GET", "/api/wechat-capture/jobs").get("jobs", [])
        selected = [
            job
            for job in jobs
            if not normalized_status or job.get("status") == normalized_status
        ]
        selected = list(reversed(selected[-bounded_limit:]))
        return {
            "jobs": [_public_job(job) for job in selected],
            "count": len(selected),
        }

    def get_job(self, job_id: str) -> dict:
        normalized_id = _validate_job_id(job_id)
        payload = self._request(
            "GET",
            f"/api/wechat-capture/jobs/{normalized_id}",
        )
        return {"job": _public_job(payload.get("job") or {})}

    def start_job(
        self,
        job_id: str,
        *,
        transcription_engine: str = "doubao",
        whisper_model: str = "base",
    ) -> dict:
        normalized_id = _validate_job_id(job_id)
        engine = str(transcription_engine or "").strip().lower()
        model = str(whisper_model or "").strip().lower()
        if engine not in TRANSCRIPTION_ENGINES:
            raise CaptureBackendError(f"Unsupported transcription engine: {engine}")
        if model not in WHISPER_MODELS:
            raise CaptureBackendError(f"Unsupported Whisper model: {model}")

        current = self._request(
            "GET",
            f"/api/wechat-capture/jobs/{normalized_id}",
        ).get("job") or {}
        status = current.get("status")
        if status not in {"waiting_for_obsidian", "failed", "processing", "completed"}:
            raise CaptureBackendError(
                f"Job cannot be started from status: {_safe_text(status)}"
            )
        if status in {"processing", "completed"}:
            return {"job": _public_job(current), "started": False}

        payload = self._request(
            "POST",
            f"/api/wechat-capture/jobs/{normalized_id}/start",
            json={
                "transcription_engine": engine,
                "model": model,
                "whisper_fallback": True,
                "doubao_resource_id": "volc.seedasr.auc",
            },
        )
        return {"job": _public_job(payload.get("job") or {}), "started": True}

    def read_transcript(
        self,
        job_id: str,
        *,
        offset: int = 0,
        limit: int = 8000,
    ) -> dict:
        normalized_id = _validate_job_id(job_id)
        start = _bounded_int(offset, minimum=0, maximum=10_000_000, name="offset")
        size = _bounded_int(limit, minimum=1, maximum=12_000, name="limit")
        job = self._request(
            "GET",
            f"/api/wechat-capture/jobs/{normalized_id}",
        ).get("job") or {}
        status = str(job.get("status") or "")
        if status != "completed":
            suffix = f": {_safe_text(job.get('error'))}" if job.get("error") else ""
            raise CaptureBackendError(f"Transcript is not ready; job status is {status}{suffix}")

        result = job.get("result") if isinstance(job.get("result"), dict) else {}
        transcript = str(result.get("text") or "")
        if start > len(transcript):
            raise CaptureBackendError("Transcript offset is past the end of the text")
        end = min(len(transcript), start + size)
        return {
            "job_id": normalized_id,
            "title": str(job.get("title") or ""),
            "author": str(job.get("author") or ""),
            "transcription_engine": str(result.get("transcription_engine") or ""),
            "offset": start,
            "next_offset": end,
            "total_chars": len(transcript),
            "complete": end >= len(transcript),
            "text": transcript[start:end],
        }

    def _request(
        self,
        method: str,
        path: str,
        *,
        authenticated: bool = True,
        json: dict | None = None,
    ) -> dict:
        headers = {"Accept": "application/json"}
        if authenticated:
            headers["X-Xiaolou-Capture-Token"] = self._read_token()
        if json is not None:
            headers["Content-Type"] = "application/json"

        try:
            response = self.session.request(
                method,
                f"{self.backend_url}{path}",
                headers=headers,
                json=json,
                timeout=15,
            )
        except requests.RequestException:
            raise CaptureBackendError(
                "The local capture backend is unavailable on 127.0.0.1:5050"
            ) from None

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code >= 400 or not isinstance(payload, dict):
            error = _safe_text(payload.get("error") if isinstance(payload, dict) else "")
            message = error or f"Local capture backend returned HTTP {response.status_code}"
            raise CaptureBackendError(message)
        if payload.get("success") is not True:
            raise CaptureBackendError(
                _safe_text(payload.get("error")) or "Local capture backend rejected the request"
            )
        return payload

    def _read_token(self) -> str:
        try:
            token = self.token_path.read_text(encoding="utf-8").strip()
        except OSError:
            raise CaptureBackendError(
                "Local capture authentication is unavailable; start the backend once"
            ) from None
        if len(token) < 43:
            raise CaptureBackendError("Local capture authentication token is invalid")
        return token


def _public_job(job: dict) -> dict:
    allowed_fields = (
        "id",
        "capture_id",
        "title",
        "author",
        "duration_ms",
        "status",
        "acknowledged",
        "created_at",
        "updated_at",
        "error",
    )
    public = {key: job.get(key) for key in allowed_fields if key in job}
    if "error" in public:
        public["error"] = _safe_text(public["error"])
    result = job.get("result")
    if isinstance(result, dict):
        public["result"] = {
            key: result.get(key)
            for key in (
                "success",
                "video_id",
                "title",
                "author",
                "platform",
                "content_type",
                "transcription_engine",
            )
            if key in result
        }
        public["transcript_chars"] = len(str(result.get("text") or ""))
    return public


def _validate_backend_url(value: str) -> str:
    parsed = urlparse(str(value or ""))
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
    ):
        raise CaptureBackendError("The agent tool only connects to a loopback HTTP backend")
    return str(value).rstrip("/")


def _validate_job_id(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not JOB_ID_PATTERN.fullmatch(normalized):
        raise CaptureBackendError("job_id must be a 32-character hexadecimal identifier")
    return normalized


def _bounded_int(value, *, minimum: int, maximum: int, name: str) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise CaptureBackendError(f"{name} must be an integer") from None
    if number < minimum or number > maximum:
        raise CaptureBackendError(f"{name} must be between {minimum} and {maximum}")
    return number


def _safe_text(value) -> str:
    text = URL_PATTERN.sub("[redacted-url]", str(value or ""))
    return "".join(char for char in text if char >= " " or char in "\t\n")[:2000]
