import ctypes
import os
import subprocess
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import script.windows_dpapi as windows_dpapi
import wechat_capture.security as security_module
from script.windows_dpapi import protect_bytes, unprotect_bytes
from wechat_capture.security import (
    CapturePaths,
    ensure_local_token,
    install_current_user_ca,
    is_allowed_host,
    uninstall_current_user_ca,
)


THUMBPRINT = "A1B2C3D4E5F600112233445566778899AABBCCDD"


class WechatCaptureSecurityTest(unittest.TestCase):
    def test_allowlist_is_narrow(self):
        self.assertTrue(is_allowed_host("channels.weixin.qq.com"))
        self.assertTrue(is_allowed_host("cdn.finder.video.qq.com"))
        self.assertTrue(is_allowed_host("findera4.video.qq.com"))
        self.assertTrue(is_allowed_host("wxsmw.wxs.qq.com"))
        self.assertTrue(is_allowed_host("subdomain.wxqcloud.qq.com"))
        self.assertFalse(is_allowed_host("mail.qq.com"))
        self.assertFalse(is_allowed_host("finder-a.video.qq.com.evil.example"))
        self.assertFalse(is_allowed_host("finder.video.qq.com.evil.example"))
        self.assertFalse(is_allowed_host("example.com"))

    def test_capture_paths_create_expected_locations(self):
        with TemporaryDirectory() as tmp:
            paths = CapturePaths(Path(tmp))

            self.assertEqual(paths.base_dir, Path(tmp))
            self.assertEqual(paths.mitm_dir, Path(tmp) / "mitm")
            self.assertEqual(paths.queue_dir, Path(tmp) / "queue")
            self.assertEqual(paths.token_path, Path(tmp) / "auth-token")
            self.assertEqual(paths.log_path, Path(tmp) / "capture.log")
            self.assertTrue(paths.mitm_dir.is_dir())
            self.assertTrue(paths.queue_dir.is_dir())

    @patch.dict(os.environ, {"USERNAME": "Task2User", "USERDOMAIN": "Task2Domain"}, clear=False)
    def test_token_is_stable_not_short_and_rehardens_existing_file(self):
        with TemporaryDirectory() as tmp, patch(
            "wechat_capture.security.subprocess.run"
        ) as run:
            paths = CapturePaths(Path(tmp))
            first = ensure_local_token(paths)

            self.assertGreaterEqual(len(first), 43)
            self.assertEqual(paths.token_path.read_text(encoding="utf-8"), first)
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    [
                        "icacls.exe",
                        str(paths.base_dir),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(OI)(CI)F",
                    ],
                    [
                        "icacls.exe",
                        str(paths.token_path),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(F)",
                    ],
                ],
            )

            run.reset_mock()

            second = ensure_local_token(paths)

            self.assertEqual(first, second)
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    [
                        "icacls.exe",
                        str(paths.base_dir),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(OI)(CI)F",
                    ],
                    [
                        "icacls.exe",
                        str(paths.token_path),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(F)",
                    ],
                ],
            )

    @patch.dict(os.environ, {"USERNAME": "Task2User", "USERDOMAIN": "Task2Domain"}, clear=False)
    def test_weak_existing_token_is_regenerated_and_rehardened(self):
        with TemporaryDirectory() as tmp, patch(
            "wechat_capture.security.subprocess.run"
        ) as run:
            paths = CapturePaths(Path(tmp))
            paths.token_path.write_text("short", encoding="utf-8")

            token = ensure_local_token(paths)

            self.assertNotEqual(token, "short")
            self.assertGreaterEqual(len(token), 43)
            self.assertEqual(paths.token_path.read_text(encoding="utf-8"), token)
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    [
                        "icacls.exe",
                        str(paths.base_dir),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(OI)(CI)F",
                    ],
                    [
                        "icacls.exe",
                        str(paths.token_path),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(F)",
                    ],
                ],
            )

    @patch.dict(os.environ, {"USERNAME": "Task2User", "USERDOMAIN": "Task2Domain"}, clear=False)
    def test_private_write_removes_plaintext_when_file_acl_fails(self):
        with TemporaryDirectory() as tmp, patch(
            "wechat_capture.security.subprocess.run"
        ) as run:
            path = Path(tmp) / "secret.txt"
            run.side_effect = [
                subprocess.CompletedProcess(
                    [
                        "icacls.exe",
                        str(path.parent),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(OI)(CI)F",
                    ],
                    0,
                    stdout="processed",
                    stderr="",
                ),
                subprocess.CalledProcessError(
                    5,
                    [
                        "icacls.exe",
                        str(path),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(F)",
                    ],
                    output="",
                    stderr="acl failure",
                ),
            ]

            with self.assertRaises(subprocess.CalledProcessError):
                security_module._write_private_text(path, "secret", encoding="utf-8")

            self.assertFalse(path.exists())
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    [
                        "icacls.exe",
                        str(path.parent),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(OI)(CI)F",
                    ],
                    [
                        "icacls.exe",
                        str(path),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(F)",
                    ],
                ],
            )

    @patch.dict(os.environ, {"USERNAME": "Task2User", "USERDOMAIN": "Task2Domain"}, clear=False)
    def test_install_current_user_ca_verifies_by_exact_thumbprint_lookup(self):
        with TemporaryDirectory() as tmp, patch(
            "wechat_capture.security._expected_thumbprint_for_cert",
            return_value=THUMBPRINT,
        ), patch("wechat_capture.security.subprocess.run") as run:
            paths = CapturePaths(Path(tmp))
            paths.ca_cert_path.write_text("dummy cert", encoding="utf-8")
            run.side_effect = [
                subprocess.CompletedProcess(
                    ["certutil.exe", "-user", "-addstore", "Root", str(paths.ca_cert_path)],
                    0,
                    stdout="CertUtil: -addstore command completed successfully.",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    ["certutil.exe", "-user", "-store", "Root", THUMBPRINT],
                    0,
                    stdout="lookup succeeded",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    [
                        "icacls.exe",
                        str(paths.mitm_dir),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(OI)(CI)F",
                    ],
                    0,
                    stdout="processed",
                    stderr="",
                ),
                subprocess.CompletedProcess(
                    [
                        "icacls.exe",
                        str(paths.ca_thumbprint_path),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(F)",
                    ],
                    0,
                    stdout="processed",
                    stderr="",
                ),
            ]

            installed = install_current_user_ca(paths)

            self.assertEqual(installed, THUMBPRINT)
            self.assertEqual(
                paths.ca_thumbprint_path.read_text(encoding="ascii").strip(),
                THUMBPRINT,
            )
            self.assertEqual(
                [call.args[0] for call in run.call_args_list],
                [
                    ["certutil.exe", "-user", "-addstore", "Root", str(paths.ca_cert_path)],
                    ["certutil.exe", "-user", "-store", "Root", THUMBPRINT],
                    [
                        "icacls.exe",
                        str(paths.mitm_dir),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(OI)(CI)F",
                    ],
                    [
                        "icacls.exe",
                        str(paths.ca_thumbprint_path),
                        "/inheritance:r",
                        "/grant:r",
                        "Task2Domain\\Task2User:(F)",
                    ],
                ],
            )

    def test_uninstall_current_user_ca_deletes_by_thumbprint_only(self):
        with TemporaryDirectory() as tmp, patch(
            "wechat_capture.security.subprocess.run"
        ) as run:
            paths = CapturePaths(Path(tmp))
            paths.ca_thumbprint_path.write_text(THUMBPRINT, encoding="ascii")

            removed = uninstall_current_user_ca(paths)

            self.assertTrue(removed)
            run.assert_called_once_with(
                ["certutil.exe", "-user", "-delstore", "Root", THUMBPRINT],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertFalse(paths.ca_thumbprint_path.exists())

    def test_uninstall_current_user_ca_refuses_non_thumbprint_marker(self):
        with TemporaryDirectory() as tmp, patch(
            "wechat_capture.security.subprocess.run"
        ) as run:
            paths = CapturePaths(Path(tmp))
            paths.ca_thumbprint_path.write_text("CN=mitmproxy", encoding="ascii")

            with self.assertRaises(ValueError):
                uninstall_current_user_ca(paths)

            run.assert_not_called()

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_dpapi_round_trip(self):
        secret = b"media-url?token=private"
        encrypted = protect_bytes(secret)

        self.assertNotIn(secret, encrypted)
        self.assertEqual(unprotect_bytes(encrypted), secret)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_dpapi_protect_raises_oserror_with_windows_error(self):
        with patch.object(
            windows_dpapi._crypt32, "CryptProtectData", return_value=0
        ), patch("script.windows_dpapi.ctypes.get_last_error", return_value=5):
            with self.assertRaises(OSError) as ctx:
                protect_bytes(b"secret")

        self.assertEqual(ctx.exception.winerror, 5)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_dpapi_unprotect_raises_oserror_with_windows_error(self):
        with patch.object(
            windows_dpapi._crypt32, "CryptUnprotectData", return_value=0
        ), patch("script.windows_dpapi.ctypes.get_last_error", return_value=13):
            with self.assertRaises(OSError) as ctx:
                unprotect_bytes(b"ciphertext")

        self.assertEqual(ctx.exception.winerror, 13)

    @unittest.skipUnless(os.name == "nt", "Windows DPAPI only")
    def test_copy_and_free_frees_memory_when_copy_raises(self):
        blob = windows_dpapi.DATA_BLOB(
            3,
            ctypes.cast(ctypes.c_void_p(1234), ctypes.POINTER(ctypes.c_byte)),
        )

        with patch(
            "script.windows_dpapi.ctypes.string_at",
            side_effect=RuntimeError("copy failed"),
        ), patch.object(windows_dpapi._kernel32, "LocalFree") as local_free:
            with self.assertRaisesRegex(RuntimeError, "copy failed"):
                windows_dpapi._copy_and_free(blob)

        local_free.assert_called_once()


if __name__ == "__main__":
    unittest.main()
