from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent_tools.wechat_capture_client import (
    CaptureBackendError,
    WechatCaptureClient,
)


JOB_ID = "a" * 32
TOKEN = "t" * 43


class FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, responses: list[FakeResponse]) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def request(self, method, url, **kwargs):
        self.calls.append({"method": method, "url": url, **kwargs})
        return self.responses.pop(0)


class WechatCaptureClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.token_path = Path(self.temp_dir.name) / "auth-token"
        self.token_path.write_text(TOKEN, encoding="utf-8")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def client(self, *responses: FakeResponse) -> tuple[WechatCaptureClient, FakeSession]:
        session = FakeSession(list(responses))
        return (
            WechatCaptureClient(token_path=self.token_path, session=session),
            session,
        )

    def test_status_counts_jobs_without_returning_job_data(self) -> None:
        client, _ = self.client(
            FakeResponse({"success": True, "api_key_configured": True}),
            FakeResponse(
                {
                    "success": True,
                    "jobs": [
                        {"status": "waiting_for_obsidian", "media_url": "secret"},
                        {"status": "completed", "source_url": "private"},
                    ],
                }
            ),
        )

        result = client.status()

        self.assertEqual(result["backend"], "ready")
        self.assertTrue(result["doubao_api_key_configured"])
        self.assertEqual(result["job_counts"]["waiting_for_obsidian"], 1)
        self.assertEqual(result["job_counts"]["completed"], 1)
        self.assertNotIn("jobs", result)

    def test_list_jobs_uses_whitelist_and_filters_status(self) -> None:
        client, _ = self.client(
            FakeResponse(
                {
                    "success": True,
                    "jobs": [
                        {
                            "id": JOB_ID,
                            "title": "Target",
                            "author": "Author",
                            "status": "completed",
                            "source_url": "https://example.invalid/private",
                            "output_dir": "C:/private",
                            "result": {
                                "text": "hello",
                                "audio_path": "C:/private/audio.mp3",
                                "transcription_engine": "doubao",
                            },
                        },
                        {"id": "b" * 32, "status": "failed"},
                    ],
                }
            )
        )

        result = client.list_jobs(status="completed")

        self.assertEqual(result["count"], 1)
        job = result["jobs"][0]
        self.assertEqual(job["title"], "Target")
        self.assertEqual(job["transcript_chars"], 5)
        self.assertEqual(job["result"]["transcription_engine"], "doubao")
        self.assertNotIn("source_url", job)
        self.assertNotIn("output_dir", job)
        self.assertNotIn("audio_path", str(job))
        self.assertNotIn("hello", str(job))

    def test_start_job_sends_only_bounded_settings_and_auth(self) -> None:
        client, session = self.client(
            FakeResponse(
                {
                    "success": True,
                    "job": {"id": JOB_ID, "status": "waiting_for_obsidian"},
                }
            ),
            FakeResponse(
                {
                    "success": True,
                    "job": {"id": JOB_ID, "status": "processing"},
                },
                status_code=202,
            ),
        )

        result = client.start_job(JOB_ID, transcription_engine="doubao")

        self.assertTrue(result["started"])
        start_call = session.calls[1]
        self.assertEqual(start_call["headers"]["X-Xiaolou-Capture-Token"], TOKEN)
        self.assertEqual(start_call["json"]["transcription_engine"], "doubao")
        self.assertNotIn("doubao_api_key", start_call["json"])
        self.assertNotIn("url", start_call["json"])

    def test_completed_job_is_not_started_again(self) -> None:
        client, session = self.client(
            FakeResponse(
                {
                    "success": True,
                    "job": {"id": JOB_ID, "status": "completed"},
                }
            )
        )

        result = client.start_job(JOB_ID)

        self.assertFalse(result["started"])
        self.assertEqual(len(session.calls), 1)

    def test_transcript_is_returned_in_bounded_chunks(self) -> None:
        client, _ = self.client(
            FakeResponse(
                {
                    "success": True,
                    "job": {
                        "id": JOB_ID,
                        "title": "Target",
                        "status": "completed",
                        "result": {
                            "text": "abcdefghij",
                            "audio_path": "C:/private/audio.mp3",
                            "transcription_engine": "whisper",
                        },
                    },
                }
            )
        )

        result = client.read_transcript(JOB_ID, offset=3, limit=4)

        self.assertEqual(result["text"], "defg")
        self.assertEqual(result["next_offset"], 7)
        self.assertFalse(result["complete"])
        self.assertNotIn("audio_path", result)

    def test_invalid_job_id_is_rejected_before_network_call(self) -> None:
        client, session = self.client()

        with self.assertRaisesRegex(CaptureBackendError, "job_id"):
            client.get_job("../private")

        self.assertEqual(session.calls, [])

    def test_non_loopback_backend_is_rejected(self) -> None:
        with self.assertRaisesRegex(CaptureBackendError, "loopback"):
            WechatCaptureClient(
                backend_url="https://example.invalid",
                token_path=self.token_path,
            )

    def test_backend_error_redacts_url(self) -> None:
        client, _ = self.client(
            FakeResponse(
                {
                    "success": False,
                    "error": "failed at https://media.example/video?token=secret",
                },
                status_code=500,
            )
        )

        with self.assertRaises(CaptureBackendError) as raised:
            client.get_job(JOB_ID)

        self.assertNotIn("token=secret", str(raised.exception))
        self.assertIn("[redacted-url]", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
