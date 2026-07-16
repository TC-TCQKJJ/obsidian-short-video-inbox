import unittest

from script.wechat_channels_resolver import (
    extract_wechat_channels_url,
    is_wechat_channels_share,
    parse_wechat_channels_html,
    resolve_wechat_channels_share,
)


class WechatChannelsResolverTest(unittest.TestCase):
    def test_extracts_channels_url_from_share_text(self):
        share = "这个回放很有价值 https://channels.weixin.qq.com/web/pages/feed?feed_id=feed123&nonce=abc"

        self.assertEqual(
            extract_wechat_channels_url(share),
            "https://channels.weixin.qq.com/web/pages/feed?feed_id=feed123&nonce=abc",
        )

    def test_extracts_weixin_sph_short_link(self):
        url = "https://weixin.qq.com/sph/A2grkLkAXh"

        self.assertTrue(is_wechat_channels_share(url))
        self.assertEqual(extract_wechat_channels_url(url), url)

    def test_parses_escaped_mp4_url_from_html_json(self):
        html = """
        <html>
          <head>
            <meta property="og:title" content="直播回放：一小时深度分享">
            <meta property="og:image" content="https://res.wx.qq.com/cover.webp">
          </head>
          <script>
            window.__INITIAL_STATE__ = {
              "nickname": "小楼",
              "playUrl": "https:\\/\\/finder.video.qq.com\\/video\\/abc.mp4?token=1\\u0026from=share"
            };
          </script>
        </html>
        """

        meta = parse_wechat_channels_html(
            html,
            "https://channels.weixin.qq.com/web/pages/feed?feed_id=feed123",
            "https://channels.weixin.qq.com/web/pages/feed?feed_id=feed123",
        )

        self.assertEqual(meta.platform, "wechat_channels")
        self.assertEqual(meta.aweme_id, "feed123")
        self.assertEqual(meta.title, "直播回放：一小时深度分享")
        self.assertEqual(meta.author, "小楼")
        self.assertEqual(
            meta.download_url,
            "https://finder.video.qq.com/video/abc.mp4?token=1&from=share",
        )
        self.assertEqual(meta.cover_url, "https://res.wx.qq.com/cover.webp")

    def test_parses_m3u8_url_from_inline_text(self):
        html = """
        <html><body>
          replay=https%3A%2F%2Ffinder.video.qq.com%2Flive%2Freplay.m3u8%3Fauth%3Dxyz%26expires%3D1
        </body></html>
        """

        meta = parse_wechat_channels_html(
            html,
            "https://channels.weixin.qq.com/live/replay?object_id=object456",
            "https://channels.weixin.qq.com/live/replay?object_id=object456",
        )

        self.assertEqual(meta.aweme_id, "object456")
        self.assertEqual(
            meta.download_url,
            "https://finder.video.qq.com/live/replay.m3u8?auth=xyz&expires=1",
        )

    def test_resolves_direct_wechat_media_cdn_url(self):
        url = "https://wxsmw.wxs.qq.com/video/replay.mp4?token=abc"

        self.assertTrue(is_wechat_channels_share(url))
        meta = resolve_wechat_channels_share(url)

        self.assertEqual(meta.platform, "wechat_channels")
        self.assertEqual(meta.download_url, url)


if __name__ == "__main__":
    unittest.main()
