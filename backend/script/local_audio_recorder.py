from __future__ import annotations

import re
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path


LOOPBACK_KEYWORDS = (
    "立体声混音",
    "stereo mix",
    "what u hear",
    "cable output",
    "vb-audio",
    "virtual-audio-capturer",
    "loopback",
)


@dataclass
class RecordingSession:
    session_id: str
    out_dir: Path
    audio_path: Path
    device: str
    process: subprocess.Popen
    started_at: float


def parse_dshow_audio_devices(ffmpeg_output: str) -> list[str]:
    devices: list[str] = []
    seen: set[str] = set()
    for line in ffmpeg_output.splitlines():
        match = re.search(r'\]\s+"(.+?)"\s+\(audio\)', line)
        if not match:
            continue
        name = match.group(1)
        if name not in seen:
            seen.add(name)
            devices.append(name)
    return devices


def list_dshow_audio_devices() -> list[str]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return parse_dshow_audio_devices(f"{proc.stdout}\n{proc.stderr}")


def choose_loopback_device(devices: list[str]) -> str | None:
    for device in devices:
        normalized = device.casefold()
        if any(keyword in normalized for keyword in LOOPBACK_KEYWORDS):
            return device
    return None


def build_record_command(
    device: str,
    audio_path: Path,
    *,
    sample_rate: int = 16000,
    audio_format: str = "mp3",
) -> list[str]:
    codec = "pcm_s16le" if audio_format == "wav" else "libmp3lame"
    command = [
        "ffmpeg",
        "-y",
        "-f",
        "dshow",
        "-i",
        f"audio={device}",
        "-vn",
        "-acodec",
        codec,
        "-ar",
        str(sample_rate),
        "-ac",
        "1",
        "-map_metadata",
        "-1",
    ]
    if audio_format != "wav":
        command.extend(["-b:a", "64k"])
    command.append(str(audio_path))
    return command


def start_recording(
    output_root: Path,
    *,
    device: str | None = None,
    allow_microphone: bool = False,
) -> RecordingSession:
    devices = list_dshow_audio_devices()
    selected = device or choose_loopback_device(devices)
    if not selected and allow_microphone and devices:
        selected = devices[0]
    if not selected:
        raise RuntimeError(
            "没有找到可录制系统播放声音的设备。请启用“立体声混音”或安装 VB-CABLE 后重试。"
            f" 当前 FFmpeg 可见音频设备：{', '.join(devices) or '无'}"
        )
    if selected not in devices:
        raise RuntimeError(
            f"FFmpeg 找不到音频设备：{selected}。当前可见设备：{', '.join(devices) or '无'}"
        )

    session_id = f"local-audio-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:8]}"
    out_dir = output_root / session_id
    out_dir.mkdir(parents=True, exist_ok=True)
    audio_path = out_dir / "audio.mp3"
    process = subprocess.Popen(
        build_record_command(selected, audio_path),
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    return RecordingSession(
        session_id=session_id,
        out_dir=out_dir,
        audio_path=audio_path,
        device=selected,
        process=process,
        started_at=time.time(),
    )


def stop_recording(session: RecordingSession, *, timeout: int = 10) -> Path:
    if session.process.poll() is None:
        try:
            if session.process.stdin:
                session.process.stdin.write(b"q\n")
                session.process.stdin.flush()
            session.process.wait(timeout=timeout)
        except Exception:
            session.process.terminate()
            try:
                session.process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                session.process.kill()
                session.process.wait(timeout=timeout)
    if not session.audio_path.is_file() or session.audio_path.stat().st_size == 0:
        raise RuntimeError("录音没有生成有效音频文件，请检查音频设备是否正在输出声音。")
    return session.audio_path
