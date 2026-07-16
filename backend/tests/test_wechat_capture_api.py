import tempfile
import unittest
from pathlib import Path

from script.wechat_capture_jobs import JobStore
from tests.test_wechat_capture_jobs import PRIVATE_CANDIDATE
from web.app import app


AUTH = {"X-Xiaolou-Capture-Token": "test-token"}


class WechatCaptureApiTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = JobStore(
            root / "jobs",
            root / "output",
            protect=lambda data: b"cipher:" + data[::-1],
            unprotect=lambda data: data.removeprefix(b"cipher:")[::-1],
            processor=lambda payload, settings, out_dir: {
                "success": True,
                "text": "done",
                "out_dir": str(out_dir),
            },
        )
        app.config.update(
            TESTING=True,
            WECHAT_CAPTURE_JOB_STORE=self.store,
            WECHAT_CAPTURE_TOKEN="test-token",
        )
        self.client = app.test_client()

    def tearDown(self):
        self.store.shutdown()
        self.tmp.cleanup()
        app.config.pop("WECHAT_CAPTURE_JOB_STORE", None)
        app.config.pop("WECHAT_CAPTURE_TOKEN", None)

    def test_create_job_rejects_missing_token(self):
        response = self.client.post("/api/wechat-capture/jobs", json=PRIVATE_CANDIDATE)

        self.assertEqual(response.status_code, 401)

    def test_create_job_rejects_disallowed_media_host(self):
        payload = {**PRIVATE_CANDIDATE, "media_url": "http://127.0.0.1/private"}

        response = self.client.post(
            "/api/wechat-capture/jobs",
            json=payload,
            headers=AUTH,
        )

        self.assertEqual(response.status_code, 400)

    def test_job_lifecycle_never_returns_private_payload(self):
        created = self.client.post(
            "/api/wechat-capture/jobs",
            json=PRIVATE_CANDIDATE,
            headers=AUTH,
        )
        self.assertEqual(created.status_code, 201)
        job = created.get_json()["job"]
        job_id = job["id"]
        self.assertNotIn("media_url", job)

        listed = self.client.get("/api/wechat-capture/jobs", headers=AUTH)
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("media_url", listed.get_data(as_text=True))

        started = self.client.post(
            f"/api/wechat-capture/jobs/{job_id}/start",
            json={"skip_transcribe": True},
            headers=AUTH,
        )
        self.assertEqual(started.status_code, 202)
        self.store.wait(job_id, timeout=3)

        acknowledged = self.client.post(
            f"/api/wechat-capture/jobs/{job_id}/ack",
            headers=AUTH,
        )
        self.assertEqual(acknowledged.status_code, 200)


if __name__ == "__main__":
    unittest.main()
