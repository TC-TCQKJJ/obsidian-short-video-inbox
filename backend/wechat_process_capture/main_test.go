//go:build windows

package main

import "testing"

func TestAllowedHosts(t *testing.T) {
	allowed := []string{
		"https://channels.weixin.qq.com/feed",
		"https://finder.video.qq.com/video",
		"https://cdn.finder.video.qq.com/video",
		"https://wxsmw.wxs.qq.com/video.mp4",
		"https://a.wxqcloud.qq.com/video.mp4",
		"https://a.wxlivecdn.com/live.m3u8",
	}
	for _, rawURL := range allowed {
		if !isAllowedURL(rawURL) {
			t.Fatalf("expected allowed URL: %s", rawURL)
		}
	}
}

func TestBroadQQHostsAreNotAllowed(t *testing.T) {
	blocked := []string{
		"https://mail.qq.com/",
		"https://qq.com/",
		"https://evilwxs.qq.com/video.mp4",
		"https://wxs.qq.com.evil.example/video.mp4",
	}
	for _, rawURL := range blocked {
		if isAllowedURL(rawURL) {
			t.Fatalf("expected blocked URL: %s", rawURL)
		}
	}
}

func TestMediaURLIgnoresQueryString(t *testing.T) {
	if !isMediaURL("https://wxsmw.wxs.qq.com/video.mp4?token=secret") {
		t.Fatal("expected signed MP4 URL to be treated as media")
	}
	if !isMediaURL("https://finder.video.qq.com/251/20302/stodownload?token=secret") {
		t.Fatal("expected signed stodownload URL to be treated as media")
	}
	if isMediaURL("https://channels.weixin.qq.com/feed.json") {
		t.Fatal("did not expect JSON URL to be treated as media")
	}
}

func TestLooksLikeJSON(t *testing.T) {
	if !looksLikeJSON("application/json; charset=utf-8", []byte("compressed")) {
		t.Fatal("expected JSON content type to be accepted")
	}
	if !looksLikeJSON("text/plain", []byte("  {\"ok\":true}")) {
		t.Fatal("expected JSON-shaped body to be accepted")
	}
	if looksLikeJSON("application/octet-stream", []byte{0, 1, 2}) {
		t.Fatal("did not expect binary body to be accepted")
	}
}
