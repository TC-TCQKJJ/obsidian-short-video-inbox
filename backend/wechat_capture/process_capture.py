from __future__ import annotations

import base64
import ctypes
import hmac
import json
import logging
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from wechat_capture.addon import PassiveWechatCaptureAddon
from wechat_capture.security import CapturePaths


BRIDGE_HOST = "127.0.0.1"
BRIDGE_PORT = 2024
BRIDGE_PATH = "/v1/events"
MAX_EVENT_BYTES = 12 * 1024 * 1024
DRIVER_READY_STATE = "driver_ready"
DRIVER_STOPPED_STATE = "driver_stopped"


class ProcessCaptureEventProcessor:
    def __init__(
        self,
        addon: PassiveWechatCaptureAddon,
        token: str,
        *,
        logger: logging.Logger | None = None,
    ) -> None:
        self.addon = addon
        self.token = token
        self.logger = logger or logging.getLogger(__name__)
        self.driver_ready = threading.Event()
        self.driver_stopped = threading.Event()
        self.stop_requested = threading.Event()
        self._counter_lock = threading.Lock()
        self._tcp_events = 0
        self._http_events = 0
        self._capture_errors = 0
        self._last_capture_error = ""
        self._request_events = 0
        self._response_events = 0
        self._target_scripts = 0
        self._instrumented_scripts = 0
        self._feed_metadata = 0
        self._dropped_events = 0
        self._failed_events = 0
        self._last_logged_metrics = (0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    def authenticate(self, authorization: str | None) -> bool:
        expected = f"Bearer {self.token}"
        return hmac.compare_digest(str(authorization or ""), expected)

    def handle(self, event: object) -> None:
        if not isinstance(event, dict):
            raise ValueError("event must be an object")

        event_type = event.get("type")
        if event_type == "status":
            self._handle_status(event)
            return
        if event_type == "request":
            self._increment_local_counter("request")
            self.addon.record_request_event(
                _required_text(event, "url"),
                observed_at=_optional_timestamp(event.get("observed_at")),
                request_headers=_string_headers(event.get("headers")),
            )
            return
        if event_type == "response":
            self._increment_local_counter("response")
            body_text = _required_text(event, "body")
            try:
                body = base64.b64decode(body_text, validate=True)
            except ValueError as exc:
                raise ValueError("response body is not valid base64") from exc
            self.addon.record_response_event(
                _required_text(event, "url"),
                response_headers=_string_headers(event.get("headers")),
                body=body,
            )
            return
        raise ValueError("unsupported event type")

    def _handle_status(self, event: dict[str, object]) -> None:
        state = _required_text(event, "state")
        if state == DRIVER_READY_STATE:
            self.driver_ready.set()
            self.logger.info("WeChat process capture driver is ready")
        elif state == DRIVER_STOPPED_STATE:
            self.driver_stopped.set()
            self.logger.info("WeChat process capture driver stopped")
        elif state == "heartbeat":
            self._update_helper_metrics(event)
        elif state == "starting":
            self.logger.debug("WeChat process capture state: %s", state)
        else:
            self.logger.warning("WeChat process capture state: %s", state)

    def diagnostic_text(self) -> str:
        with self._counter_lock:
            tcp = self._tcp_events
            http = self._http_events
            capture_errors = self._capture_errors
            requests = self._request_events
            responses = self._response_events
            target_scripts = self._target_scripts
            instrumented_scripts = self._instrumented_scripts
            feed_metadata = self._feed_metadata
            dropped = self._dropped_events
            failed = self._failed_events

        if not self.driver_ready.is_set():
            return "捕获驱动正在启动"
        if (
            tcp == 0
            and http == 0
            and capture_errors == 0
            and requests == 0
            and responses == 0
        ):
            return "驱动已连接，尚未检测到视频号流量"
        if dropped or failed:
            return "已检测到视频号流量，部分事件异常，请查看日志"
        if capture_errors and requests == 0 and responses == 0:
            return f"视频号 TLS/HTTP 失败 {capture_errors} 次，请查看日志"
        if http == 0 and requests == 0 and responses == 0:
            return f"已接管微信流量：原始连接 {tcp}，尚未解密视频号 HTTP"
        if requests == 0 and responses == 0:
            if target_scripts and not instrumented_scripts:
                return "已收到微信目标脚本，但未能改写，当前版本可能不兼容"
            if instrumented_scripts and not feed_metadata:
                return "微信目标脚本已改写，等待页面回传视频信息"
            return f"已解密 {http} 个视频号 HTTP 请求，等待媒体链接"
        if responses == 0:
            if target_scripts == 0:
                return f"已检测到 {requests} 个视频请求，尚未收到微信目标脚本"
            if instrumented_scripts == 0:
                return "已检测到视频请求，但微信目标脚本未能改写"
            if feed_metadata == 0:
                return "微信目标脚本已改写，等待页面回传视频信息"
            return f"已检测到 {requests} 个视频请求，等待视频信息"
        return f"已检测到视频号数据：请求 {requests}，信息 {responses}"

    def _increment_local_counter(self, event_type: str) -> None:
        with self._counter_lock:
            if event_type == "request":
                self._request_events += 1
            else:
                self._response_events += 1

    def _update_helper_metrics(self, event: dict[str, object]) -> None:
        metrics = (
            _optional_count(event.get("tcp_count")),
            _optional_count(event.get("http_count")),
            _optional_count(event.get("capture_error_count")),
            _optional_count(event.get("request_count")),
            _optional_count(event.get("response_count")),
            _optional_count(event.get("target_script_count")),
            _optional_count(event.get("instrumented_script_count")),
            _optional_count(event.get("feed_metadata_count")),
            _optional_count(event.get("dropped_count")),
            _optional_count(event.get("failed_count")),
        )
        with self._counter_lock:
            self._tcp_events = max(self._tcp_events, metrics[0])
            self._http_events = max(self._http_events, metrics[1])
            self._capture_errors = max(self._capture_errors, metrics[2])
            self._request_events = max(self._request_events, metrics[3])
            self._response_events = max(self._response_events, metrics[4])
            self._target_scripts = max(self._target_scripts, metrics[5])
            self._instrumented_scripts = max(
                self._instrumented_scripts,
                metrics[6],
            )
            self._feed_metadata = max(self._feed_metadata, metrics[7])
            self._dropped_events = max(self._dropped_events, metrics[8])
            self._failed_events = max(self._failed_events, metrics[9])
            last_capture_error = event.get("last_capture_error")
            if isinstance(last_capture_error, str) and last_capture_error:
                self._last_capture_error = last_capture_error
            current = (
                self._tcp_events,
                self._http_events,
                self._capture_errors,
                self._request_events,
                self._response_events,
                self._dropped_events,
                self._failed_events,
            )
            changed = current != self._last_logged_metrics
            if changed:
                self._last_logged_metrics = current
        if changed:
            self.logger.info(
                "WeChat process capture counters: tcp=%d http=%d capture_errors=%d "
                "requests=%d responses=%d target_scripts=%d instrumented=%d "
                "feed_metadata=%d dropped=%d failed=%d",
                *current,
            )
            if self._last_capture_error:
                self.logger.warning(
                    "WeChat process capture last error: %s",
                    self._last_capture_error,
                )


class _BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, processor: ProcessCaptureEventProcessor):
        self.processor = processor
        super().__init__(address, _BridgeHandler)


class _BridgeHandler(BaseHTTPRequestHandler):
    server: _BridgeServer

    def do_POST(self) -> None:
        if self.path != BRIDGE_PATH:
            self.send_error(404)
            return
        if not self.server.processor.authenticate(self.headers.get("Authorization")):
            self.send_error(401)
            return

        try:
            content_length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            self.send_error(400)
            return
        if content_length < 1 or content_length > MAX_EVENT_BYTES:
            self.send_error(413)
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
            self.server.processor.handle(payload)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            self.send_error(400)
            return

        body = json.dumps(
            {"stop": self.server.processor.stop_requested.is_set()},
            separators=(",", ":"),
        ).encode("ascii")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, _format: str, *_args) -> None:
        return


class ProcessCaptureBridgeServer:
    def __init__(
        self,
        processor: ProcessCaptureEventProcessor,
        *,
        host: str = BRIDGE_HOST,
        port: int = BRIDGE_PORT,
    ) -> None:
        self.processor = processor
        self.host = host
        self.port = port
        self._server: _BridgeServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Process capture bridge can only be started once")
        self._server = _BridgeServer((self.host, self.port), self.processor)
        self.port = int(self._server.server_address[1])
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="wechat-process-capture-bridge",
            daemon=True,
        )
        self._thread.start()

    def request_stop(self) -> None:
        self.processor.stop_requested.set()

    def close(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()


class ProcessCaptureLauncher:
    def __init__(
        self,
        paths: CapturePaths,
        *,
        launch_script: Path | None = None,
    ) -> None:
        self.paths = paths
        self.launch_script = launch_script or Path(__file__).with_name(
            "launch-process-capture.ps1"
        )

    def launch(self) -> int:
        if not self.paths.process_helper_path.exists():
            raise FileNotFoundError(self.paths.process_helper_path)
        result = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(self.launch_script),
                "-Executable",
                str(self.paths.process_helper_path),
            ],
            check=True,
            capture_output=True,
            text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        output = result.stdout.strip().splitlines()
        if not output or not output[-1].isdigit():
            raise RuntimeError("Elevated process capture did not return a PID")
        return int(output[-1])


class ProcessCaptureWorker:
    def __init__(
        self,
        *,
        paths: CapturePaths,
        token: str,
        matcher,
        logger: logging.Logger | None = None,
        launcher: ProcessCaptureLauncher | None = None,
    ) -> None:
        self.paths = paths
        self.logger = logger or logging.getLogger(__name__)
        self.processor = ProcessCaptureEventProcessor(
            PassiveWechatCaptureAddon(matcher, logger=self.logger),
            token,
            logger=self.logger,
        )
        self.bridge = ProcessCaptureBridgeServer(self.processor)
        self.launcher = launcher or ProcessCaptureLauncher(paths)
        self.pid: int | None = None
        self._error: BaseException | None = None

    def start(self) -> None:
        self.bridge.start()
        try:
            self.pid = self.launcher.launch()
        except BaseException as exc:
            self._error = exc
            self.bridge.close()
            raise

    def wait_ready(self, timeout: float) -> None:
        if self._error is not None:
            raise RuntimeError("Process capture failed to start") from self._error
        if not self.processor.driver_ready.wait(timeout=timeout):
            raise TimeoutError("Process capture driver did not become ready")

    def stop(self) -> None:
        self.bridge.request_stop()

    def join(self, timeout: float | None = None) -> None:
        timeout = 5.0 if timeout is None else timeout
        self.processor.driver_stopped.wait(timeout=timeout)
        self.bridge.close()

    def is_alive(self) -> bool:
        return self.bridge.is_alive() and (
            self.pid is None or _windows_process_exists(self.pid)
        )


def _windows_process_exists(pid: int) -> bool:
    if not hasattr(ctypes, "windll"):
        return True
    process_query_limited_information = 0x1000
    handle = ctypes.windll.kernel32.OpenProcess(
        process_query_limited_information,
        False,
        pid,
    )
    if not handle:
        return False
    ctypes.windll.kernel32.CloseHandle(handle)
    return True


def _required_text(event: dict[str, object], name: str) -> str:
    value = event.get(name)
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _optional_timestamp(value: object) -> float:
    if isinstance(value, (int, float)) and float(value) > 0:
        return float(value)
    return time.time()


def _optional_count(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _string_headers(value: object) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("headers must be an object")
    result: dict[str, str] = {}
    for name, header_value in value.items():
        if not isinstance(name, str) or not isinstance(header_value, str):
            raise ValueError("headers must contain only strings")
        result[name] = header_value
    return result
