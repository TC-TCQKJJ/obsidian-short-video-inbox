//go:build windows

package main

import (
	"bytes"
	"context"
	"crypto/rand"
	"crypto/rsa"
	"crypto/tls"
	"crypto/x509"
	"crypto/x509/pkix"
	"encoding/json"
	"encoding/pem"
	"errors"
	"fmt"
	"io"
	"math/big"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
	"regexp"
	"strings"
	"sync/atomic"
	"syscall"
	"time"

	"github.com/qtgolang/SunnyNet/SunnyNet"
	sunnyHTTP "github.com/qtgolang/SunnyNet/src/http"
	"github.com/qtgolang/SunnyNet/src/public"
	"golang.org/x/sys/windows/registry"
)

const (
	bridgeURL           = "http://127.0.0.1:2024/v1/events"
	processName         = "WeChatAppEx.exe"
	maxResponseBodySize = 8 * 1024 * 1024
	heartbeatInterval   = 2 * time.Second
	maxBridgeFailures   = 3
	eventQueueSize      = 16
	captureFeedPath     = "/__xiaolou_capture/feed"
)

var exactHosts = map[string]struct{}{
	"weixin.qq.com":          {},
	"channels.weixin.qq.com": {},
	"finder.video.qq.com":    {},
	"res.wx.qq.com":          {},
}

var allowedSuffixes = []string{
	".finder.video.qq.com",
	".wxs.qq.com",
	".wxqcloud.qq.com",
	".wxlivecdn.com",
}

var certificateHosts = []string{
	"weixin.qq.com",
	"channels.weixin.qq.com",
	"finder.video.qq.com",
	"res.wx.qq.com",
	"*.finder.video.qq.com",
	"*.wxs.qq.com",
	"*.wxqcloud.qq.com",
	"*.wxlivecdn.com",
}

var interceptionRules = strings.Join([]string{
	"weixin.qq.com",
	"weixin.qq.com:*",
	"channels.weixin.qq.com",
	"channels.weixin.qq.com:*",
	"finder.video.qq.com",
	"finder.video.qq.com:*",
	"res.wx.qq.com",
	"res.wx.qq.com:*",
	"*.finder.video.qq.com",
	"*.finder.video.qq.com:*",
	"*.wxs.qq.com",
	"*.wxs.qq.com:*",
	"*.wxqcloud.qq.com",
	"*.wxqcloud.qq.com:*",
	"*.wxlivecdn.com",
	"*.wxlivecdn.com:*",
}, ";")

var (
	captureCacheToken         = fmt.Sprintf("%x", time.Now().UnixNano())
	htmlScriptPattern         = regexp.MustCompile(`(src|href)="([^"]+\.js)"`)
	targetScriptImportPattern = regexp.MustCompile(
		`(virtual_svg-icons-register\.publish[^"'?]*\.js)`,
	)
	feedFunctionPattern = regexp.MustCompile(
		`(?s)async (finderGetCommentDetail|finderPcFlow|finderGetRecommend|finderUserPage)\((\w+)\)\{(.*?)\}async`,
	)
)

const feedFunctionReplacement = `async $1($2){var __xiaolou_result__=await(async()=>{$3})();try{var __xiaolou_objects__=__xiaolou_result__&&__xiaolou_result__.data&&__xiaolou_result__.data.object;var __xiaolou_list__=Array.isArray(__xiaolou_objects__)?__xiaolou_objects__:[__xiaolou_objects__];__xiaolou_list__.forEach(function(__xiaolou_object__){try{var __xiaolou_desc__=__xiaolou_object__&&__xiaolou_object__.objectDesc;var __xiaolou_media__=__xiaolou_desc__&&__xiaolou_desc__.media&&__xiaolou_desc__.media[0];if(!__xiaolou_object__||!__xiaolou_desc__||!__xiaolou_media__)return;var __xiaolou_url__=String(__xiaolou_media__.url||"")+String(__xiaolou_media__.urlToken||"");var __xiaolou_id__=String(__xiaolou_object__.id||__xiaolou_object__.objectId||"");if(!__xiaolou_id__||!__xiaolou_url__)return;var __xiaolou_spec__=__xiaolou_media__.spec&&__xiaolou_media__.spec[0]||{};var __xiaolou_contact__=__xiaolou_object__.contact||{};fetch("` + captureFeedPath + `",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({schema:"xiaolou_capture_v1",capture_id:__xiaolou_id__,nonce_id:String(__xiaolou_object__.objectNonceId||__xiaolou_object__.nonceId||""),title:String(__xiaolou_desc__.description||""),author:String(__xiaolou_object__.nickname||__xiaolou_contact__.nickname||__xiaolou_contact__.nickName||""),media_url:__xiaolou_url__,cover_url:String(__xiaolou_media__.coverUrl||""),duration_ms:Number(__xiaolou_media__.duration||__xiaolou_spec__.durationMs||0),decrypt_key:Number(__xiaolou_media__.decodeKey||0)})}).catch(function(){});}catch(__xiaolou_item_error__){}});}catch(__xiaolou_error__){}return __xiaolou_result__;}async`

type capturePaths struct {
	certFile  string
	keyFile   string
	tokenFile string
}

type bridgeEvent struct {
	Type          string            `json:"type"`
	State         string            `json:"state,omitempty"`
	URL           string            `json:"url,omitempty"`
	Headers       map[string]string `json:"headers,omitempty"`
	Body          []byte            `json:"body,omitempty"`
	ObservedAt    float64           `json:"observed_at,omitempty"`
	TCPCount      uint64            `json:"tcp_count,omitempty"`
	HTTPCount     uint64            `json:"http_count,omitempty"`
	CaptureErrors uint64            `json:"capture_error_count,omitempty"`
	LastError     string            `json:"last_capture_error,omitempty"`
	RequestCount  uint64            `json:"request_count,omitempty"`
	ResponseCount uint64            `json:"response_count,omitempty"`
	TargetScripts uint64            `json:"target_script_count,omitempty"`
	Instrumented  uint64            `json:"instrumented_script_count,omitempty"`
	FeedMetadata  uint64            `json:"feed_metadata_count,omitempty"`
	DroppedCount  uint64            `json:"dropped_count,omitempty"`
	FailedCount   uint64            `json:"failed_count,omitempty"`
}

type bridgeResponse struct {
	Stop bool `json:"stop"`
}

type bridgeClient struct {
	token  string
	client *http.Client
}

type eventSender struct {
	bridge          *bridgeClient
	events          chan bridgeEvent
	done            chan struct{}
	upstreamProxy   string
	upstreamTargets map[string]struct{}
	tcpCount        atomic.Uint64
	httpCount       atomic.Uint64
	captureErrors   atomic.Uint64
	lastError       atomic.Value
	requestCount    atomic.Uint64
	responseCount   atomic.Uint64
	targetScripts   atomic.Uint64
	instrumented    atomic.Uint64
	feedMetadata    atomic.Uint64
	droppedCount    atomic.Uint64
	failedCount     atomic.Uint64
}

func main() {
	if err := run(); err != nil {
		os.Exit(1)
	}
}

func run() error {
	paths, err := defaultCapturePaths()
	if err != nil {
		return err
	}
	if err := validateCA(paths.certFile, paths.keyFile); err != nil {
		return err
	}

	tokenBytes, err := os.ReadFile(paths.tokenFile)
	if err != nil {
		return fmt.Errorf("read local bridge token: %w", err)
	}
	token := strings.TrimSpace(string(tokenBytes))
	if len(token) < 43 {
		return errors.New("local bridge token is invalid")
	}

	bridge := newBridgeClient(token)
	if _, err := bridge.post(bridgeEvent{Type: "status", State: "starting"}); err != nil {
		return fmt.Errorf("connect to local capture bridge: %w", err)
	}

	certManager := SunnyNet.NewCertManager()
	if !certManager.LoadX509KeyPair(paths.certFile, paths.keyFile) {
		return errors.New("SunnyNet could not load the per-machine CA")
	}

	sunny := SunnyNet.NewSunny()
	sunny.SetCert(certManager.Context())
	if sunny.Error != nil {
		return fmt.Errorf("configure SunnyNet CA: %w", sunny.Error)
	}
	if err := configureCaptureCertificates(
		sunny,
		paths.certFile,
		paths.keyFile,
	); err != nil {
		return err
	}
	rules := interceptionRules
	proxyURL := ""
	var proxyTargets []string
	if configuredProxy, configuredTargets, ok := currentLoopbackProxy(); ok {
		proxyURL = configuredProxy
		proxyTargets = configuredTargets
		rules += ";" + strings.Join(proxyTargets, ";")
	}
	if err := sunny.SetMustTcpRegexp(rules, false); err != nil {
		return fmt.Errorf("configure narrow interception rules: %w", err)
	}

	sender := newEventSender(bridge, proxyURL, proxyTargets)
	defer sender.close()
	sunny.SetGoCallback(sender.handleHTTP, sender.handleTCP, nil, nil)
	sunny.ProcessCancelAll()
	sunny.ProcessAddName(processName)

	if !sunny.OpenDrive(0) {
		sunny.ProcessCancelAll()
		sunny.Close()
		return errors.New("SunnyNet process driver failed to start")
	}
	defer func() {
		sunny.ProcessCancelAll()
		sunny.Close()
		_, _ = bridge.post(bridgeEvent{
			Type:  "status",
			State: "driver_stopped",
		})
	}()

	if _, err := bridge.post(bridgeEvent{
		Type:  "status",
		State: "driver_ready",
	}); err != nil {
		return fmt.Errorf("report process driver readiness: %w", err)
	}

	ctx, cancel := signal.NotifyContext(
		context.Background(),
		os.Interrupt,
		syscall.SIGTERM,
	)
	defer cancel()

	ticker := time.NewTicker(heartbeatInterval)
	defer ticker.Stop()
	failures := 0
	for {
		select {
		case <-ctx.Done():
			return nil
		case <-ticker.C:
			response, postErr := bridge.post(sender.heartbeat())
			if postErr != nil {
				failures++
				if failures >= maxBridgeFailures {
					return errors.New("local capture bridge became unavailable")
				}
				continue
			}
			failures = 0
			if response.Stop {
				return nil
			}
		}
	}
}

func defaultCapturePaths() (capturePaths, error) {
	cacheDir, err := os.UserCacheDir()
	if err != nil {
		return capturePaths{}, fmt.Errorf("find local application data: %w", err)
	}
	baseDir := filepath.Join(cacheDir, "Xiaolou", "WechatCapture")
	return capturePaths{
		certFile:  filepath.Join(baseDir, "mitm", "mitmproxy-ca-cert.pem"),
		keyFile:   filepath.Join(baseDir, "mitm", "mitmproxy-ca.pem"),
		tokenFile: filepath.Join(baseDir, "auth-token"),
	}, nil
}

func validateCA(certFile, keyFile string) error {
	pair, err := tls.LoadX509KeyPair(certFile, keyFile)
	if err != nil {
		return fmt.Errorf("load per-machine CA: %w", err)
	}
	if len(pair.Certificate) == 0 {
		return errors.New("per-machine CA does not contain a certificate")
	}
	cert, err := x509.ParseCertificate(pair.Certificate[0])
	if err != nil {
		return fmt.Errorf("parse per-machine CA: %w", err)
	}
	if !cert.IsCA || !cert.BasicConstraintsValid {
		return errors.New("configured certificate is not a valid CA")
	}
	if time.Now().Before(cert.NotBefore) || time.Now().After(cert.NotAfter) {
		return errors.New("per-machine CA is outside its validity period")
	}
	return nil
}

func configureCaptureCertificates(
	sunny *SunnyNet.Sunny,
	certFile string,
	keyFile string,
) error {
	certPEM, err := os.ReadFile(certFile)
	if err != nil {
		return fmt.Errorf("read per-machine CA certificate: %w", err)
	}
	keyPEM, err := os.ReadFile(keyFile)
	if err != nil {
		return fmt.Errorf("read per-machine CA key: %w", err)
	}
	leafCertPEM, leafKeyPEM, err := createCaptureCertificate(certPEM, keyPEM)
	if err != nil {
		return err
	}
	manager := SunnyNet.NewCertManager()
	manager.Certificates = string(leafCertPEM)
	manager.PrivateKey = string(leafKeyPEM)
	for _, host := range certificateHosts {
		if !sunny.AddHttpCertificate(
			host,
			manager,
			SunnyNet.HTTPCertRules_Response,
		) {
			return fmt.Errorf("register capture certificate for %s", host)
		}
	}
	return nil
}

func createCaptureCertificate(
	rootCertPEM []byte,
	rootKeyPEM []byte,
) ([]byte, []byte, error) {
	rootPair, err := tls.X509KeyPair(rootCertPEM, rootKeyPEM)
	if err != nil {
		return nil, nil, fmt.Errorf("load capture CA for leaf certificate: %w", err)
	}
	if len(rootPair.Certificate) == 0 {
		return nil, nil, errors.New("capture CA certificate is empty")
	}
	rootCert, err := x509.ParseCertificate(rootPair.Certificate[0])
	if err != nil {
		return nil, nil, fmt.Errorf("parse capture CA for leaf certificate: %w", err)
	}
	rootKey, ok := rootPair.PrivateKey.(*rsa.PrivateKey)
	if !ok {
		return nil, nil, errors.New("capture CA must use an RSA private key")
	}

	leafKey, err := rsa.GenerateKey(rand.Reader, 2048)
	if err != nil {
		return nil, nil, fmt.Errorf("generate capture leaf key: %w", err)
	}
	serialLimit := new(big.Int).Lsh(big.NewInt(1), 128)
	serial, err := rand.Int(rand.Reader, serialLimit)
	if err != nil {
		return nil, nil, fmt.Errorf("generate capture certificate serial: %w", err)
	}
	now := time.Now()
	notAfter := now.Add(7 * 24 * time.Hour)
	if rootCert.NotAfter.Before(notAfter) {
		notAfter = rootCert.NotAfter
	}
	leafTemplate := &x509.Certificate{
		SerialNumber: serial,
		Subject: pkix.Name{
			CommonName: "Xiaolou WeChat Channels Capture",
		},
		NotBefore:             now.Add(-5 * time.Minute),
		NotAfter:              notAfter,
		KeyUsage:              x509.KeyUsageDigitalSignature | x509.KeyUsageKeyEncipherment,
		ExtKeyUsage:           []x509.ExtKeyUsage{x509.ExtKeyUsageServerAuth},
		BasicConstraintsValid: true,
		DNSNames:              append([]string(nil), certificateHosts...),
	}
	leafDER, err := x509.CreateCertificate(
		rand.Reader,
		leafTemplate,
		rootCert,
		&leafKey.PublicKey,
		rootKey,
	)
	if err != nil {
		return nil, nil, fmt.Errorf("sign capture leaf certificate: %w", err)
	}
	leafCertPEM := pem.EncodeToMemory(&pem.Block{
		Type:  "CERTIFICATE",
		Bytes: leafDER,
	})
	leafKeyPEM := pem.EncodeToMemory(&pem.Block{
		Type:  "RSA PRIVATE KEY",
		Bytes: x509.MarshalPKCS1PrivateKey(leafKey),
	})
	if _, err := tls.X509KeyPair(leafCertPEM, leafKeyPEM); err != nil {
		return nil, nil, fmt.Errorf("validate capture leaf certificate: %w", err)
	}
	return leafCertPEM, leafKeyPEM, nil
}

func currentLoopbackProxy() (string, []string, bool) {
	key, err := registry.OpenKey(
		registry.CURRENT_USER,
		`Software\Microsoft\Windows\CurrentVersion\Internet Settings`,
		registry.QUERY_VALUE,
	)
	if err != nil {
		return "", nil, false
	}
	defer key.Close()

	enabled, _, err := key.GetIntegerValue("ProxyEnable")
	if err != nil || enabled != 1 {
		return "", nil, false
	}
	raw, _, err := key.GetStringValue("ProxyServer")
	if err != nil {
		return "", nil, false
	}
	return parseLoopbackProxy(raw)
}

func parseLoopbackProxy(raw string) (string, []string, bool) {
	candidate := strings.TrimSpace(raw)
	if strings.Contains(candidate, "=") {
		values := make(map[string]string)
		for _, entry := range strings.Split(candidate, ";") {
			name, value, found := strings.Cut(entry, "=")
			if found {
				values[strings.ToLower(strings.TrimSpace(name))] = strings.TrimSpace(value)
			}
		}
		candidate = values["https"]
		if candidate == "" {
			candidate = values["http"]
		}
	}
	if candidate == "" {
		return "", nil, false
	}
	if !strings.Contains(candidate, "://") {
		candidate = "http://" + candidate
	}
	parsed, err := url.Parse(candidate)
	if err != nil || parsed.Scheme != "http" || parsed.User != nil {
		return "", nil, false
	}
	port := parsed.Port()
	if port == "" {
		return "", nil, false
	}
	host := strings.ToLower(parsed.Hostname())
	ip := net.ParseIP(host)
	if host != "localhost" && (ip == nil || !ip.IsLoopback()) {
		return "", nil, false
	}

	targets := []string{host, net.JoinHostPort(host, port)}
	if host == "localhost" {
		targets = append(
			targets,
			"127.0.0.1",
			net.JoinHostPort("127.0.0.1", port),
		)
	}
	return "http://" + net.JoinHostPort(host, port), targets, true
}

func newBridgeClient(token string) *bridgeClient {
	transport := &http.Transport{
		Proxy: nil,
		DialContext: (&net.Dialer{
			Timeout: 2 * time.Second,
		}).DialContext,
		ResponseHeaderTimeout: 3 * time.Second,
	}
	return &bridgeClient{
		token: token,
		client: &http.Client{
			Transport: transport,
			Timeout:   5 * time.Second,
		},
	}
}

func (client *bridgeClient) post(event bridgeEvent) (bridgeResponse, error) {
	payload, err := json.Marshal(event)
	if err != nil {
		return bridgeResponse{}, err
	}
	request, err := http.NewRequest(http.MethodPost, bridgeURL, bytes.NewReader(payload))
	if err != nil {
		return bridgeResponse{}, err
	}
	request.Header.Set("Authorization", "Bearer "+client.token)
	request.Header.Set("Content-Type", "application/json")
	response, err := client.client.Do(request)
	if err != nil {
		return bridgeResponse{}, err
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		_, _ = io.Copy(io.Discard, io.LimitReader(response.Body, 4096))
		return bridgeResponse{}, fmt.Errorf("bridge returned HTTP %d", response.StatusCode)
	}
	var result bridgeResponse
	if err := json.NewDecoder(io.LimitReader(response.Body, 4096)).Decode(&result); err != nil {
		return bridgeResponse{}, err
	}
	return result, nil
}

func newEventSender(
	bridge *bridgeClient,
	upstreamProxy string,
	upstreamTargets []string,
) *eventSender {
	targets := make(map[string]struct{}, len(upstreamTargets))
	for _, target := range upstreamTargets {
		targets[strings.ToLower(strings.TrimSpace(target))] = struct{}{}
	}
	sender := &eventSender{
		bridge:          bridge,
		events:          make(chan bridgeEvent, eventQueueSize),
		done:            make(chan struct{}),
		upstreamProxy:   upstreamProxy,
		upstreamTargets: targets,
	}
	go sender.run()
	return sender
}

func (sender *eventSender) run() {
	defer close(sender.done)
	for event := range sender.events {
		if _, err := sender.bridge.post(event); err != nil {
			sender.failedCount.Add(1)
		}
	}
}

func (sender *eventSender) close() {
	close(sender.events)
	<-sender.done
}

func (sender *eventSender) enqueue(event bridgeEvent) {
	switch event.Type {
	case "request":
		sender.requestCount.Add(1)
	case "response":
		sender.responseCount.Add(1)
	}
	select {
	case sender.events <- event:
	default:
		sender.droppedCount.Add(1)
	}
}

func (sender *eventSender) heartbeat() bridgeEvent {
	lastError := ""
	if value := sender.lastError.Load(); value != nil {
		lastError, _ = value.(string)
	}
	return bridgeEvent{
		Type:          "status",
		State:         "heartbeat",
		TCPCount:      sender.tcpCount.Load(),
		HTTPCount:     sender.httpCount.Load(),
		CaptureErrors: sender.captureErrors.Load(),
		LastError:     lastError,
		RequestCount:  sender.requestCount.Load(),
		ResponseCount: sender.responseCount.Load(),
		TargetScripts: sender.targetScripts.Load(),
		Instrumented:  sender.instrumented.Load(),
		FeedMetadata:  sender.feedMetadata.Load(),
		DroppedCount:  sender.droppedCount.Load(),
		FailedCount:   sender.failedCount.Load(),
	}
}

func (sender *eventSender) handleHTTP(conn SunnyNet.ConnHTTP) {
	rawURL := conn.URL()
	if conn.Type() == public.HttpRequestFail {
		if isLoopbackURL(rawURL) {
			sender.recordCaptureError(
				"capture request retained the loopback proxy target",
			)
		} else if rawURL == "" || isAllowedURL(rawURL) {
			sender.recordCaptureError(conn.Error())
		}
		return
	}
	if conn.Type() == public.HttpSendRequest && isLoopbackURL(rawURL) {
		sender.recordCaptureError(
			"capture request retained the loopback proxy target",
		)
		conn.StopRequest(http.StatusBadGateway, nil)
		return
	}
	if !isAllowedURL(rawURL) {
		return
	}

	switch conn.Type() {
	case public.HttpSendRequest:
		if isCaptureFeedURL(rawURL) {
			body := conn.GetRequestBody()
			if len(body) > 0 && len(body) <= maxResponseBodySize && json.Valid(body) {
				sender.feedMetadata.Add(1)
				sender.enqueue(bridgeEvent{
					Type: "response",
					URL:  rawURL,
					Headers: map[string]string{
						"Content-Type": "application/json",
					},
					Body: body,
				})
			} else {
				sender.recordCaptureError("invalid local feed metadata")
			}
			headers := make(sunnyHTTP.Header)
			headers.Set("Content-Type", "application/json")
			conn.StopRequest(http.StatusOK, []byte("{}"), headers)
			return
		}
		if shouldForceIdentityResponse(rawURL) {
			requestHeader := conn.GetRequestHeader()
			requestHeader.Del("Accept-Encoding")
			requestHeader.Del("If-Modified-Since")
			requestHeader.Del("If-None-Match")
			requestHeader.Set("Cache-Control", "no-cache")
		}
		if sender.upstreamProxy != "" &&
			!conn.SetAgent(sender.upstreamProxy, 60_000) {
			sender.recordCaptureError("configure HTTP upstream proxy failed")
			conn.StopRequest(http.StatusBadGateway, nil)
			return
		}
		sender.httpCount.Add(1)
		if !isMediaURL(rawURL) {
			return
		}
		sender.enqueue(bridgeEvent{
			Type:       "request",
			URL:        rawURL,
			Headers:    safeRequestHeaders(conn.GetRequestHeader()),
			ObservedAt: float64(time.Now().UnixNano()) / 1e9,
		})
	case public.HttpResponseOK:
		headers := responseHeaders(conn.GetResponseHeader())
		targetScript := isTargetFeedScript(rawURL, headers["Content-Type"])
		if targetScript {
			sender.targetScripts.Add(1)
		}
		body := conn.GetResponseBody()
		if len(body) == 0 || len(body) > maxResponseBodySize {
			return
		}
		if modifiedBody, modified := instrumentWechatResponse(
			rawURL,
			headers["Content-Type"],
			body,
		); modified {
			if targetScript &&
				!bytes.Contains(body, []byte(captureFeedPath)) &&
				bytes.Contains(modifiedBody, []byte(captureFeedPath)) {
				sender.instrumented.Add(1)
			}
			responseHeader := conn.GetResponseHeader()
			responseHeader.Del("Content-Encoding")
			responseHeader.Del("Content-Length")
			conn.SetResponseBody(modifiedBody)
			return
		}
		if !looksLikeJSON(headers["Content-Type"], body) {
			return
		}
		sender.enqueue(bridgeEvent{
			Type:    "response",
			URL:     rawURL,
			Headers: headers,
			Body:    body,
		})
	}
}

func isTargetFeedScript(rawURL string, contentType string) bool {
	return isTargetFeedScriptURL(rawURL) &&
		strings.Contains(
			strings.ToLower(contentType),
			"javascript",
		)
}

func isTargetFeedScriptURL(rawURL string) bool {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	return strings.EqualFold(parsed.Hostname(), "res.wx.qq.com") &&
		strings.Contains(parsed.Path, "virtual_svg-icons-register.publish")
}

func shouldForceIdentityResponse(rawURL string) bool {
	if isTargetFeedScriptURL(rawURL) {
		return true
	}
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	return strings.EqualFold(parsed.Hostname(), "channels.weixin.qq.com") &&
		strings.HasPrefix(parsed.Path, "/web/pages/")
}

func isCaptureFeedURL(rawURL string) bool {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	return strings.EqualFold(parsed.Hostname(), "channels.weixin.qq.com") &&
		parsed.Path == captureFeedPath
}

func instrumentWechatResponse(
	rawURL string,
	contentType string,
	body []byte,
) ([]byte, bool) {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return body, false
	}
	host := strings.ToLower(parsed.Hostname())
	loweredContentType := strings.ToLower(contentType)
	source := string(body)

	if host == "channels.weixin.qq.com" &&
		strings.Contains(loweredContentType, "text/html") {
		modified := htmlScriptPattern.ReplaceAllString(
			source,
			`$1="$2?xiaolou_capture=`+captureCacheToken+`"`,
		)
		return []byte(modified), modified != source
	}

	if host != "res.wx.qq.com" ||
		!strings.Contains(loweredContentType, "javascript") {
		return body, false
	}

	modified := targetScriptImportPattern.ReplaceAllString(
		source,
		`$1?xiaolou_capture=`+captureCacheToken,
	)
	if strings.Contains(parsed.Path, "virtual_svg-icons-register") {
		modified = feedFunctionPattern.ReplaceAllString(
			modified,
			feedFunctionReplacement,
		)
	}
	return []byte(modified), modified != source
}

func (sender *eventSender) handleTCP(conn SunnyNet.ConnTCP) {
	if conn.Type() != public.SunnyNetMsgTypeTCPAboutToConnect {
		return
	}
	sender.tcpCount.Add(1)
	if sender.upstreamProxy == "" || sender.isUpstreamTarget(conn.RemoteAddress()) {
		return
	}
	if !conn.SetAgent(sender.upstreamProxy, 60_000) {
		sender.recordCaptureError("configure TCP upstream proxy failed")
	}
}

func (sender *eventSender) isUpstreamTarget(target string) bool {
	_, ok := sender.upstreamTargets[strings.ToLower(strings.TrimSpace(target))]
	return ok
}

func (sender *eventSender) recordCaptureError(raw string) {
	sender.captureErrors.Add(1)
	sender.lastError.Store(sanitizeCaptureError(raw))
}

func sanitizeCaptureError(raw string) string {
	message := strings.Join(strings.Fields(raw), " ")
	if len(message) > 240 {
		message = message[:240]
	}
	if message == "" {
		return "unknown capture error"
	}
	return message
}

func isLoopbackURL(rawURL string) bool {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	host := strings.ToLower(parsed.Hostname())
	if host == "localhost" {
		return true
	}
	ip := net.ParseIP(host)
	return ip != nil && ip.IsLoopback()
}

func isAllowedURL(rawURL string) bool {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	host := strings.ToLower(strings.TrimSuffix(parsed.Hostname(), "."))
	if _, ok := exactHosts[host]; ok {
		return true
	}
	for _, suffix := range allowedSuffixes {
		if strings.HasSuffix(host, suffix) {
			return true
		}
	}
	return false
}

func isMediaURL(rawURL string) bool {
	parsed, err := url.Parse(rawURL)
	if err != nil {
		return false
	}
	path := strings.ToLower(parsed.Path)
	return strings.HasSuffix(path, ".mp4") ||
		strings.HasSuffix(path, ".m3u8") ||
		strings.HasSuffix(path, ".flv") ||
		strings.HasSuffix(path, "/stodownload")
}

func safeRequestHeaders(headers sunnyHTTP.Header) map[string]string {
	result := make(map[string]string)
	for _, name := range []string{
		"User-Agent",
		"Referer",
		"Origin",
		"Range",
		"Accept",
	} {
		if value := headers.Get(name); value != "" {
			result[name] = value
		}
	}
	return result
}

func responseHeaders(headers sunnyHTTP.Header) map[string]string {
	result := make(map[string]string)
	for _, name := range []string{"Content-Type", "Content-Encoding"} {
		if value := headers.Get(name); value != "" {
			result[name] = value
		}
	}
	return result
}

func looksLikeJSON(contentType string, body []byte) bool {
	if strings.Contains(strings.ToLower(contentType), "json") {
		return true
	}
	trimmed := bytes.TrimSpace(body)
	return len(trimmed) > 0 && (trimmed[0] == '{' || trimmed[0] == '[')
}
