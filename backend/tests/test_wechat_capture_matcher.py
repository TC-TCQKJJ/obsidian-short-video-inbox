from dataclasses import replace
import json
import time
import unittest
from unittest.mock import patch

from wechat_capture.feed_parser import parse_feed_objects
from wechat_capture.matcher import CaptureMatcher
from wechat_capture.model import CaptureCandidate


FEED = {
    "id": "feed-123",
    "objectNonceId": "nonce-1",
    "contact": {"nickname": "小楼测试"},
    "objectDesc": {
        "description": "目标直播回放",
        "media": [
            {
                "url": "https://wxsmw.wxs.qq.com/video/feed-123.mp4",
                "urlToken": "?token=secret",
                "decodeKey": "123456",
                "coverUrl": "https://wxsmw.wxs.qq.com/cover/feed-123.jpg",
                "duration": 3600000,
            }
        ],
    },
}

FEED_WITH_OBJECT_ID = {
    "objectId": "feed-object-456",
    "objectDesc": {
        "description": "对象回退",
        "media": [
            {
                "url": "https://wxsmw.wxs.qq.com/video/feed-object-456.mp4",
                "urlToken": "?token=object",
            }
        ],
    },
}


class CaptureContractAndMatcherTest(unittest.TestCase):
    def test_capture_candidate_serializes_public_and_private_fields(self):
        candidate = CaptureCandidate(
            capture_id="feed-123",
            title="目标直播回放",
            author="小楼测试",
            source_url="https://channels.weixin.qq.com/web/pages/feed?id=feed-123",
            media_url="https://wxsmw.wxs.qq.com/video/feed-123.mp4?token=secret",
            cover_url="https://wxsmw.wxs.qq.com/cover/feed-123.jpg",
            duration_ms=3600000,
            decrypt_key=123456,
            request_headers={"Range": "bytes=0-"},
            observed_at=100.0,
        )

        self.assertEqual(
            candidate.public_dict(),
            {
                "capture_id": "feed-123",
                "title": "目标直播回放",
                "author": "小楼测试",
                "source_url": "https://channels.weixin.qq.com/web/pages/feed?id=feed-123",
                "cover_url": "https://wxsmw.wxs.qq.com/cover/feed-123.jpg",
                "duration_ms": 3600000,
                "observed_at": 100.0,
            },
        )
        self.assertEqual(
            candidate.to_private_dict(),
            {
                "capture_id": "feed-123",
                "title": "目标直播回放",
                "author": "小楼测试",
                "source_url": "https://channels.weixin.qq.com/web/pages/feed?id=feed-123",
                "cover_url": "https://wxsmw.wxs.qq.com/cover/feed-123.jpg",
                "duration_ms": 3600000,
                "observed_at": 100.0,
                "media_url": "https://wxsmw.wxs.qq.com/video/feed-123.mp4?token=secret",
                "decrypt_key": 123456,
                "request_headers": {"Range": "bytes=0-"},
            },
        )

    def test_parser_extracts_structured_feed_from_nested_payload(self):
        items = parse_feed_objects({"data": {"object": FEED}})

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].capture_id, "feed-123")
        self.assertEqual(items[0].title, "目标直播回放")
        self.assertEqual(items[0].author, "小楼测试")
        self.assertEqual(
            items[0].media_url,
            "https://wxsmw.wxs.qq.com/video/feed-123.mp4?token=secret",
        )
        self.assertEqual(items[0].decrypt_key, 123456)

    def test_parser_extracts_json_encoded_object_description(self):
        feed = dict(FEED)
        feed["objectDesc"] = json.dumps(FEED["objectDesc"])

        items = parse_feed_objects({"data": {"object": feed}})

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].capture_id, "feed-123")
        self.assertEqual(
            items[0].media_url,
            "https://wxsmw.wxs.qq.com/video/feed-123.mp4?token=secret",
        )

    def test_parser_extracts_normalized_local_instrumentation_payload(self):
        payload = {
            "schema": "xiaolou_capture_v1",
            "capture_id": "feed-normalized-1",
            "nonce_id": "nonce-normalized",
            "title": "Normalized video",
            "author": "Normalized author",
            "media_url": (
                "https://finder.video.qq.com/251/20302/stodownload"
                "?token=normalized"
            ),
            "cover_url": "https://wxsmw.wxs.qq.com/cover/normalized.jpg",
            "duration_ms": 123000,
            "decrypt_key": 456789,
        }

        items = parse_feed_objects(payload)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].capture_id, "feed-normalized-1")
        self.assertEqual(items[0].title, "Normalized video")
        self.assertEqual(items[0].author, "Normalized author")
        self.assertEqual(items[0].duration_ms, 123000)
        self.assertEqual(items[0].decrypt_key, 456789)
        self.assertIn("nonce=nonce-normalized", items[0].source_url)

    def test_parser_rejects_untrusted_normalized_media_url(self):
        payload = {
            "schema": "xiaolou_capture_v1",
            "capture_id": "feed-untrusted",
            "media_url": "https://example.com/private.mp4?token=secret",
        }

        self.assertEqual(parse_feed_objects(payload), [])

    def test_parser_accepts_extensionless_media_cdn_url(self):
        payload = {
            "schema": "xiaolou_capture_v1",
            "capture_id": "feed-extensionless",
            "media_url": (
                "https://wxsmw.wxs.qq.com/251/20302/media"
                "?token=extensionless"
            ),
        }

        items = parse_feed_objects(payload)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].capture_id, "feed-extensionless")

    def test_parser_marks_active_instrumentation_payload(self):
        payload = {
            "schema": "xiaolou_capture_active_v1",
            "capture_id": "feed-active",
        }

        items = parse_feed_objects(payload)

        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].is_active)
        self.assertEqual(items[0].media_url, "")

    def test_parser_accepts_active_playing_media_without_capture_id(self):
        items = parse_feed_objects(
            {
                "schema": "xiaolou_capture_active_v1",
                "capture_id": "",
                "media_url": (
                    "https://findera4.video.qq.com/251/20302/stodownload"
                    "?token=active"
                ),
            }
        )

        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].is_active)
        self.assertEqual(items[0].capture_id, "")

    def test_parser_accepts_active_blob_with_bounded_dom_contexts(self):
        items = parse_feed_objects(
            {
                "schema": "xiaolou_capture_active_v1",
                "capture_id": "",
                "media_url": "blob:https://channels.weixin.qq.com/player",
                "context_texts": [
                    "  Player controls  ",
                    "Target title  Target author",
                    "Target title  Target author",
                    123,
                ],
            }
        )

        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].is_active)
        self.assertEqual(items[0].media_url, "")
        self.assertEqual(
            items[0].context_texts,
            ("Player controls", "Target title Target author"),
        )

    def test_normalized_instrumentation_matches_observed_media_request(self):
        payload = {
            "schema": "xiaolou_capture_v1",
            "capture_id": "feed-instrumented",
            "media_url": (
                "https://finder.video.qq.com/251/20302/stodownload"
                "?token=feed"
            ),
            "decrypt_key": 987654,
        }
        matcher = CaptureMatcher(max_age_seconds=30)
        matcher.record_feed(parse_feed_objects(payload)[0])

        matcher.record_media_request(
            "https://finder.video.qq.com/251/20302/stodownload?token=latest",
            observed_at=time.time(),
        )

        matches = matcher.recent_candidates()
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].capture_id, "feed-instrumented")
        self.assertEqual(matches[0].decrypt_key, 987654)

    def test_media_request_can_arrive_before_instrumented_feed(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        observed_at = time.time()
        matcher.record_media_request(
            "https://finder.video.qq.com/251/20302/stodownload?token=latest",
            observed_at=observed_at,
            request_headers={"Range": "bytes=0-"},
        )
        payload = {
            "schema": "xiaolou_capture_v1",
            "capture_id": "feed-late",
            "media_url": (
                "https://finder.video.qq.com/251/20302/stodownload"
                "?token=feed"
            ),
            "decrypt_key": 123456,
        }

        matcher.record_feed(parse_feed_objects(payload)[0])

        matches = matcher.recent_candidates()
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0].capture_id, "feed-late")
        self.assertEqual(matches[0].observed_at, observed_at)
        self.assertEqual(matches[0].request_headers, {"Range": "bytes=0-"})

    def test_parser_deduplicates_and_accepts_contact_nickname_variants(self):
        payload = {
            "entries": [
                {"object": FEED},
                {
                    "object": {
                        "id": "feed-123",
                        "contact": {"nickName": "别名作者"},
                        "objectDesc": {
                            "description": "重复对象",
                            "media": [
                                {
                                    "url": "https://wxsmw.wxs.qq.com/video/feed-123.mp4",
                                    "urlToken": "?token=secret",
                                }
                            ],
                        },
                    }
                },
            ]
        }

        items = parse_feed_objects(payload)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].author, "小楼测试")

    def test_parser_rejects_feed_when_url_token_is_missing_or_empty(self):
        missing_token = {
            "object": {
                "id": "feed-missing-token",
                "objectDesc": {
                    "description": "缺少 token",
                    "media": [{"url": "https://wxsmw.wxs.qq.com/video/feed-missing-token.mp4"}],
                },
            }
        }
        empty_token = {
            "object": {
                "id": "feed-empty-token",
                "objectDesc": {
                    "description": "空 token",
                    "media": [
                        {
                            "url": "https://wxsmw.wxs.qq.com/video/feed-empty-token.mp4",
                            "urlToken": "",
                        }
                    ],
                },
            }
        }

        self.assertEqual(parse_feed_objects(missing_token), [])
        self.assertEqual(parse_feed_objects(empty_token), [])

    def test_parser_falls_back_to_object_id_when_id_is_absent(self):
        items = parse_feed_objects({"data": {"object": FEED_WITH_OBJECT_ID}})

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].capture_id, "feed-object-456")
        self.assertEqual(
            items[0].media_url,
            "https://wxsmw.wxs.qq.com/video/feed-object-456.mp4?token=object",
        )

    def test_parser_uses_empty_author_when_contact_is_missing(self):
        payload = {
            "object": {
                "id": "feed-no-author",
                "objectDesc": {
                    "description": "无作者",
                    "media": [
                        {
                            "url": "https://wxsmw.wxs.qq.com/video/feed-no-author.mp4",
                            "urlToken": "?token=no-author",
                        }
                    ],
                },
            }
        }

        items = parse_feed_objects(payload)

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].author, "")

    def test_media_request_promotes_only_matching_feed_and_retains_signed_url(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        candidate = parse_feed_objects({"object": FEED})[0]
        matcher.record_feed(candidate)
        observed_at = time.time()

        matcher.record_media_request(
            "https://wxsmw.wxs.qq.com/video/feed-123.mp4?token=latest",
            observed_at=observed_at,
            request_headers={"Range": "bytes=0-"},
        )

        matches = matcher.recent_candidates()
        self.assertEqual([item.capture_id for item in matches], ["feed-123"])
        self.assertEqual(
            matches[0].media_url,
            "https://wxsmw.wxs.qq.com/video/feed-123.mp4?token=latest",
        )
        self.assertEqual(matches[0].request_headers, {"Range": "bytes=0-"})
        self.assertEqual(matches[0].observed_at, observed_at)
        self.assertEqual(matcher.feed_count, 1)

    def test_unmatched_media_request_does_not_guess(self):
        matcher = CaptureMatcher(max_age_seconds=30)
        matcher.record_feed(parse_feed_objects({"object": FEED})[0])

        matcher.record_media_request(
            "https://wxsmw.wxs.qq.com/video/other.mp4?token=x",
            observed_at=time.time(),
        )

        self.assertEqual(matcher.recent_candidates(), [])

    def test_active_mode_hides_matched_preloads_until_current_feed_arrives(self):
        matcher = CaptureMatcher(max_age_seconds=30, require_active=True)
        preload = parse_feed_objects({"object": FEED})[0]
        matcher.record_feed(preload)
        matcher.record_media_request(
            preload.media_url,
            observed_at=time.time(),
        )

        self.assertEqual(matcher.recent_candidates(), [])

        active = replace(preload, is_active=True)
        matcher.record_feed(active)

        self.assertEqual(
            [item.capture_id for item in matcher.recent_candidates()],
            ["feed-123"],
        )

    def test_active_mode_returns_only_latest_current_feed(self):
        matcher = CaptureMatcher(max_age_seconds=30, require_active=True)
        first = replace(
            parse_feed_objects({"object": FEED})[0],
            is_active=True,
        )
        second_payload = {
            **FEED_WITH_OBJECT_ID,
            "objectDesc": {
                **FEED_WITH_OBJECT_ID["objectDesc"],
                "media": [
                    {
                        "url": "https://wxsmw.wxs.qq.com/video/second",
                        "urlToken": "?token=second",
                    }
                ],
            },
        }
        second = replace(
            parse_feed_objects({"object": second_payload})[0],
            is_active=True,
        )
        for candidate in (first, second):
            preload = replace(candidate, is_active=False)
            matcher.record_feed(preload)
            matcher.record_media_request(
                candidate.media_url,
                observed_at=time.time(),
            )
        matcher.record_feed(first)
        matcher.record_feed(second)

        self.assertEqual(
            [item.capture_id for item in matcher.recent_candidates()],
            ["feed-object-456"],
        )

    def test_active_mode_joins_current_feed_to_media_by_capture_id(self):
        matcher = CaptureMatcher(max_age_seconds=30, require_active=True)
        preload = parse_feed_objects({"object": FEED})[0]
        matcher.record_feed(preload)
        matcher.record_media_request(
            preload.media_url,
            observed_at=time.time(),
        )
        active = parse_feed_objects(
            {
                "schema": "xiaolou_capture_active_v1",
                "capture_id": preload.capture_id,
                "media_url": "https://wx.qlogo.cn/avatar/not-video",
            }
        )[0]

        matcher.record_feed(active)

        matches = matcher.recent_candidates()
        self.assertEqual([item.capture_id for item in matches], ["feed-123"])
        self.assertIn("wxs.qq.com", matches[0].media_url)

    def test_active_mode_joins_playing_media_without_request_recency(self):
        matcher = CaptureMatcher(max_age_seconds=30, require_active=True)
        preload = parse_feed_objects(
            {
                "schema": "xiaolou_capture_v1",
                "capture_id": "feed-playing",
                "title": "Playing target",
                "media_url": (
                    "https://findera4.video.qq.com/251/20302/stodownload"
                    "?token=preload"
                ),
            }
        )[0]
        matcher.record_feed(preload)
        active = parse_feed_objects(
            {
                "schema": "xiaolou_capture_active_v1",
                "capture_id": "",
                "media_url": (
                    "https://findera4.video.qq.com/251/20302/stodownload"
                    "?token=playing"
                ),
            }
        )[0]

        matcher.record_feed(active)

        matches = matcher.recent_candidates()
        self.assertEqual([item.capture_id for item in matches], ["feed-playing"])
        self.assertIn("token=playing", matches[0].media_url)

    def test_active_mode_matches_unique_title_in_nearest_dom_context(self):
        matcher = CaptureMatcher(max_age_seconds=30, require_active=True)
        first = parse_feed_objects(
            {
                "schema": "xiaolou_capture_v1",
                "capture_id": "feed-target",
                "title": "Target title",
                "author": "Target author",
                "media_url": "https://findera4.video.qq.com/target",
            }
        )[0]
        second = parse_feed_objects(
            {
                "schema": "xiaolou_capture_v1",
                "capture_id": "feed-preload",
                "title": "Preloaded title",
                "author": "Other author",
                "media_url": "https://findera4.video.qq.com/preload",
            }
        )[0]
        matcher.record_feed(first)
        matcher.record_feed(second)
        active = parse_feed_objects(
            {
                "schema": "xiaolou_capture_active_v1",
                "media_url": "blob:https://channels.weixin.qq.com/player",
                "context_texts": [
                    "Player controls",
                    "Target title Target author",
                    "Target title Target author Preloaded title Other author",
                ],
            }
        )[0]

        matcher.record_feed(active)

        self.assertEqual(
            [item.capture_id for item in matcher.recent_candidates()],
            ["feed-target"],
        )

    def test_active_mode_rejects_ambiguous_dom_context(self):
        matcher = CaptureMatcher(max_age_seconds=30, require_active=True)
        for capture_id, title in (
            ("feed-first", "First title"),
            ("feed-second", "Second title"),
        ):
            matcher.record_feed(
                parse_feed_objects(
                    {
                        "schema": "xiaolou_capture_v1",
                        "capture_id": capture_id,
                        "title": title,
                        "media_url": (
                            f"https://findera4.video.qq.com/{capture_id}"
                        ),
                    }
                )[0]
            )
        matcher.record_feed(
            parse_feed_objects(
                {
                    "schema": "xiaolou_capture_active_v1",
                    "context_texts": ["First title Second title"],
                }
            )[0]
        )

        self.assertEqual(matcher.recent_candidates(), [])

    def test_recent_candidates_prunes_entries_older_than_max_age(self):
        matcher = CaptureMatcher(max_age_seconds=10)
        candidate = replace(parse_feed_objects({"object": FEED})[0], observed_at=50.0)

        matcher.record_feed(candidate)
        matcher.record_media_request(
            candidate.media_url,
            observed_at=50.0,
        )

        with patch("wechat_capture.matcher.time.time", return_value=55.0):
            self.assertEqual([item.capture_id for item in matcher.recent_candidates()], ["feed-123"])
        with patch("wechat_capture.matcher.time.time", return_value=61.0):
            self.assertEqual(matcher.recent_candidates(), [])
        self.assertEqual(matcher.feed_count, 0)


if __name__ == "__main__":
    unittest.main()
