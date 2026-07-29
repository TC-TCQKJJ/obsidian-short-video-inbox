import importlib.util
import json
import logging
import time
import unittest
from tempfile import TemporaryDirectory
from urllib.parse import urlunsplit

from wechat_capture.matcher import CaptureMatcher
from wechat_capture.security import CapturePaths


try:
    from wechat_capture.addon import PassiveWechatCaptureAddon, build_proxy_options
    from wechat_capture.redaction import redact_headers, redact_url
except ModuleNotFoundError:
    PassiveWechatCaptureAddon = None
    build_proxy_options = None
    redact_headers = None
    redact_url = None


FEED = {
    "id": "feed-123",
    "objectDesc": {
        "description": "Target replay",
        "media": [
            {
                "url": "https://wxsmw.wxs.qq.com/video/feed-123.mp4",
                "urlToken": "?token=secret",
            }
        ],
    },
}


class FakeResponse:
    def __init__(self, raw_content: bytes, headers: dict[str, str] | None = None) -> None:
        self.raw_content = raw_content
        self.headers = dict(headers or {})


class FakeRequest:
    def __init__(
        self,
        *,
        host: str,
        path: str,
        headers: dict[str, str] | None = None,
        scheme: str = "https",
        timestamp_start: float | None = None,
    ) -> None:
        self.host = host
        self.path = path
        self.headers = dict(headers or {})
        self.scheme = scheme
        self.timestamp_start = time.time() if timestamp_start is None else timestamp_start
        self.url = urlunsplit((scheme, host, path, "", ""))
        self.pretty_url = self.url


class FakeFlow:
    def __init__(self, request: FakeRequest, response: FakeResponse | None = None) -> None:
        self.request = request
        self.response = response
        self.killed = False

    def kill(self) -> None:
        self.killed = True


def fake_flow(
    *,
    host: str,
    path: str,
    request_headers: dict[str, str] | None = None,
    response_body: bytes | None = None,
    response_headers: dict[str, str] | None = None,
) -> FakeFlow:
    request = FakeRequest(host=host, path=path, headers=request_headers)
    response = None
    if response_body is not None:
        response = FakeResponse(response_body, headers=response_headers)
    return FakeFlow(request=request, response=response)


class WechatCaptureAddonTest(unittest.TestCase):
    def _build_addon(self, matcher: CaptureMatcher, logger: logging.Logger | None = None):
        self.assertIsNotNone(PassiveWechatCaptureAddon)
        return PassiveWechatCaptureAddon(matcher, logger=logger)

    def _build_logger(self) -> logging.Logger:
        return logging.getLogger(f"{__name__}.{self.id()}")

    def _assert_response_untouched(
        self,
        flow: FakeFlow,
        original_body: bytes,
        original_headers: dict[str, str],
    ) -> None:
        self.assertEqual(flow.response.raw_content, original_body)
        self.assertEqual(flow.response.headers, original_headers)

    def test_json_feed_response_is_recorded_without_modification(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        addon = self._build_addon(matcher)
        original = json.dumps({"data": {"object": FEED}}).encode("utf-8")
        response_headers = {"Content-Type": "application/json"}
        flow = fake_flow(
            host="channels.weixin.qq.com",
            path="/finder-live/cgi-bin/feedProfile",
            response_body=original,
            response_headers=response_headers,
        )

        addon.response(flow)

        self._assert_response_untouched(flow, original, response_headers)
        self.assertEqual(matcher.feed_count, 1)

    def test_json_body_is_detected_without_json_content_type(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        addon = self._build_addon(matcher)
        original = json.dumps({"object": FEED}).encode("utf-8")
        response_headers = {"Content-Type": "text/plain"}
        flow = fake_flow(
            host="channels.weixin.qq.com",
            path="/finder-live/cgi-bin/feedProfile",
            response_body=original,
            response_headers=response_headers,
        )

        addon.response(flow)

        self._assert_response_untouched(flow, original, response_headers)
        self.assertEqual(matcher.feed_count, 1)

    def test_unmatched_json_logs_schema_without_payload_values(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        logger = self._build_logger()
        addon = self._build_addon(matcher, logger=logger)
        payload = {
            "data": {
                "objectDesc": "LEAKED_TITLE",
                "mediaList": [
                    {
                        "playUrl": "https://finder.video.qq.com/private.mp4?token=LEAKED",
                        "decodeKey": "LEAKED_KEY",
                    }
                ],
            }
        }

        with self.assertLogs(logger, level="INFO") as captured:
            addon.record_feed_payload(payload)

        self.assertEqual(matcher.feed_count, 0)
        self.assertEqual(len(captured.output), 1)
        self.assertIn("data.objectDesc:string", captured.output[0])
        self.assertIn("data.mediaList:array", captured.output[0])
        self.assertIn("data.mediaList.playUrl:string", captured.output[0])
        self.assertIn("data.mediaList.decodeKey:string", captured.output[0])
        self.assertNotIn("LEAKED_TITLE", captured.output[0])
        self.assertNotIn("finder.video.qq.com", captured.output[0])
        self.assertNotIn("LEAKED_KEY", captured.output[0])

    def test_non_json_binary_response_is_ignored_without_modifying_response(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        addon = self._build_addon(matcher)
        original = b"\x00\xffbinary\x10payload"
        response_headers = {"Content-Type": "application/octet-stream"}
        flow = fake_flow(
            host="channels.weixin.qq.com",
            path="/finder-live/cgi-bin/feedProfile",
            response_body=original,
            response_headers=response_headers,
        )

        addon.response(flow)

        self._assert_response_untouched(flow, original, response_headers)
        self.assertEqual(matcher.feed_count, 0)

    def test_undecodable_json_response_is_ignored_without_modifying_response(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        logger = self._build_logger()
        addon = self._build_addon(matcher, logger=logger)
        original = b'{\xff"token":"secret"}'
        response_headers = {"Content-Type": "application/json"}
        flow = fake_flow(
            host="channels.weixin.qq.com",
            path="/finder-live/cgi-bin/feedProfile?token=response-secret",
            response_body=original,
            response_headers=response_headers,
            request_headers={
                "Cookie": "session=secret",
                "Authorization": "Bearer secret",
            },
        )

        with self.assertLogs(logger, level="DEBUG") as captured:
            addon.response(flow)

        self._assert_response_untouched(flow, original, response_headers)
        self.assertEqual(matcher.feed_count, 0)
        self.assertEqual(len(captured.output), 1)
        self.assertIn(
            "Ignored undecodable JSON response for https://channels.weixin.qq.com/finder-live/cgi-bin/feedProfile?<redacted>",
            captured.output[0],
        )
        self.assertNotIn("token=response-secret", captured.output[0])
        self.assertNotIn("session=secret", captured.output[0])
        self.assertNotIn("Bearer secret", captured.output[0])

    def test_large_response_body_is_ignored_and_logs_redacted_url(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        logger = self._build_logger()
        addon = self._build_addon(matcher, logger=logger)
        original = b"[" + (b" " * (8 * 1024 * 1024 + 1)) + b"]"
        response_headers = {"Content-Type": "application/json"}
        flow = fake_flow(
            host="channels.weixin.qq.com",
            path="/finder-live/cgi-bin/feedProfile?token=response-secret",
            response_body=original,
            response_headers=response_headers,
            request_headers={
                "Cookie": "session=secret",
                "Authorization": "Bearer secret",
            },
        )

        with self.assertLogs(logger, level="DEBUG") as captured:
            addon.response(flow)

        self._assert_response_untouched(flow, original, response_headers)
        self.assertEqual(matcher.feed_count, 0)
        self.assertEqual(len(captured.output), 1)
        self.assertIn(
            "Ignored oversized WeChat response for https://channels.weixin.qq.com/finder-live/cgi-bin/feedProfile?<redacted>",
            captured.output[0],
        )
        self.assertNotIn("token=response-secret", captured.output[0])
        self.assertNotIn("session=secret", captured.output[0])
        self.assertNotIn("Bearer secret", captured.output[0])

    def test_disallowed_host_is_killed_before_http_processing_and_logs_redacted_url(self):
        logger = self._build_logger()
        addon = self._build_addon(CaptureMatcher(max_age_seconds=30), logger=logger)
        flow = fake_flow(
            host="mail.qq.com",
            path="/blocked.mp4?token=request-secret",
            request_headers={
                "Cookie": "session=secret",
                "Authorization": "Bearer secret",
            },
        )

        with self.assertLogs(logger, level="DEBUG") as captured:
            addon.request(flow)

        self.assertTrue(flow.killed)
        self.assertEqual(len(captured.output), 1)
        self.assertIn(
            "Blocked WeChat capture request for https://mail.qq.com/blocked.mp4?<redacted>",
            captured.output[0],
        )
        self.assertNotIn("token=request-secret", captured.output[0])
        self.assertNotIn("session=secret", captured.output[0])
        self.assertNotIn("Bearer secret", captured.output[0])

    def test_clash_health_check_is_passed_through_without_recording(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        addon = self._build_addon(matcher)
        flow = fake_flow(host="www.gstatic.com", path="/generate_204")

        addon.request(flow)

        self.assertFalse(flow.killed)
        self.assertEqual(matcher.recent_candidates(), [])

    def test_matching_media_request_is_observed_with_safe_headers_only(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        addon = self._build_addon(matcher)
        addon.record_feed_payload({"object": FEED})
        flow = fake_flow(
            host="wxsmw.wxs.qq.com",
            path="/video/feed-123.mp4?token=latest",
            request_headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://channels.weixin.qq.com/feed",
                "Origin": "https://channels.weixin.qq.com",
                "Range": "bytes=0-",
                "Accept": "*/*",
                "Cookie": "session=secret",
                "Authorization": "Bearer secret",
            },
        )

        addon.request(flow)

        matches = matcher.recent_candidates()
        self.assertEqual(len(matches), 1)
        self.assertEqual(
            matches[0].media_url,
            "https://wxsmw.wxs.qq.com/video/feed-123.mp4?token=latest",
        )
        self.assertEqual(
            matches[0].request_headers,
            {
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://channels.weixin.qq.com/feed",
                "Origin": "https://channels.weixin.qq.com",
                "Range": "bytes=0-",
                "Accept": "*/*",
            },
        )

    def test_stodownload_media_request_is_observed(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        addon = self._build_addon(matcher)
        feed = {
            "id": "feed-stodownload",
            "objectDesc": {
                "description": "Current download route",
                "media": [
                    {
                        "url": "https://finder.video.qq.com/251/20302/stodownload",
                        "urlToken": "?token=feed",
                    }
                ],
            },
        }
        addon.record_feed_payload({"object": feed})

        addon.record_request_event(
            "https://finder.video.qq.com/251/20302/stodownload?token=latest",
            observed_at=time.time(),
        )

        self.assertEqual(len(matcher.recent_candidates()), 1)

    def test_extensionless_media_cdn_request_is_observed(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        addon = self._build_addon(matcher)
        media_url = (
            "https://wxsmw.wxs.qq.com/251/20302/media?token=extensionless"
        )
        addon.record_feed_payload(
            {
                "schema": "xiaolou_capture_v1",
                "capture_id": "feed-extensionless",
                "media_url": media_url,
            }
        )

        addon.record_request_event(
            media_url,
            observed_at=time.time(),
        )

        self.assertEqual(len(matcher.recent_candidates()), 1)

    def test_logs_redact_signed_query_and_cookie(self):
        self.assertEqual(
            redact_url("https://wxsmw.wxs.qq.com/v.mp4?token=secret"),
            "https://wxsmw.wxs.qq.com/v.mp4?<redacted>",
        )
        self.assertEqual(
            redact_headers(
                {
                    "Cookie": "session=secret",
                    "Authorization": "Bearer secret",
                    "Referer": "https://channels.weixin.qq.com/secret",
                    "X-Access-Token": "abc",
                    "User-Agent": "Mozilla/5.0",
                }
            ),
            {
                "Cookie": "<redacted>",
                "Authorization": "<redacted>",
                "Referer": "<redacted>",
                "X-Access-Token": "<redacted>",
                "User-Agent": "Mozilla/5.0",
            },
        )

    @unittest.skipUnless(
        importlib.util.find_spec("mitmproxy") is not None,
        "mitmproxy only available in isolated host environment",
    )
    def test_proxy_options_are_localhost_only(self):
        self.assertIsNotNone(build_proxy_options)
        with TemporaryDirectory() as tmp:
            paths = CapturePaths(tmp)

            proxy_options = build_proxy_options(paths)

        self.assertEqual(proxy_options.listen_host, "127.0.0.1")
        self.assertEqual(proxy_options.listen_port, 2023)
        self.assertEqual(proxy_options.confdir, str(paths.mitm_dir))
        self.assertTrue(proxy_options.block_global)
        self.assertFalse(proxy_options.ssl_insecure)
        self.assertEqual(proxy_options.connection_strategy, "eager")


if __name__ == "__main__":
    unittest.main()
