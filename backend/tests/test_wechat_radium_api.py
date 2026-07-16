import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from web.app import app


class WechatRadiumApiTest(unittest.TestCase):
    def test_extract_falls_back_to_recent_media_url_when_share_page_hides_media(self):
        with TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            (out_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "aweme_id": "media1",
                        "title": "缓存媒体",
                        "author": "",
                        "platform": "wechat_channels",
                        "content_type": "video",
                        "transcription_engine": "whisper",
                    }
                ),
                encoding="utf-8",
            )
            (out_dir / "download_url.txt").write_text(
                "https://finder.video.qq.com/video.flv?token=ok\n",
                encoding="utf-8",
            )
            (out_dir / "transcript.txt").write_text("转写文本", encoding="utf-8")

            page_link = Mock(url="https://channels.weixin.qq.com/web/pages/live?oid=abc")
            media_link = Mock(url="https://finder.video.qq.com/video.flv?token=ok")

            with (
                patch("web.app.find_recent_wechat_channels_links", return_value=[page_link]),
                patch(
                    "web.app.find_recent_wechat_channels_media_urls",
                    return_value=[media_link],
                    create=True,
                ),
                patch(
                    "web.app.process_douyin_share",
                    side_effect=[
                        RuntimeError(
                            "WeChat Channels media URL was not exposed in the share page. "
                            "This link may need a browser-assisted resolver."
                        ),
                        out_dir,
                    ],
                ) as process,
            ):
                response = app.test_client().post("/api/wechat-radium/extract", json={})

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["success"])
        self.assertEqual(response.json["download_url"], media_link.url)
        self.assertEqual([call.args[0] for call in process.call_args_list], [page_link.url, media_link.url])

    def test_extract_uses_request_url_as_media_search_anchor(self):
        with TemporaryDirectory() as tmp_dir:
            out_dir = Path(tmp_dir)
            (out_dir / "meta.json").write_text(
                json.dumps(
                    {
                        "aweme_id": "target",
                        "title": "target",
                        "author": "",
                        "platform": "wechat_channels",
                        "content_type": "video",
                    }
                ),
                encoding="utf-8",
            )
            (out_dir / "download_url.txt").write_text(
                "https://wxsmw.wxs.qq.com/video/target.mp4?token=ok\n",
                encoding="utf-8",
            )
            (out_dir / "transcript.txt").write_text("", encoding="utf-8")

            source_url = "https://channels.weixin.qq.com/web/pages/feed?oid=target&exportkey=target-key"
            media_link = Mock(url="https://wxsmw.wxs.qq.com/video/target.mp4?token=ok")

            with (
                patch(
                    "web.app.find_recent_wechat_channels_media_urls",
                    return_value=[media_link],
                    create=True,
                ) as find_media,
                patch(
                    "web.app.process_douyin_share",
                    side_effect=[
                        RuntimeError(
                            "WeChat Channels media URL was not exposed in the share page. "
                            "This link may need a browser-assisted resolver."
                        ),
                        out_dir,
                    ],
                ),
            ):
                response = app.test_client().post(
                    "/api/wechat-radium/extract",
                    json={"url": source_url},
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(find_media.call_args.kwargs["anchor_url"], source_url)
        self.assertTrue(find_media.call_args.kwargs["require_anchor"])

    def test_candidates_returns_anchored_media_options(self):
        source_url = "https://weixin.qq.com/sph/A2grkLkAXh"
        media_link = Mock(
            url="https://wxsmw.wxs.qq.com/video/target.mp4?token=ok",
            path="C:/Users/example/AppData/Roaming/Tencent/xwechat/radium/web/profiles/multitab/Local Storage/leveldb/007351.log",
            modified_at=1783524796.0,
            size=47575,
            offset=22930,
        )

        with patch(
            "web.app.find_recent_wechat_channels_media_urls",
            return_value=[media_link],
            create=True,
        ) as find_media:
            response = app.test_client().post(
                "/api/wechat-radium/candidates",
                json={"url": source_url, "limit": 5},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["success"])
        self.assertEqual(find_media.call_args.kwargs["anchor_url"], source_url)
        self.assertTrue(find_media.call_args.kwargs["require_anchor"])
        self.assertEqual(response.json["candidates"][0]["url"], media_link.url)
        self.assertEqual(response.json["candidates"][0]["host"], "wxsmw.wxs.qq.com")
        self.assertEqual(response.json["candidates"][0]["file"], "007351.log")


if __name__ == "__main__":
    unittest.main()
