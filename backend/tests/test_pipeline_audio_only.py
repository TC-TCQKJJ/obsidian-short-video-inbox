import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from script.config import Settings
from script.pipeline import _process_video
from script.wechat_channels_resolver import WechatChannelsContentMeta


class PipelineAudioOnlyTest(unittest.TestCase):
    def test_wechat_channels_mp4_extracts_audio_without_saving_video(self):
        meta = WechatChannelsContentMeta(
            aweme_id="feed123",
            title="直播回放",
            author="小楼",
            source_url="https://channels.weixin.qq.com/web/pages/feed?feed_id=feed123",
            download_url="https://mpvideo.qpic.cn/video/feed123.mp4?token=abc",
        )

        with TemporaryDirectory() as tmp_dir, patch(
            "script.pipeline.download_file"
        ) as download_file, patch("script.pipeline.extract_audio") as extract_audio, patch(
            "script.pipeline._write_transcript_files"
        ) as write_transcript:
            _process_video(
                meta,
                Path(tmp_dir),
                Settings(skip_transcribe=True, audio_format="mp3"),
            )

        download_file.assert_not_called()
        extract_audio.assert_called_once()
        self.assertEqual(
            extract_audio.call_args.args[0],
            "https://mpvideo.qpic.cn/video/feed123.mp4?token=abc",
        )
        extra_files = write_transcript.call_args.kwargs["extra_files"]
        self.assertNotIn("video", extra_files)
        self.assertEqual(extra_files["audio"], "audio.mp3")


if __name__ == "__main__":
    unittest.main()
