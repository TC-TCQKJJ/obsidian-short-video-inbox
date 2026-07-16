import json
import io
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from script.wechat_capture_jobs import JobStore, extract_audio_stream


PRIVATE_CANDIDATE = {
    "capture_id": "capture-123",
    "title": "Target replay",
    "author": "Teacher",
    "source_url": "https://channels.weixin.qq.com/web/pages/feed?id=123",
    "media_url": "https://wxsmw.wxs.qq.com/video/123.mp4?token=secret",
    "cover_url": "https://wxsmw.wxs.qq.com/cover/123.jpg",
    "duration_ms": 123000,
    "decrypt_key": 42,
    "request_headers": {"Referer": "https://channels.weixin.qq.com/"},
    "observed_at": 1.0,
}


class WechatCaptureJobStoreTest(unittest.TestCase):
    def test_create_persists_only_ciphertext_and_public_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = JobStore(
                root / "jobs",
                root / "output",
                protect=lambda data: b"cipher:" + data[::-1],
                unprotect=lambda data: data.removeprefix(b"cipher:")[::-1],
            )

            job = store.create(PRIVATE_CANDIDATE)

            payload_path = root / "jobs" / f"{job['id']}.payload"
            public_path = root / "jobs" / f"{job['id']}.json"
            self.assertNotIn(PRIVATE_CANDIDATE["media_url"].encode(), payload_path.read_bytes())
            self.assertNotIn(
                PRIVATE_CANDIDATE["media_url"],
                public_path.read_text(encoding="utf-8"),
            )
            self.assertEqual(job["status"], "waiting_for_obsidian")
            self.assertNotIn("media_url", job)

    def test_start_is_idempotent_and_ack_deletes_private_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release = threading.Event()
            started = threading.Event()
            calls = []

            def process(payload, settings, out_dir):
                calls.append((payload["capture_id"], settings))
                started.set()
                release.wait(2)
                return {"success": True, "text": "done", "out_dir": str(out_dir)}

            store = JobStore(
                root / "jobs",
                root / "output",
                protect=lambda data: b"cipher:" + data[::-1],
                unprotect=lambda data: data.removeprefix(b"cipher:")[::-1],
                processor=process,
            )
            job = store.create(PRIVATE_CANDIDATE)

            first = store.start(job["id"], {"model": "base"})
            second = store.start(job["id"], {"model": "base"})
            self.assertEqual(first["id"], second["id"])
            self.assertTrue(started.wait(2))
            release.set()
            completed = store.wait(job["id"], timeout=3)

            self.assertEqual(len(calls), 1)
            self.assertEqual(completed["status"], "completed")
            acknowledged = store.acknowledge(job["id"])
            self.assertTrue(acknowledged["acknowledged"])
            self.assertFalse((root / "jobs" / f"{job['id']}.payload").exists())

    def test_processing_job_recovers_to_waiting_on_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            jobs = root / "jobs"
            jobs.mkdir()
            (jobs / "job-1.json").write_text(
                json.dumps(
                    {
                        "id": "job-1",
                        "capture_id": "capture-1",
                        "title": "Title",
                        "author": "Author",
                        "status": "processing",
                        "acknowledged": False,
                    }
                ),
                encoding="utf-8",
            )
            (jobs / "job-1.payload").write_bytes(b"cipher")

            store = JobStore(
                jobs,
                root / "output",
                protect=lambda data: data,
                unprotect=lambda data: data,
            )

            self.assertEqual(store.get("job-1")["status"], "waiting_for_obsidian")

    def test_http_media_is_streamed_to_ffmpeg_without_video_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            audio_path = root / "audio.mp3"
            upstream = Mock()
            upstream.iter_content.return_value = [b"first", b"second"]
            upstream.raise_for_status.return_value = None
            process = Mock()
            process.stdin = io.BytesIO()
            process.stderr = io.BytesIO()
            process.wait.return_value = 0

            with (
                patch(
                    "script.wechat_capture_jobs.requests.get",
                    return_value=upstream,
                ) as get,
                patch(
                    "script.wechat_capture_jobs.subprocess.Popen",
                    return_value=process,
                ) as popen,
                patch(
                    "script.wechat_capture_jobs._ffmpeg",
                    return_value="ffmpeg",
                ),
            ):
                result = extract_audio_stream(
                    {**PRIVATE_CANDIDATE, "decrypt_key": 0},
                    audio_path,
                )

            self.assertEqual(result, audio_path)
            get.assert_called_once_with(
                PRIVATE_CANDIDATE["media_url"],
                headers=PRIVATE_CANDIDATE["request_headers"],
                stream=True,
                timeout=(15, 120),
            )
            command = popen.call_args.args[0]
            self.assertIn("pipe:0", command)
            self.assertIn("-vn", command)
            self.assertEqual(command[-1], str(audio_path))
            self.assertFalse(any(root.glob("*.mp4")))
            self.assertFalse(any(root.glob("*.flv")))
            self.assertFalse(any(root.glob("*.m3u8")))


if __name__ == "__main__":
    unittest.main()
