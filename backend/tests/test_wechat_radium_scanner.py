import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from script.wechat_radium_scanner import (
    find_recent_wechat_channels_links,
    find_recent_wechat_channels_media_urls,
)


class WechatRadiumScannerTest(unittest.TestCase):
    def test_finds_recent_channels_links_in_binary_cache_files(self):
        with TemporaryDirectory() as tmp_dir:
            cache = Path(tmp_dir) / "Share Data"
            cache.write_bytes(
                b"\x00prefix"
                b"https://channels.weixin.qq.com/web/pages/live?oid=abc&exportkey=k%2B1&pass_ticket=t%3D%3D&wx_header=0"
                b"\x00suffix"
            )

            links = find_recent_wechat_channels_links(Path(tmp_dir), minutes=10, limit=5)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].url,
            "https://channels.weixin.qq.com/web/pages/live?oid=abc&exportkey=k+1&pass_ticket=t==&wx_header=0",
        )

    def test_ignores_old_cache_files(self):
        with TemporaryDirectory() as tmp_dir:
            cache = Path(tmp_dir) / "old-cache"
            cache.write_bytes(
                b"https://channels.weixin.qq.com/web/pages/feed?oid=old&exportkey=old"
            )
            old_time = time.time() - 7200
            cache.touch()
            cache.chmod(0o666)
            import os

            os.utime(cache, (old_time, old_time))

            links = find_recent_wechat_channels_links(Path(tmp_dir), minutes=10, limit=5)

        self.assertEqual(links, [])

    def test_finds_recent_media_urls_in_binary_cache_files(self):
        with TemporaryDirectory() as tmp_dir:
            cache = Path(tmp_dir) / "media-cache"
            cache.write_bytes(
                b"\x00prefix"
                b"https://finder.video.qq.com/abc/video.flv?token=t%2B1"
                b"\x00middle"
                b"https://example.com/cover.jpg"
                b"\x00suffix"
            )

            links = find_recent_wechat_channels_media_urls(Path(tmp_dir), minutes=10, limit=5)

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].url,
            "https://finder.video.qq.com/abc/video.flv?token=t+1",
        )

    def test_prefers_later_media_url_in_same_log_file(self):
        with TemporaryDirectory() as tmp_dir:
            cache = Path(tmp_dir) / "current.log"
            cache.write_bytes(
                b"https://wxsmw.wxs.qq.com/video/old.mp4?token=old\n"
                b"some later log line\n"
                b"https://wxsmw.wxs.qq.com/video/current.mp4?token=current\n"
            )

            links = find_recent_wechat_channels_media_urls(Path(tmp_dir), minutes=10, limit=2)

        self.assertEqual(
            [link.url for link in links],
            [
                "https://wxsmw.wxs.qq.com/video/current.mp4?token=current",
                "https://wxsmw.wxs.qq.com/video/old.mp4?token=old",
            ],
        )

    def test_anchor_url_prefers_media_from_matching_cache_file(self):
        with TemporaryDirectory() as tmp_dir:
            current = Path(tmp_dir) / "current.log"
            current.write_bytes(
                b"https://channels.weixin.qq.com/web/pages/feed?oid=target&exportkey=target-key\n"
                b"https://wxsmw.wxs.qq.com/video/target.mp4?token=target\n"
            )
            unrelated = Path(tmp_dir) / "newer.log"
            unrelated.write_bytes(
                b"https://wxsmw.wxs.qq.com/video/unrelated.mp4?token=unrelated\n"
            )
            newer_time = time.time() + 5
            import os

            os.utime(unrelated, (newer_time, newer_time))

            links = find_recent_wechat_channels_media_urls(
                Path(tmp_dir),
                minutes=10,
                limit=2,
                anchor_url="https://channels.weixin.qq.com/web/pages/feed?oid=target&exportkey=target-key",
            )

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].url,
            "https://wxsmw.wxs.qq.com/video/target.mp4?token=target",
        )

    def test_sph_short_link_anchor_matches_redirected_channels_record(self):
        with TemporaryDirectory() as tmp_dir:
            current = Path(tmp_dir) / "current.log"
            current.write_bytes(
                b"https://channels.weixin.qq.com/finder-preview/pages/sph?id=A2grkLkAXh\n"
                b"https://wxsmw.wxs.qq.com/video/target.mp4?token=target\n"
            )

            links = find_recent_wechat_channels_media_urls(
                Path(tmp_dir),
                minutes=10,
                limit=2,
                anchor_url="https://weixin.qq.com/sph/A2grkLkAXh",
                require_anchor=True,
            )

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].url,
            "https://wxsmw.wxs.qq.com/video/target.mp4?token=target",
        )

    def test_anchor_in_share_data_matches_media_from_same_profile(self):
        with TemporaryDirectory() as tmp_dir:
            profile = Path(tmp_dir) / "web" / "profiles" / "multitab_abc"
            share_data = profile / "Share Data"
            share_data.parent.mkdir(parents=True)
            share_data.write_bytes(
                b"https://channels.weixin.qq.com/finder-preview/pages/sph?id=A2grkLkAXh"
            )
            media_log = profile / "Local Storage" / "leveldb" / "007351.log"
            media_log.parent.mkdir(parents=True)
            media_log.write_bytes(
                b"https://wxsmw.wxs.qq.com/video/target.mp4?token=target\n"
            )
            unrelated = Path(tmp_dir) / "web" / "profiles" / "multitab_other" / "Local Storage" / "leveldb" / "newer.log"
            unrelated.parent.mkdir(parents=True)
            unrelated.write_bytes(
                b"https://wxsmw.wxs.qq.com/video/unrelated.mp4?token=unrelated\n"
            )
            import os

            now = time.time()
            os.utime(share_data, (now, now))
            os.utime(media_log, (now + 10, now + 10))
            os.utime(unrelated, (now + 20, now + 20))

            links = find_recent_wechat_channels_media_urls(
                Path(tmp_dir),
                minutes=10,
                limit=2,
                anchor_url="https://weixin.qq.com/sph/A2grkLkAXh",
                require_anchor=True,
            )

        self.assertEqual(len(links), 1)
        self.assertEqual(
            links[0].url,
            "https://wxsmw.wxs.qq.com/video/target.mp4?token=target",
        )

    def test_require_anchor_returns_empty_when_anchor_is_not_in_cache(self):
        with TemporaryDirectory() as tmp_dir:
            cache = Path(tmp_dir) / "current.log"
            cache.write_bytes(
                b"https://wxsmw.wxs.qq.com/video/unrelated.mp4?token=unrelated\n"
            )

            links = find_recent_wechat_channels_media_urls(
                Path(tmp_dir),
                minutes=10,
                limit=2,
                anchor_url="https://weixin.qq.com/sph/A2grkLkAXh",
                require_anchor=True,
            )

        self.assertEqual(links, [])

    def test_ignores_favicon_database_when_finding_channels_pages(self):
        with TemporaryDirectory() as tmp_dir:
            favicons = Path(tmp_dir) / "Favicons"
            favicons.write_bytes(
                b"https://channels.weixin.qq.com/web/pages/feed?oid=old&exportkey=old"
            )
            cache = Path(tmp_dir) / "Cache_Data"
            cache.write_bytes(
                b"https://channels.weixin.qq.com/web/pages/feed?oid=current&exportkey=current"
            )

            links = find_recent_wechat_channels_links(Path(tmp_dir), minutes=10, limit=5)

        self.assertEqual(len(links), 1)
        self.assertIn("oid=current", links[0].url)

    def test_ignores_non_wechat_media_urls(self):
        with TemporaryDirectory() as tmp_dir:
            cache = Path(tmp_dir) / "external-media-cache"
            cache.write_bytes(
                b"https://liveplay.example.com/live/video.flv?token=external"
            )

            links = find_recent_wechat_channels_media_urls(Path(tmp_dir), minutes=10, limit=5)

        self.assertEqual(links, [])


if __name__ == "__main__":
    unittest.main()
