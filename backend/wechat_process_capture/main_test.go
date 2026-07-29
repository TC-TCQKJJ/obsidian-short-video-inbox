//go:build windows

package main

import (
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/pem"
	"math/big"
	"strings"
	"testing"
	"time"
)

func TestAllowedHosts(t *testing.T) {
	allowed := []string{
		"https://channels.weixin.qq.com/feed",
		"https://res.wx.qq.com/t/wx_fed/finder/app.js",
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

func TestCaptureFeedURLIsNarrowlyScoped(t *testing.T) {
	if !isCaptureFeedURL("https://channels.weixin.qq.com/__xiaolou_capture/feed") {
		t.Fatal("expected the local feed endpoint to be recognized")
	}
	for _, rawURL := range []string{
		"https://channels.weixin.qq.com/__xiaolou_capture/other",
		"https://res.wx.qq.com/__xiaolou_capture/feed",
		"https://channels.weixin.qq.com.evil.example/__xiaolou_capture/feed",
	} {
		if isCaptureFeedURL(rawURL) {
			t.Fatalf("expected local feed endpoint to reject %s", rawURL)
		}
	}
}

func TestCaptureActiveURLIsNarrowlyScoped(t *testing.T) {
	if !isCaptureActiveURL(
		"https://channels.weixin.qq.com/__xiaolou_capture/active.js",
	) {
		t.Fatal("expected the local active script endpoint to be recognized")
	}
	for _, rawURL := range []string{
		"https://channels.weixin.qq.com/__xiaolou_capture/other.js",
		"https://res.wx.qq.com/__xiaolou_capture/active.js",
		"https://channels.weixin.qq.com.evil.example/__xiaolou_capture/active.js",
	} {
		if isCaptureActiveURL(rawURL) {
			t.Fatalf("expected local active script endpoint to reject %s", rawURL)
		}
	}
}

func TestInstrumentWechatHTMLBustsScriptCacheOnly(t *testing.T) {
	body := []byte(`<html><head><script src="https://res.wx.qq.com/app.js"></script></head><body><img src="cover.jpg"></body></html>`)
	modified, ok := instrumentWechatResponse(
		"https://channels.weixin.qq.com/web/pages/feed",
		"text/html; charset=utf-8",
		body,
	)
	if !ok {
		t.Fatal("expected WeChat Channels HTML to be modified")
	}
	text := string(modified)
	if !strings.Contains(text, `app.js?xiaolou_capture=`+captureCacheToken) {
		t.Fatal("expected the script URL to be cache-busted")
	}
	if !strings.Contains(text, `src="cover.jpg"`) {
		t.Fatal("expected non-script assets to remain unchanged")
	}
	if !strings.Contains(
		text,
		`src="/__xiaolou_capture/active.js?xiaolou_capture=`+
			captureCacheToken,
	) {
		t.Fatal("expected the active-video bridge to load in the page")
	}
	if strings.Index(text, captureActivePath) > strings.Index(text, "</head>") {
		t.Fatal("expected the active-video bridge before the closing head")
	}
	if !strings.Contains(captureVideoBridgeScript, `context_texts:`) {
		t.Fatal("expected the active-video bridge to send DOM contexts")
	}
	if !strings.Contains(captureVideoBridgeScript, `document.createTreeWalker`) {
		t.Fatal("expected the active-video bridge to inspect visible text nodes")
	}
	if strings.Contains(captureVideoBridgeScript, `node.innerText`) {
		t.Fatal("did not expect hidden ancestor text in active-video contexts")
	}
	if !strings.Contains(captureVideoBridgeScript, `visibleVideoScore`) {
		t.Fatal("expected the active-video bridge to rank visible videos")
	}
	if strings.Contains(
		captureVideoBridgeScript,
		`if(!video.paused)captureVideo(video)`,
	) {
		t.Fatal("did not expect every unpaused preload video to be captured")
	}
	if !strings.Contains(captureVideoBridgeScript, `media_url:""`) {
		t.Fatal("expected active DOM matching to ignore stale player media URLs")
	}
	if strings.Contains(captureVideoBridgeScript, `videoSource`) {
		t.Fatal("did not expect player source probing in active DOM matching")
	}
	if !strings.Contains(captureVideoBridgeScript, `document.elementFromPoint`) {
		t.Fatal("expected occluded preload cards to be excluded")
	}
	if !strings.Contains(captureVideoBridgeScript, `visibleStyle(parent)`) {
		t.Fatal("expected hidden text ancestors to be excluded")
	}
}

func TestInstrumentWechatAPIFunctionPostsNormalizedMetadata(t *testing.T) {
	body := []byte(`async finderGetCommentDetail(e){return await api(e)}async next(){}`)
	rawURL := "https://res.wx.qq.com/t/wx_fed/finder/web/web-finder/res/js/virtual_svg-icons-register.publish.js"
	if !isTargetFeedScript(rawURL, "application/javascript") {
		t.Fatal("expected the WeChat API script to be recognized")
	}
	modified, ok := instrumentWechatResponse(
		rawURL,
		"application/javascript",
		body,
	)
	if !ok {
		t.Fatal("expected the WeChat API script to be instrumented")
	}
	text := string(modified)
	for _, expected := range []string{
		`/__xiaolou_capture/feed`,
		`xiaolou_capture_active_v1`,
		`decrypt_key:__xiaolou_active__?0:Number`,
		`__xiaolou_capture_feed__(__xiaolou_object__,false)`,
		`return __xiaolou_result__`,
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("expected instrumented script to contain %q", expected)
		}
	}
	if strings.Contains(text, `==="finderGetCommentDetail"`) {
		t.Fatal("did not expect comment preload state to mark a feed active")
	}
}

func TestInstrumentWechatInteractionFeedAndCurrentItem(t *testing.T) {
	body := []byte(
		`async finderGetInteractionedFeedList(e){return await api(e)}}const x=1;` +
			`class A{` +
			`setCurrentFeedIndex(t,i){this.currentFeedIndex=t}coolDownSwitchFeed(){}` +
			`goToNextFlowFeed(t){this.currentFeedIndex++}insertFeed(){}` +
			`replaceFeeds(t,i=!0){this.feeds=t;this.currentFeedIndex=0}}class B{}`,
	)
	modified, ok := instrumentWechatResponse(
		"https://res.wx.qq.com/t/virtual_svg-icons-register.publish.js",
		"application/javascript",
		body,
	)
	if !ok {
		t.Fatal("expected interaction and current-feed methods to be instrumented")
	}
	text := string(modified)
	for _, expected := range []string{
		`finderGetInteractionedFeedList`,
		`globalThis.__xiaolou_capture_feed__`,
		`this.currentFeed,false`,
		`xiaolou_capture_active_v1`,
		`media_url:__xiaolou_active__?"":__xiaolou_url__`,
	} {
		if !strings.Contains(text, expected) {
			t.Fatalf("expected current-feed instrumentation to contain %q", expected)
		}
	}
	if strings.Contains(text, `__xiaolou_capture_feed__(this.currentFeed,true)`) {
		t.Fatal("did not expect internal preload state to mark a feed active")
	}
}

func TestInstrumentWechatJavascriptBustsOnlyTargetScriptImport(t *testing.T) {
	body := []byte(
		`import"./worker-client.publishABC.js";` +
			`import"./virtual_svg-icons-register.publishXYZ.js";`,
	)
	modified, ok := instrumentWechatResponse(
		"https://res.wx.qq.com/t/index.publishABC.js",
		"application/javascript",
		body,
	)
	if !ok {
		t.Fatal("expected the target script import to be cache-busted")
	}
	text := string(modified)
	if !strings.Contains(
		text,
		`virtual_svg-icons-register.publishXYZ.js?xiaolou_capture=`+
			captureCacheToken,
	) {
		t.Fatal("expected the target script import to use the session token")
	}
	if !strings.Contains(text, `worker-client.publishABC.js"`) {
		t.Fatal("expected unrelated imports to remain unchanged")
	}
}

func TestTargetFeedScriptIsNarrowlyScoped(t *testing.T) {
	for _, testCase := range []struct {
		rawURL      string
		contentType string
	}{
		{
			rawURL:      "https://res.wx.qq.com/t/virtual_svg-icons-register.publish.js",
			contentType: "application/javascript",
		},
		{
			rawURL:      "https://res.wx.qq.com/t/virtual_svg-icons-register.publish.js",
			contentType: "text/javascript",
		},
	} {
		if !isTargetFeedScriptURL(testCase.rawURL) {
			t.Fatalf("expected target script URL: %s", testCase.rawURL)
		}
		if !isTargetFeedScript(testCase.rawURL, testCase.contentType) {
			t.Fatalf("expected target script: %s", testCase.rawURL)
		}
	}
	for _, rawURL := range []string{
		"https://res.wx.qq.com/t/unrelated.publish.js",
		"https://channels.weixin.qq.com/t/virtual_svg-icons-register.publish.js",
		"https://res.wx.qq.com.evil.example/t/virtual_svg-icons-register.publish.js",
	} {
		if isTargetFeedScriptURL(rawURL) {
			t.Fatalf("expected unrelated script URL to be rejected: %s", rawURL)
		}
		if isTargetFeedScript(rawURL, "application/javascript") {
			t.Fatalf("expected unrelated script to be rejected: %s", rawURL)
		}
	}
}

func TestIdentityResponseScopeIsNarrow(t *testing.T) {
	for _, rawURL := range []string{
		"https://channels.weixin.qq.com/web/pages/feed",
		"https://channels.weixin.qq.com/web/pages/home?flow=2",
		"https://res.wx.qq.com/t/virtual_svg-icons-register.publish.js",
	} {
		if !shouldForceIdentityResponse(rawURL) {
			t.Fatalf("expected identity response for %s", rawURL)
		}
	}
	for _, rawURL := range []string{
		"https://channels.weixin.qq.com/finder/feed",
		"https://finder.video.qq.com/video.mp4",
		"https://res.wx.qq.com/t/unrelated.js",
	} {
		if shouldForceIdentityResponse(rawURL) {
			t.Fatalf("expected normal response handling for %s", rawURL)
		}
	}
}

func TestInstrumentWechatResponseLeavesUnrelatedJavascriptUnchanged(t *testing.T) {
	body := []byte(`console.log("unrelated")`)
	modified, ok := instrumentWechatResponse(
		"https://res.wx.qq.com/static/unrelated.js",
		"application/javascript",
		body,
	)
	if ok || string(modified) != string(body) {
		t.Fatal("expected unrelated JavaScript to remain unchanged")
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
	if !isMediaURL("https://wxsmw.wxs.qq.com/251/20302/media?token=secret") {
		t.Fatal("expected an extensionless media CDN URL to be treated as media")
	}
	if isMediaURL("https://channels.weixin.qq.com/feed.json") {
		t.Fatal("did not expect JSON URL to be treated as media")
	}
	if isMediaURL("https://mail.qq.com/extensionless") {
		t.Fatal("did not expect an unrelated extensionless URL to be media")
	}
}

func TestLoopbackURL(t *testing.T) {
	if !isLoopbackURL("https://127.0.0.1:7897/test.mp4") {
		t.Fatal("expected a loopback proxy URL to be detected")
	}
	if !isLoopbackURL("https://localhost:7897/test.mp4") {
		t.Fatal("expected localhost to be detected")
	}
	if isLoopbackURL("https://finder.video.qq.com/test.mp4") {
		t.Fatal("did not expect a remote capture URL to be loopback")
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

func TestParseLoopbackProxy(t *testing.T) {
	tests := []struct {
		name    string
		raw     string
		wantURL string
		wantOK  bool
	}{
		{
			name:    "single mixed proxy",
			raw:     "127.0.0.1:7897",
			wantURL: "http://127.0.0.1:7897",
			wantOK:  true,
		},
		{
			name:    "protocol-specific proxy",
			raw:     "http=127.0.0.1:8080;https=127.0.0.1:7897",
			wantURL: "http://127.0.0.1:7897",
			wantOK:  true,
		},
		{
			name:   "remote proxy is rejected",
			raw:    "proxy.example.com:8080",
			wantOK: false,
		},
		{
			name:   "credentials are rejected",
			raw:    "http://user:secret@127.0.0.1:7897",
			wantOK: false,
		},
	}

	for _, test := range tests {
		t.Run(test.name, func(t *testing.T) {
			proxyURL, targets, ok := parseLoopbackProxy(test.raw)
			if ok != test.wantOK {
				t.Fatalf("parseLoopbackProxy(%q) ok = %v, want %v", test.raw, ok, test.wantOK)
			}
			if proxyURL != test.wantURL {
				t.Fatalf("parseLoopbackProxy(%q) URL = %q, want %q", test.raw, proxyURL, test.wantURL)
			}
			if ok && len(targets) < 2 {
				t.Fatal("expected host and host:port interception targets")
			}
		})
	}
}

func TestCreateCaptureCertificate(t *testing.T) {
	rootKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		t.Fatal(err)
	}
	rootTemplate := &x509.Certificate{
		SerialNumber:          big.NewInt(1),
		Subject:               pkix.Name{CommonName: "test capture CA"},
		NotBefore:             time.Now().Add(-time.Hour),
		NotAfter:              time.Now().Add(24 * time.Hour),
		KeyUsage:              x509.KeyUsageCertSign | x509.KeyUsageDigitalSignature,
		BasicConstraintsValid: true,
		IsCA:                  true,
	}
	rootDER, err := x509.CreateCertificate(
		rand.Reader,
		rootTemplate,
		rootTemplate,
		&rootKey.PublicKey,
		rootKey,
	)
	if err != nil {
		t.Fatal(err)
	}
	rootCertPEM := pem.EncodeToMemory(&pem.Block{
		Type:  "CERTIFICATE",
		Bytes: rootDER,
	})
	rootKeyPEM := pem.EncodeToMemory(&pem.Block{
		Type:  "RSA PRIVATE KEY",
		Bytes: x509.MarshalPKCS1PrivateKey(rootKey),
	})

	leafCertPEM, leafKeyPEM, err := createCaptureCertificate(
		rootCertPEM,
		rootKeyPEM,
	)
	if err != nil {
		t.Fatal(err)
	}
	leafPair, err := tls.X509KeyPair(leafCertPEM, leafKeyPEM)
	if err != nil {
		t.Fatal(err)
	}
	leafCert, err := x509.ParseCertificate(leafPair.Certificate[0])
	if err != nil {
		t.Fatal(err)
	}
	rootCert, err := x509.ParseCertificate(rootDER)
	if err != nil {
		t.Fatal(err)
	}
	if err := leafCert.CheckSignatureFrom(rootCert); err != nil {
		t.Fatalf("leaf certificate is not signed by the per-machine CA: %v", err)
	}
	for _, host := range []string{
		"channels.weixin.qq.com",
		"res.wx.qq.com",
		"cdn.finder.video.qq.com",
		"wxsmw.wxs.qq.com",
	} {
		if err := leafCert.VerifyHostname(host); err != nil {
			t.Fatalf("leaf certificate does not cover %s: %v", host, err)
		}
	}
	leafKey, ok := leafPair.PrivateKey.(*rsa.PrivateKey)
	if !ok {
		t.Fatal("leaf certificate did not use an RSA key")
	}
	if leafKey.PublicKey.N.Cmp(rootKey.PublicKey.N) == 0 {
		t.Fatal("leaf certificate reused the CA private key")
	}
}
