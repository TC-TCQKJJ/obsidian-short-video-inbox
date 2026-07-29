//go:build windows

package main

import (
	"bytes"
	"context"
	"crypto/tls"
	"crypto/x509"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/url"
	"os"
	"os/signal"
	"path/filepath"
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
)

var exactHosts = map[string]struct{}{
	"weixin.qq.com":          {},
	"channels.weixin.qq.com": {},
	"finder.video.qq.com":    {},
}

var allowedSuffixes = []string{
	".finder.video.qq.com",
	".wxs.qq.com",
	".wxqcloud.qq.com",
	".wxlivecdn.com",
}

var interceptionRules = strings.Join([]string{
	"weixin.qq.com",
	"weixin.qq.com:*",
	"channels.weixin.qq.com",
	"channels.weixin.qq.com:*",
	"finder.video.qq.com",
	"finder.video.qq.com:*",
	"*.finder.video.qq.com",
	"*.finder.video.qq.com:*",
	"*.wxs.qq.com",
	"*.wxs.qq.com:*",
	"*.wxqcloud.qq.com",
	"*.wxqcloud.qq.com:*",
	"*.wxlivecdn.com",
	"*.wxlivecdn.com:*",
}, ";")

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
	RequestCount  uint64            `json:"request_count,omitempty"`
	ResponseCount uint64            `json:"response_count,omitempty"`
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
	bridge        *bridgeClient
	events        chan bridgeEvent
	done          chan struct{}
	tcpCount      atomic.Uint64
	httpCount     atomic.Uint64
	requestCount  atomic.Uint64
	responseCount atomic.Uint64
	droppedCount  atomic.Uint64
	failedCount   atomic.Uint64
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
	rules := interceptionRules
	if proxyURL, proxyTargets, ok := currentLoopbackProxy(); ok {
		rules += ";" + strings.Join(proxyTargets, ";")
		if !sunny.SetGlobalProxy(proxyURL, 60_000) {
			return errors.New("configure loopback system proxy as SunnyNet upstream")
		}
	}
	if err := sunny.SetMustTcpRegexp(rules, false); err != nil {
		return fmt.Errorf("configure narrow interception rules: %w", err)
	}

	sender := newEventSender(bridge)
	defer sender.close()
	sunny.SetGoCallback(sender.handleHTTP, sender.handleTCP, nil, nil)
	sunny.ProcessCancelAll()
	sunny.ProcessAddName(processName)

	if !sunny.OpenDrive(0) {
		sunny.ProcessCancelAll()
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

	targets := []string{net.JoinHostPort(host, port)}
	if host == "localhost" {
		targets = append(targets, net.JoinHostPort("127.0.0.1", port))
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

func newEventSender(bridge *bridgeClient) *eventSender {
	sender := &eventSender{
		bridge: bridge,
		events: make(chan bridgeEvent, eventQueueSize),
		done:   make(chan struct{}),
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
	return bridgeEvent{
		Type:          "status",
		State:         "heartbeat",
		TCPCount:      sender.tcpCount.Load(),
		HTTPCount:     sender.httpCount.Load(),
		RequestCount:  sender.requestCount.Load(),
		ResponseCount: sender.responseCount.Load(),
		DroppedCount:  sender.droppedCount.Load(),
		FailedCount:   sender.failedCount.Load(),
	}
}

func (sender *eventSender) handleHTTP(conn SunnyNet.ConnHTTP) {
	rawURL := conn.URL()
	if !isAllowedURL(rawURL) {
		return
	}

	switch conn.Type() {
	case public.HttpSendRequest:
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
		body := conn.GetResponseBody()
		if len(body) == 0 || len(body) > maxResponseBodySize {
			return
		}
		headers := responseHeaders(conn.GetResponseHeader())
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

func (sender *eventSender) handleTCP(conn SunnyNet.ConnTCP) {
	if conn.Type() == public.SunnyNetMsgTypeTCPAboutToConnect {
		sender.tcpCount.Add(1)
	}
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
