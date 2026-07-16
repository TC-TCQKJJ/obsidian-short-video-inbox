from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

CONFIG_PATH = (
    Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    / "Xiaolou"
    / "DouyinCapture"
    / "fns-bridge.json"
)


def _load_config() -> dict[str, str]:
    if not CONFIG_PATH.is_file():
        raise RuntimeError(f"Fast Note Sync bridge config not found: {CONFIG_PATH}")
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8-sig"))


def _run(command: list[str], *, timeout: int = 120) -> None:
    proc = subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout).strip()
        raise RuntimeError(f"Audio host command failed: {' '.join(command)}\n{detail}")


@contextmanager
def host_audio(audio_path: Path):
    config = _load_config()
    public_base_url = (
        config.get("audio_public_base_url") or config["server_url"]
    ).rstrip("/")
    public_path = config.get("audio_public_path", "/doubao-audio").strip("/")
    ssh_host = config.get("audio_ssh_host", "resume-server")
    remote_dir = config.get("audio_remote_dir", "/opt/doubao-audio").rstrip("/")
    suffix = audio_path.suffix.lower() or ".mp3"
    remote_name = f"{int(time.time())}-{uuid.uuid4().hex}{suffix}"
    remote_path = f"{remote_dir}/{remote_name}"

    with TemporaryDirectory(prefix="doubao-upload-") as temp_dir:
        upload_source = Path(temp_dir) / f"audio{suffix}"
        shutil.copyfile(audio_path, upload_source)
        _run(["ssh", ssh_host, "mkdir", "-p", remote_dir])
        _run(
            [
                "scp",
                str(upload_source),
                f"{ssh_host}:{remote_path}",
            ]
        )
        audio_url = f"{public_base_url}/{public_path}/{remote_name}"
        try:
            yield audio_url
        finally:
            try:
                _run(
                    [
                        "ssh",
                        ssh_host,
                        "rm",
                        "-f",
                        remote_path,
                    ],
                    timeout=30,
                )
            except Exception:
                pass
