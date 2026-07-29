import base64
import http.client
import json
import time
import unittest
from pathlib import Path

from wechat_capture.addon import PassiveWechatCaptureAddon
from wechat_capture.matcher import CaptureMatcher
from wechat_capture.process_capture import (
    BRIDGE_PATH,
    ProcessCaptureBridgeServer,
    ProcessCaptureEventProcessor,
)


FEED = {
    "id": "feed-123",
    "objectDesc": {
        "description": "Process capture target",
        "media": [
            {
                "url": "https://wxsmw.wxs.qq.com/video/feed-123.mp4",
                "urlToken": "?token=secret",
            }
        ],
    },
}

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
GO_HELPER = REPOSITORY_ROOT / "backend" / "wechat_process_capture" / "main.go"
PATCH_SCRIPT = (
    REPOSITORY_ROOT
    / "backend"
    / "wechat_process_capture"
    / "patch-sunnynet.ps1"
)
VERIFY_SCRIPT = (
    REPOSITORY_ROOT
    / "backend"
    / "wechat_process_capture"
    / "verify-binary.ps1"
)
LAUNCH_SCRIPT = (
    REPOSITORY_ROOT
    / "backend"
    / "wechat_capture"
    / "launch-process-capture.ps1"
)


class ProcessCaptureEventProcessorTest(unittest.TestCase):
    def setUp(self):
        self.matcher = CaptureMatcher(max_age_seconds=30)
        self.processor = ProcessCaptureEventProcessor(
            PassiveWechatCaptureAddon(self.matcher),
            "t" * 43,
        )

    def test_authenticated_feed_and_media_events_create_candidate(self):
        body = json.dumps({"data": {"object": FEED}}).encode("utf-8")
        self.processor.handle(
            {
                "type": "response",
                "url": "https://channels.weixin.qq.com/finder/feed",
                "headers": {"Content-Type": "application/json"},
                "body": base64.b64encode(body).decode("ascii"),
            }
        )
        self.processor.handle(
            {
                "type": "request",
                "url": "https://wxsmw.wxs.qq.com/video/feed-123.mp4?token=latest",
                "headers": {
                    "User-Agent": "Mozilla/5.0",
                    "Range": "bytes=0-",
                    "Cookie": "session=secret",
                    "Authorization": "Bearer secret",
                },
                "observed_at": time.time(),
            }
        )

        candidates = self.matcher.recent_candidates()
        self.assertEqual(len(candidates), 1)
        self.assertEqual(
            candidates[0].request_headers,
            {
                "User-Agent": "Mozilla/5.0",
                "Range": "bytes=0-",
            },
        )

    def test_disallowed_host_event_is_ignored(self):
        self.processor.handle(
            {
                "type": "request",
                "url": "https://mail.qq.com/private.mp4",
                "headers": {},
                "observed_at": time.time(),
            }
        )

        self.assertEqual(self.matcher.recent_candidates(), [])

    def test_driver_status_updates_lifecycle_events(self):
        self.processor.handle({"type": "status", "state": "driver_ready"})
        self.assertTrue(self.processor.driver_ready.is_set())

        self.processor.handle({"type": "status", "state": "driver_stopped"})
        self.assertTrue(self.processor.driver_stopped.is_set())

    def test_heartbeat_metrics_are_visible_in_diagnostics(self):
        self.processor.handle({"type": "status", "state": "driver_ready"})
        self.processor.handle(
            {
                "type": "status",
                "state": "heartbeat",
                "request_count": 2,
                "response_count": 1,
                "dropped_count": 0,
                "failed_count": 0,
            }
        )

        self.assertIn("请求 2", self.processor.diagnostic_text())
        self.assertIn("信息 1", self.processor.diagnostic_text())

    def test_heartbeat_distinguishes_raw_and_decrypted_traffic(self):
        self.processor.handle({"type": "status", "state": "driver_ready"})
        self.processor.handle(
            {
                "type": "status",
                "state": "heartbeat",
                "tcp_count": 3,
            }
        )
        self.assertIn("原始连接 3", self.processor.diagnostic_text())

        self.processor.handle(
            {
                "type": "status",
                "state": "heartbeat",
                "tcp_count": 3,
                "http_count": 2,
            }
        )
        self.assertIn("已解密 2", self.processor.diagnostic_text())

    def test_capture_failure_is_logged_without_event_payloads(self):
        self.processor.handle({"type": "status", "state": "driver_ready"})
        self.processor.handle(
            {
                "type": "status",
                "state": "heartbeat",
                "capture_error_count": 1,
                "last_capture_error": "TLS handshake failed",
            }
        )

        self.assertIn("失败 1 次", self.processor.diagnostic_text())
        self.assertEqual(self.processor._last_capture_error, "TLS handshake failed")

    def test_malformed_response_body_is_rejected(self):
        with self.assertRaises(ValueError):
            self.processor.handle(
                {
                    "type": "response",
                    "url": "https://channels.weixin.qq.com/finder/feed",
                    "headers": {"Content-Type": "application/json"},
                    "body": "not-base64",
                }
            )

    def test_authentication_requires_exact_bearer_token(self):
        self.assertTrue(self.processor.authenticate("Bearer " + ("t" * 43)))
        self.assertFalse(self.processor.authenticate("Bearer wrong"))
        self.assertFalse(self.processor.authenticate(None))


class ProcessCaptureBridgeServerTest(unittest.TestCase):
    def setUp(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        self.processor = ProcessCaptureEventProcessor(
            PassiveWechatCaptureAddon(matcher),
            "b" * 43,
        )
        self.bridge = ProcessCaptureBridgeServer(
            self.processor,
            port=0,
        )
        self.bridge.start()

    def tearDown(self):
        self.bridge.close()

    def _post(self, token: str):
        connection = http.client.HTTPConnection(
            self.bridge.host,
            self.bridge.port,
            timeout=2,
        )
        payload = json.dumps(
            {"type": "status", "state": "heartbeat"}
        ).encode("ascii")
        connection.request(
            "POST",
            BRIDGE_PATH,
            body=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        response = connection.getresponse()
        body = response.read()
        connection.close()
        return response.status, body

    def test_bridge_accepts_authenticated_loopback_event(self):
        status, body = self._post("b" * 43)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"stop": False})

    def test_bridge_rejects_wrong_token(self):
        status, _body = self._post("wrong")

        self.assertEqual(status, 401)

    def test_stop_request_is_returned_to_helper(self):
        self.bridge.request_stop()

        status, body = self._post("b" * 43)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body), {"stop": True})


class ProcessCaptureSourceBoundaryTest(unittest.TestCase):
    def test_go_helper_loads_unique_ca_without_public_listener_or_system_proxy(self):
        source = GO_HELPER.read_text(encoding="utf-8")

        self.assertIn("LoadX509KeyPair", source)
        self.assertIn("sunny.SetCert", source)
        self.assertIn("SetMustTcpRegexp(rules, false)", source)
        self.assertIn("currentLoopbackProxy()", source)
        self.assertIn("conn.SetAgent(sender.upstreamProxy", source)
        self.assertNotIn("sunny.SetGlobalProxy(", source)
        self.assertIn('ProcessAddName(processName)', source)
        self.assertIn('"WeChatAppEx.exe"', source)
        self.assertIn('"finder.video.qq.com:*"', source)
        self.assertIn('"*.wxs.qq.com:*"', source)
        self.assertIn("sender.handleTCP", source)
        self.assertNotIn("SunnyRoot", source)
        self.assertNotIn("sunny.Start(", source)
        self.assertNotIn("SetIEProxy", source)
        self.assertNotIn("InstallCert", source)
        self.assertNotIn("ProcessALLName", source)

    def test_go_helper_fails_closed_on_driver_error(self):
        source = GO_HELPER.read_text(encoding="utf-8")

        self.assertIn("if !sunny.OpenDrive(0)", source)
        self.assertIn("sunny.Close()", source)
        self.assertIn("process driver failed to start", source)
        self.assertIn("registry.CURRENT_USER", source)

    def test_launcher_uses_hidden_elevated_process(self):
        source = LAUNCH_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("-Verb RunAs", source)
        self.assertIn("-WindowStyle Hidden", source)

    def test_build_removes_and_verifies_absence_of_upstream_private_key(self):
        patch_source = PATCH_SCRIPT.read_text(encoding="utf-8")
        verify_source = VERIFY_SCRIPT.read_text(encoding="utf-8")

        self.assertIn("defaultManager", patch_source)
        self.assertIn("RootKey", patch_source)
        self.assertIn("GetTcpTable2Func", patch_source)
        self.assertIn("MIIEpAIBAAKCAQEAzU+hPfoE", verify_source)


if __name__ == "__main__":
    unittest.main()
