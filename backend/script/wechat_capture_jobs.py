from __future__ import annotations

import json
import re
import shutil
import subprocess
import threading
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests

from script.doubao_transcriber import transcribe_audio_url
from script.fns_audio_host import host_audio
from script.transcriber import transcribe_audio
from script.wechat_media_decrypt import WechatDecryptor
from script.windows_dpapi import protect_bytes, unprotect_bytes
from wechat_capture.redaction import redact_url
from wechat_capture.security import is_allowed_host


SAFE_HEADERS = {"user-agent", "referer", "origin", "range", "accept"}
PRIVATE_KEYS = {"media_url", "decrypt_key", "request_headers", "download_url"}
VIDEO_SUFFIXES = {".mp4", ".flv", ".m3u8"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class JobStore:
    def __init__(
        self,
        jobs_dir: Path,
        output_dir: Path,
        *,
        protect=protect_bytes,
        unprotect=unprotect_bytes,
        processor=None,
    ) -> None:
        self.jobs_dir = Path(jobs_dir)
        self.output_dir = Path(output_dir)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self._protect = protect
        self._unprotect = unprotect
        self._processor = processor or process_wechat_capture
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="wechat-capture-job",
        )
        self._lock = threading.RLock()
        self._records: dict[str, dict] = {}
        self._futures: dict[str, Future] = {}
        self._load()

    def create(self, candidate: dict) -> dict:
        capture_id = str(candidate["capture_id"])
        with self._lock:
            for record in self._records.values():
                if (
                    record.get("capture_id") == capture_id
                    and not record.get("acknowledged")
                ):
                    return _copy_public(record)

            job_id = uuid.uuid4().hex
            timestamp = _now()
            record = {
                "id": job_id,
                "capture_id": capture_id,
                "title": str(candidate.get("title", "")),
                "author": str(candidate.get("author", "")),
                "source_url": str(candidate.get("source_url", "")),
                "cover_url": str(candidate.get("cover_url", "")),
                "duration_ms": int(candidate.get("duration_ms", 0)),
                "observed_at": float(candidate.get("observed_at", 0.0)),
                "status": "waiting_for_obsidian",
                "acknowledged": False,
                "created_at": timestamp,
                "updated_at": timestamp,
                "output_dir": "",
                "error": "",
                "result": {},
            }
            payload = json.dumps(
                candidate,
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            self._atomic_write(self._payload_path(job_id), self._protect(payload))
            self._records[job_id] = record
            self._write_record(record)
            return _copy_public(record)

    def list(self) -> list[dict]:
        with self._lock:
            return [
                _copy_public(record)
                for record in sorted(
                    self._records.values(),
                    key=lambda item: item.get("created_at", ""),
                )
                if not record.get("acknowledged")
            ]

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(job_id)
            return _copy_public(record) if record else None

    def start(self, job_id: str, settings: dict | None = None) -> dict:
        with self._lock:
            record = self._require(job_id)
            if record["status"] in {"processing", "completed"}:
                return _copy_public(record)
            if record.get("acknowledged"):
                raise ValueError("Acknowledged jobs cannot be started")

            payload_path = self._payload_path(job_id)
            if not payload_path.exists():
                raise FileNotFoundError("Encrypted job payload is missing")
            payload = json.loads(
                self._unprotect(payload_path.read_bytes()).decode("utf-8")
            )
            out_dir = self.output_dir / f"wechat-{job_id}"
            record.update(
                status="processing",
                updated_at=_now(),
                output_dir=str(out_dir.resolve()),
                error="",
            )
            self._write_record(record)
            self._futures[job_id] = self._executor.submit(
                self._run,
                job_id,
                payload,
                dict(settings or {}),
                out_dir,
            )
            return _copy_public(record)

    def wait(self, job_id: str, timeout: float | None = None) -> dict:
        future = self._futures.get(job_id)
        if future is not None:
            future.result(timeout=timeout)
        record = self.get(job_id)
        if record is None:
            raise KeyError(job_id)
        return record

    def acknowledge(self, job_id: str) -> dict:
        with self._lock:
            record = self._require(job_id)
            if record["status"] == "processing":
                raise ValueError("Processing jobs cannot be acknowledged")
            self._payload_path(job_id).unlink(missing_ok=True)
            record.update(acknowledged=True, updated_at=_now())
            self._write_record(record)
            return _copy_public(record)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _run(
        self,
        job_id: str,
        payload: dict,
        settings: dict,
        out_dir: Path,
    ) -> None:
        try:
            result = self._processor(payload, settings, out_dir)
        except Exception as exc:
            with self._lock:
                record = self._records[job_id]
                record.update(
                    status="failed",
                    updated_at=_now(),
                    error=_sanitize_error(exc),
                )
                self._write_record(record)
            return

        with self._lock:
            record = self._records[job_id]
            record.update(
                status="completed",
                updated_at=_now(),
                result=_sanitize_result(result),
                error="",
            )
            self._write_record(record)

    def _load(self) -> None:
        for path in self.jobs_dir.glob("*.json"):
            try:
                record = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if record.get("status") == "processing":
                record["status"] = "waiting_for_obsidian"
                record["updated_at"] = _now()
                self._write_record(record)
            if record.get("id"):
                self._records[str(record["id"])] = record

    def _require(self, job_id: str) -> dict:
        record = self._records.get(job_id)
        if record is None:
            raise KeyError(job_id)
        return record

    def _record_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.json"

    def _payload_path(self, job_id: str) -> Path:
        return self.jobs_dir / f"{job_id}.payload"

    def _write_record(self, record: dict) -> None:
        data = json.dumps(record, ensure_ascii=False, indent=2).encode("utf-8")
        self._atomic_write(self._record_path(record["id"]), data)

    @staticmethod
    def _atomic_write(path: Path, data: bytes) -> None:
        temp = path.with_suffix(path.suffix + ".tmp")
        temp.write_bytes(data)
        temp.replace(path)


def process_wechat_capture(payload: dict, settings: dict, out_dir: Path) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    for path in out_dir.iterdir():
        if path.suffix.lower() in VIDEO_SUFFIXES:
            raise RuntimeError("Video files are not allowed in capture output")

    audio_path = out_dir / "audio.mp3"
    extract_audio_stream(payload, audio_path)
    cover_path = _download_cover(payload.get("cover_url", ""), out_dir)
    text, segments, engine = _transcribe(audio_path, payload, settings)

    meta = {
        "aweme_id": payload["capture_id"],
        "video_id": payload["capture_id"],
        "title": payload.get("title", ""),
        "author": payload.get("author", ""),
        "source_url": payload.get("source_url", ""),
        "cover_url": payload.get("cover_url", ""),
        "content_type": "video",
        "platform": "wechat_channels",
        "transcription_engine": engine,
        "files": {
            "audio": audio_path.name,
            "transcript": "transcript.txt",
            **({"cover": cover_path.name} if cover_path else {}),
        },
    }
    (out_dir / "transcript.txt").write_text(text + "\n", encoding="utf-8")
    (out_dir / "transcript_segments.json").write_text(
        json.dumps(
            {"meta": meta, "segments": segments, "full_text": text},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (out_dir / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {
        "success": True,
        "video_id": payload["capture_id"],
        "title": payload.get("title", ""),
        "author": payload.get("author", ""),
        "platform": "wechat_channels",
        "content_type": "video",
        "text": text,
        "segments": segments,
        "transcription_engine": engine,
        "out_dir": str(out_dir.resolve()),
        "audio_path": str(audio_path.resolve()),
    }


def extract_audio_stream(payload: dict, audio_path: Path) -> Path:
    media_url = str(payload["media_url"])
    parsed = urlsplit(media_url)
    if parsed.scheme not in {"http", "https"} or not is_allowed_host(parsed.hostname or ""):
        raise ValueError("Media URL host is not allowed")
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    headers = _safe_headers(payload.get("request_headers", {}))

    try:
        if parsed.path.lower().endswith(".m3u8") and not int(
            payload.get("decrypt_key", 0)
        ):
            return _extract_hls(media_url, headers, audio_path)
        return _extract_http_stream(
            media_url,
            headers,
            int(payload.get("decrypt_key", 0)),
            audio_path,
        )
    except Exception:
        audio_path.unlink(missing_ok=True)
        raise


def _extract_http_stream(
    media_url: str,
    headers: dict[str, str],
    decrypt_key: int,
    audio_path: Path,
) -> Path:
    ffmpeg = _ffmpeg()
    command = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        "pipe:0",
        "-map",
        "0:a:0?",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    upstream = requests.get(
        media_url,
        headers=headers,
        stream=True,
        timeout=(15, 120),
    )
    process = None
    try:
        upstream.raise_for_status()
        process = subprocess.Popen(
            command,
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        decryptor = WechatDecryptor(decrypt_key) if decrypt_key else None
        assert process.stdin is not None
        for chunk in upstream.iter_content(chunk_size=256 * 1024):
            if not chunk:
                continue
            process.stdin.write(decryptor.transform(chunk) if decryptor else chunk)
        process.stdin.close()
        error = process.stderr.read() if process.stderr is not None else b""
        return_code = process.wait()
        if return_code != 0:
            raise RuntimeError(
                f"FFmpeg audio extraction failed for {_safe_location(media_url)}: "
                f"{error.decode('utf-8', errors='replace')[-1000:]}"
            )
    except BrokenPipeError as exc:
        if process is not None:
            process.kill()
        raise RuntimeError(
            f"FFmpeg closed input for {_safe_location(media_url)}"
        ) from exc
    finally:
        upstream.close()

    return audio_path


def _extract_hls(
    media_url: str,
    headers: dict[str, str],
    audio_path: Path,
) -> Path:
    header_text = "".join(f"{name}: {value}\r\n" for name, value in headers.items())
    command = [
        _ffmpeg(),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        *([] if not header_text else ["-headers", header_text]),
        "-i",
        media_url,
        "-map",
        "0:a:0?",
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-codec:a",
        "libmp3lame",
        "-b:a",
        "64k",
        str(audio_path),
    ]
    completed = subprocess.run(command, capture_output=True)
    if completed.returncode != 0:
        raise RuntimeError(
            f"FFmpeg HLS extraction failed for {_safe_location(media_url)}: "
            f"{completed.stderr.decode('utf-8', errors='replace')[-1000:]}"
        )
    return audio_path


def _transcribe(audio_path: Path, payload: dict, settings: dict):
    if settings.get("skip_transcribe"):
        return "", [], "none"

    engine = str(settings.get("transcription_engine") or "whisper")
    if engine == "doubao":
        api_key = str(settings.get("doubao_api_key") or "")
        if not api_key:
            raise ValueError("Doubao API key is required")
        with host_audio(audio_path) as audio_url:
            result = transcribe_audio_url(
                audio_url,
                api_key=api_key,
                title=str(payload.get("title", "")),
                author=str(payload.get("author", "")),
                cover_url=str(payload.get("cover_url", "")) or None,
                resource_id=str(
                    settings.get("doubao_resource_id") or "volc.seedasr.auc"
                ),
            )
        return result.text, result.segments, "doubao"

    result = transcribe_audio(
        audio_path,
        model_size=str(settings.get("model") or "base"),
        device="cpu",
        compute_type="int8",
    )
    return result.text, result.segments, "whisper"


def _download_cover(url: str, out_dir: Path) -> Path | None:
    parsed = urlsplit(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not is_allowed_host(parsed.hostname or ""):
        return None
    response = requests.get(url, stream=True, timeout=(5, 15))
    try:
        response.raise_for_status()
        destination = out_dir / "cover.jpg"
        size = 0
        with destination.open("wb") as handle:
            for chunk in response.iter_content(64 * 1024):
                size += len(chunk)
                if size > 5 * 1024 * 1024:
                    handle.close()
                    destination.unlink(missing_ok=True)
                    return None
                handle.write(chunk)
        return destination
    finally:
        response.close()


def _safe_headers(headers) -> dict[str, str]:
    safe: dict[str, str] = {}
    for name, value in dict(headers or {}).items():
        normalized_name = str(name).lower()
        normalized_value = str(value)
        if (
            normalized_name in SAFE_HEADERS
            and "\r" not in normalized_value
            and "\n" not in normalized_value
        ):
            safe[str(name)] = normalized_value
    return safe


def _ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("FFmpeg is not installed")
    return path


def _safe_location(url: str) -> str:
    parsed = urlsplit(url)
    return f"{parsed.hostname or ''}{parsed.path}"


def _sanitize_error(error: Exception) -> str:
    text = str(error)
    text = re.sub(
        r"https?://[^\s]+",
        lambda match: redact_url(match.group(0)),
        text,
    )
    return text[:2000]


def _sanitize_result(result) -> dict:
    if not isinstance(result, dict):
        return {}
    return _sanitize_mapping(result)


def _sanitize_mapping(value):
    if isinstance(value, dict):
        return {
            str(key): _sanitize_mapping(item)
            for key, item in value.items()
            if str(key).lower() not in PRIVATE_KEYS
        }
    if isinstance(value, list):
        return [_sanitize_mapping(item) for item in value]
    return value


def _copy_public(record: dict) -> dict:
    return json.loads(json.dumps(record, ensure_ascii=False))
