from __future__ import annotations

import os
from pathlib import Path

from script.windows_dpapi import protect_bytes, unprotect_bytes
from wechat_capture.security import harden_private_path


def default_doubao_api_key_path() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise OSError("LOCALAPPDATA is required for secure API key storage")
    return (
        Path(local_app_data)
        / "Xiaolou"
        / "DouyinCapture"
        / "secrets"
        / "doubao-api-key.dat"
    )


class DoubaoApiKeyStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        protect=protect_bytes,
        unprotect=unprotect_bytes,
        harden=harden_private_path,
    ) -> None:
        self.path = Path(path) if path is not None else default_doubao_api_key_path()
        self._protect = protect
        self._unprotect = unprotect
        self._harden = harden

    def is_configured(self) -> bool:
        return bool(self.get())

    def get(self) -> str:
        if not self.path.exists():
            return ""
        plaintext = self._unprotect(self.path.read_bytes())
        return plaintext.decode("utf-8").strip()

    def set(self, api_key: str) -> None:
        value = str(api_key or "").strip()
        if not value:
            raise ValueError("Doubao API key must not be empty")
        if len(value) > 4096:
            raise ValueError("Doubao API key is too long")

        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._harden(self.path.parent)
        ciphertext = self._protect(value.encode("utf-8"))
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        try:
            temporary.write_bytes(ciphertext)
            self._harden(temporary)
            os.replace(temporary, self.path)
            self._harden(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear(self) -> None:
        self.path.unlink(missing_ok=True)
