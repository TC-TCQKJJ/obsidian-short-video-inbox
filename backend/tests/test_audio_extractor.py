import unittest
from pathlib import Path
from unittest.mock import patch

from script.audio_extractor import convert_audio


class AudioExtractorTest(unittest.TestCase):
    def test_convert_audio_accepts_stream_url_sources(self):
        with patch("script.audio_extractor.ensure_ffmpeg", return_value="ffmpeg"), patch(
            "script.audio_extractor.subprocess.run"
        ) as run:
            run.return_value.returncode = 0
            convert_audio(
                "https://finder.video.qq.com/live/replay.m3u8?token=abc",
                Path("audio.mp3"),
            )

        command = run.call_args.args[0]
        self.assertIn("https://finder.video.qq.com/live/replay.m3u8?token=abc", command)
        self.assertIn("-map", command)
        self.assertIn("0:a:0?", command)
        self.assertIn("-vn", command)
        self.assertIn("audio.mp3", command)


if __name__ == "__main__":
    unittest.main()
