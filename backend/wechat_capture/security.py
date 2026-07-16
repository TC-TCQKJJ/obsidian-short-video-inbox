from __future__ import annotations

import hashlib
import os
import re
import secrets
import ssl
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


EXACT_HOSTS = {
    "weixin.qq.com",
    "channels.weixin.qq.com",
    "finder.video.qq.com",
}
ALLOWED_SUFFIXES = (
    ".wxs.qq.com",
    ".wxqcloud.qq.com",
    ".wxlivecdn.com",
)
_THUMBPRINT_RE = re.compile(r"^[0-9A-F]{40}$")


def _default_capture_base_dir() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / "Xiaolou" / "WechatCapture"


@dataclass(frozen=True)
class CapturePaths:
    base_dir: Path = field(default_factory=_default_capture_base_dir)

    def __post_init__(self) -> None:
        base_dir = Path(self.base_dir)
        object.__setattr__(self, "base_dir", base_dir)
        base_dir.mkdir(parents=True, exist_ok=True)
        self.mitm_dir.mkdir(parents=True, exist_ok=True)
        self.queue_dir.mkdir(parents=True, exist_ok=True)

    @property
    def mitm_dir(self) -> Path:
        return self.base_dir / "mitm"

    @property
    def queue_dir(self) -> Path:
        return self.base_dir / "queue"

    @property
    def token_path(self) -> Path:
        return self.base_dir / "auth-token"

    @property
    def log_path(self) -> Path:
        return self.base_dir / "capture.log"

    @property
    def ca_cert_path(self) -> Path:
        return self.mitm_dir / "mitmproxy-ca-cert.cer"

    @property
    def ca_thumbprint_path(self) -> Path:
        return self.mitm_dir / "ca-thumbprint.txt"


def is_allowed_host(host: str) -> bool:
    normalized = str(host or "").strip().rstrip(".").lower()
    return normalized in EXACT_HOSTS or any(
        normalized.endswith(suffix) for suffix in ALLOWED_SUFFIXES
    )


def ensure_local_token(paths: CapturePaths) -> str:
    if paths.token_path.exists():
        token = paths.token_path.read_text(encoding="utf-8").strip()
        if _is_strong_token(token):
            _reharden_private_path(paths.token_path)
            return token

    token = secrets.token_urlsafe(32)
    _write_private_text(paths.token_path, token, encoding="utf-8")
    return token


def install_current_user_ca(paths: CapturePaths) -> str:
    if not paths.ca_cert_path.exists():
        raise FileNotFoundError(paths.ca_cert_path)

    expected_thumbprint = _expected_thumbprint_for_cert(paths.ca_cert_path)
    _run(
        [
            "certutil.exe",
            "-user",
            "-addstore",
            "Root",
            str(paths.ca_cert_path),
        ]
    )
    _run(["certutil.exe", "-user", "-store", "Root", expected_thumbprint])

    _write_private_text(paths.ca_thumbprint_path, expected_thumbprint, encoding="ascii")
    return expected_thumbprint


def uninstall_current_user_ca(paths: CapturePaths) -> bool:
    if not paths.ca_thumbprint_path.exists():
        return False

    thumbprint = paths.ca_thumbprint_path.read_text(encoding="ascii").strip().upper()
    if not _THUMBPRINT_RE.fullmatch(thumbprint):
        raise ValueError("Refusing to uninstall a certificate without a stored thumbprint")

    _run(["certutil.exe", "-user", "-delstore", "Root", thumbprint])
    paths.ca_thumbprint_path.unlink()
    return True


def _write_private_text(path: Path, value: str, *, encoding: str) -> None:
    _tighten_path_acl(path.parent)
    path.write_text(value, encoding=encoding)
    try:
        _tighten_path_acl(path)
    except Exception:
        if path.exists():
            path.unlink()
        raise


def _reharden_private_path(path: Path) -> None:
    _tighten_path_acl(path.parent)
    _tighten_path_acl(path)


def _tighten_path_acl(path: Path) -> None:
    permission = "(OI)(CI)F" if path.is_dir() else "(F)"
    username = os.environ["USERNAME"]
    domain = os.environ.get("USERDOMAIN", "").strip()
    identity = f"{domain}\\{username}" if domain else username
    _run(
        [
            "icacls.exe",
            str(path),
            "/inheritance:r",
            "/grant:r",
            f"{identity}:{permission}",
        ]
    )


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        check=True,
        capture_output=True,
        text=True,
    )


def _expected_thumbprint_for_cert(cert_path: Path) -> str:
    cert_bytes = cert_path.read_bytes()
    stripped = cert_bytes.lstrip()
    if stripped.startswith(b"-----BEGIN CERTIFICATE-----"):
        pem = cert_bytes.decode("ascii").strip()
        der_bytes = ssl.PEM_cert_to_DER_cert(pem)
    else:
        der_bytes = cert_bytes
    return hashlib.sha1(der_bytes).hexdigest().upper()


def _is_strong_token(token: str) -> bool:
    return len(token) >= 43
