import unittest
from unittest.mock import patch

from script.pipeline import resolve_content_meta
from script.wechat_channels_resolver import WechatChannelsContentMeta


class PipelinePlatformRoutingTest(unittest.TestCase):
    def test_routes_wechat_channels_links_to_wechat_resolver(self):
        expected = WechatChannelsContentMeta(
            aweme_id="feed123",
            title="视频号回放",
            author="小楼",
            source_url="https://channels.weixin.qq.com/web/pages/feed?feed_id=feed123",
            download_url="https://finder.video.qq.com/video/feed123.mp4",
        )

        with patch("script.pipeline.resolve_wechat_channels_share", return_value=expected):
            meta = resolve_content_meta(
                "https://channels.weixin.qq.com/web/pages/feed?feed_id=feed123"
            )

        self.assertIs(meta, expected)

    def test_routes_wechat_media_cdn_urls_to_wechat_resolver(self):
        expected = WechatChannelsContentMeta(
            aweme_id="media123",
            title="微信视频号视频",
            author="",
            source_url="https://wxsmw.wxs.qq.com/video/media123.mp4?token=abc",
            download_url="https://wxsmw.wxs.qq.com/video/media123.mp4?token=abc",
        )

        with patch("script.pipeline.resolve_wechat_channels_share", return_value=expected):
            meta = resolve_content_meta(
                "https://wxsmw.wxs.qq.com/video/media123.mp4?token=abc"
            )

        self.assertIs(meta, expected)

    def test_routes_weixin_sph_short_links_to_wechat_resolver(self):
        expected = WechatChannelsContentMeta(
            aweme_id="A2grkLkAXh",
            title="微信视频号视频",
            author="",
            source_url="https://weixin.qq.com/sph/A2grkLkAXh",
            download_url="https://wxsmw.wxs.qq.com/video/A2grkLkAXh.mp4",
        )

        with patch("script.pipeline.resolve_wechat_channels_share", return_value=expected):
            meta = resolve_content_meta("https://weixin.qq.com/sph/A2grkLkAXh")

        self.assertIs(meta, expected)


if __name__ == "__main__":
    unittest.main()
