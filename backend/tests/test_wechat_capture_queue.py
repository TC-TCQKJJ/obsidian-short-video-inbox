import ctypes
import importlib.util
import io
import json
import logging
import queue
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, call, patch
from urllib.error import URLError

from wechat_capture.matcher import CaptureMatcher
from wechat_capture.model import CaptureCandidate
from wechat_capture.security import CapturePaths

try:
    import wechat_capture.client as client_module
    import wechat_capture.host as host_module
    import wechat_capture.pending_queue as pending_queue_module
    import wechat_capture.ui as ui_module
except (ImportError, ModuleNotFoundError):
    client_module = None
    host_module = None
    pending_queue_module = None
    ui_module = None

CaptureBackendClient = getattr(client_module, "CaptureBackendClient", None)
MitmProxyWorker = getattr(host_module, "MitmProxyWorker", None)
WindowsHotkeyManager = getattr(host_module, "WindowsHotkeyManager", None)
WechatCaptureHost = getattr(host_module, "WechatCaptureHost", None)
notify_hotkey_fallback = getattr(host_module, "_notify_hotkey_fallback", None)
HOTKEY_MODIFIERS = getattr(host_module, "HOTKEY_MODIFIERS", None)
HOTKEY_FALLBACK_MODIFIERS = getattr(
    host_module,
    "HOTKEY_FALLBACK_MODIFIERS",
    None,
)
HOTKEY_VK = getattr(host_module, "HOTKEY_VK", None)
PendingCaptureQueue = getattr(pending_queue_module, "PendingCaptureQueue", None)
CaptureCoordinator = getattr(ui_module, "CaptureCoordinator", None)
NativeCaptureControlWindow = getattr(
    ui_module,
    "NativeCaptureControlWindow",
    None,
)
load_cover_image = getattr(ui_module, "load_cover_image", None)
APP_TITLE = getattr(ui_module, "APP_TITLE", None)
AUTHOR_LABEL = getattr(ui_module, "AUTHOR_LABEL", None)
CANCEL_BUTTON_TEXT = getattr(ui_module, "CANCEL_BUTTON_TEXT", None)
CAPTURE_TIME_LABEL = getattr(ui_module, "CAPTURE_TIME_LABEL", None)
CONFIRM_BUTTON_TEXT = getattr(ui_module, "CONFIRM_BUTTON_TEXT", None)
DURATION_LABEL = getattr(ui_module, "DURATION_LABEL", None)
NO_CANDIDATE_MESSAGE = getattr(ui_module, "NO_CANDIDATE_MESSAGE", None)
SELECTION_WINDOW_TITLE = getattr(ui_module, "SELECTION_WINDOW_TITLE", None)
CONTROL_BUTTON_TEXT = getattr(ui_module, "CONTROL_BUTTON_TEXT", None)
CONTROL_CHECKING_TEXT = getattr(ui_module, "CONTROL_CHECKING_TEXT", None)
CONTROL_EMPTY_TEXT = getattr(ui_module, "CONTROL_EMPTY_TEXT", None)
CONTROL_ERROR_TEXT = getattr(ui_module, "CONTROL_ERROR_TEXT", None)
CONTROL_ERROR_MESSAGE = getattr(ui_module, "CONTROL_ERROR_MESSAGE", None)


def make_candidate(index: int, *, capture_id: str | None = None) -> CaptureCandidate:
    value = capture_id or f"capture-{index}"
    return CaptureCandidate(
        capture_id=value,
        title=f"title-{index}",
        author=f"author-{index}",
        source_url=f"https://channels.weixin.qq.com/web/pages/feed?id={value}",
        media_url=f"https://wxsmw.wxs.qq.com/video/{value}.mp4?token=secret-{index}",
        cover_url=f"https://wxsmw.wxs.qq.com/cover/{value}.jpg",
        duration_ms=1000 * index,
        decrypt_key=index,
        request_headers={"Range": f"bytes={index}-"},
        observed_at=time.time(),
    )


class FakeTkRoot:
    def __init__(self) -> None:
        self.after_calls: list[tuple[int, object]] = []
        self.protocol_calls: list[tuple[str, object]] = []
        self.destroyed = False

    def after(self, delay_ms: int, callback) -> None:
        self.after_calls.append((delay_ms, callback))

    def protocol(self, name: str, callback) -> None:
        self.protocol_calls.append((name, callback))

    def destroy(self) -> None:
        self.destroyed = True


class FakeHotkeyManager:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.register_calls: list[tuple[int, int]] = []
        self.unregister_calls: list[int] = []
        self.triggered = False
        self.error = error

    def register(self, modifiers: int, vk: int) -> int:
        self.register_calls.append((modifiers, vk))
        if self.error is not None:
            raise self.error
        return 99

    def unregister(self, hotkey_id: int) -> None:
        self.unregister_calls.append(hotkey_id)

    def pop_triggered(self, hotkey_id: int) -> bool:
        if self.triggered:
            self.triggered = False
            return hotkey_id == 99
        return False


class FakeUser32:
    WM_QUIT = 0x0012

    def __init__(self) -> None:
        self.messages = queue.Queue()
        self.register_thread_ids: list[int] = []
        self.unregister_thread_ids: list[int] = []
        self.post_thread_calls: list[tuple[int, int, int, int]] = []

    def RegisterHotKey(self, hwnd, hotkey_id: int, modifiers: int, vk: int) -> int:
        self.register_thread_ids.append(threading.get_ident())
        return 1

    def UnregisterHotKey(self, hwnd, hotkey_id: int) -> int:
        self.unregister_thread_ids.append(threading.get_ident())
        return 1

    def GetMessageW(self, message_pointer, hwnd, minimum: int, maximum: int) -> int:
        message, hotkey_id = self.messages.get(timeout=1.0)
        if message == self.WM_QUIT:
            return 0
        self._write_message(message_pointer, message, hotkey_id)
        return 1

    def PeekMessageW(
        self,
        message_pointer,
        hwnd,
        minimum: int,
        maximum: int,
        remove: int,
    ) -> int:
        try:
            message, hotkey_id = self.messages.get_nowait()
        except queue.Empty:
            return 0
        self._write_message(message_pointer, message, hotkey_id)
        return 1

    def PostThreadMessageW(
        self,
        thread_id: int,
        message: int,
        wparam: int,
        lparam: int,
    ) -> int:
        self.post_thread_calls.append((thread_id, message, wparam, lparam))
        self.messages.put((message, wparam))
        return 1

    def send_hotkey(self, hotkey_id: int) -> None:
        self.messages.put((WindowsHotkeyManager.WM_HOTKEY, hotkey_id))

    @staticmethod
    def _write_message(message_pointer, message: int, hotkey_id: int) -> None:
        native_message = ctypes.cast(
            message_pointer,
            ctypes.POINTER(host_module._Msg),
        ).contents
        native_message.message = message
        native_message.wParam = hotkey_id


class FakeProxyWorker:
    def __init__(self) -> None:
        self.start_calls = 0
        self.stop_calls = 0
        self.join_calls: list[float] = []

    def start(self) -> None:
        self.start_calls += 1

    def wait_ready(self, timeout: float) -> None:
        return None

    def stop(self) -> None:
        self.stop_calls += 1

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)


class PendingQueueTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(PendingCaptureQueue)

    def test_payload_is_not_plaintext_on_disk_and_round_trips_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.dat"
            queue = PendingCaptureQueue(path)
            candidate = make_candidate(1)

            queue.enqueue(candidate)

            raw = path.read_bytes()
            self.assertNotIn(candidate.media_url.encode("utf-8"), raw)
            self.assertNotIn(b"token=secret-1", raw)

            reloaded = PendingCaptureQueue(path)
            self.assertEqual(reloaded.items(), [candidate])

    def test_queue_write_uses_atomic_replace(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.dat"
            queue = PendingCaptureQueue(path)

            with patch("pathlib.Path.replace", autospec=True, wraps=Path.replace) as replace:
                queue.enqueue(make_candidate(1))

            self.assertTrue(replace.called)
            source_path, target_path = replace.call_args.args
            self.assertNotEqual(source_path, target_path)
            self.assertEqual(target_path, path)

    def test_queue_enforces_max_20_and_deduplicates_capture_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PendingCaptureQueue(Path(tmp) / "pending.dat")
            for index in range(25):
                queue.enqueue(make_candidate(index))
            queue.enqueue(make_candidate(999, capture_id="capture-24"))

            items = queue.items()

            self.assertEqual(len(items), 20)
            self.assertEqual(items[0].capture_id, "capture-5")
            self.assertEqual(items[-1].capture_id, "capture-24")
            self.assertEqual(items[-1].title, "title-999")

    def test_flush_removes_only_successful_submissions(self):
        with tempfile.TemporaryDirectory() as tmp:
            queue = PendingCaptureQueue(Path(tmp) / "pending.dat")
            first = make_candidate(1)
            second = make_candidate(2)
            queue.enqueue(first)
            queue.enqueue(second)

            submitted: list[str] = []

            def submit(candidate: CaptureCandidate) -> bool:
                submitted.append(candidate.capture_id)
                return candidate.capture_id == first.capture_id

            queue.flush(submit)

            self.assertEqual(submitted, [first.capture_id, second.capture_id])
            self.assertEqual(queue.items(), [second])

    def test_corrupt_queue_is_quarantined_and_subsequent_enqueue_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.dat"
            path.write_bytes(b"not-dpapi-ciphertext")
            queue = PendingCaptureQueue(
                path,
                protect=lambda payload: payload,
                unprotect=lambda payload: (
                    (_ for _ in ()).throw(OSError("dpapi fail"))
                    if payload == b"not-dpapi-ciphertext"
                    else payload
                ),
            )

            self.assertEqual(queue.items(), [])
            self.assertFalse(path.exists())
            self.assertEqual(len(list(Path(tmp).glob("pending.dat.corrupt-*"))), 1)

            candidate = make_candidate(7)
            queue.enqueue(candidate)

            self.assertEqual(queue.items(), [candidate])

    def test_corrupt_queue_flush_treats_queue_as_empty(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "pending.dat"
            path.write_bytes(b"broken-json")
            queue = PendingCaptureQueue(
                path,
                protect=lambda payload: payload,
                unprotect=lambda payload: b"{not-json" if payload == b"broken-json" else payload,
            )

            submitted = []
            queue.flush(lambda item: submitted.append(item.capture_id) or True)

            self.assertEqual(submitted, [])
            self.assertEqual(queue.items(), [])
            self.assertEqual(len(list(Path(tmp).glob("pending.dat.corrupt-*"))), 1)


class BackendClientTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(CaptureBackendClient)

    def test_submit_posts_private_payload_with_auth_header_and_timeout(self):
        candidate = make_candidate(3)
        opener = Mock(return_value=SimpleNamespace(status=202))
        client = CaptureBackendClient(token="local-token", opener=opener)

        result = client.submit(candidate)

        self.assertTrue(result)
        request = opener.call_args.args[0]
        timeout = opener.call_args.kwargs["timeout"]
        self.assertEqual(request.full_url, "http://127.0.0.1:5050/api/wechat-capture/jobs")
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.headers["X-Xiaolou-Capture-Token"], "local-token")
        self.assertEqual(request.headers["Content-Type"], "application/json")
        self.assertEqual(timeout, 5.0)
        self.assertEqual(json.loads(request.data.decode("utf-8")), candidate.to_private_dict())

    def test_submit_connection_failure_returns_false_without_logging_body(self):
        candidate = make_candidate(4)
        opener = Mock(side_effect=URLError("offline"))
        logger = logging.getLogger(f"{__name__}.{self.id()}")
        client = CaptureBackendClient(token="local-token", opener=opener, logger=logger)

        with self.assertLogs(logger, level="WARNING") as captured:
            result = client.submit(candidate)

        self.assertFalse(result)
        self.assertEqual(len(captured.output), 1)
        self.assertNotIn(candidate.media_url, captured.output[0])
        self.assertNotIn("token=secret-4", captured.output[0])


class UiCoordinatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(CaptureCoordinator)
        self.matcher = CaptureMatcher(max_age_seconds=30)

    def test_no_candidate_does_not_open_ui_or_submit(self):
        client = Mock()
        queue = Mock()
        presenter = Mock()
        coordinator = CaptureCoordinator(self.matcher, presenter, client, queue)

        result = coordinator.handle_hotkey()

        self.assertFalse(result)
        presenter.choose_candidate.assert_not_called()
        client.submit.assert_not_called()
        queue.enqueue.assert_not_called()
        presenter.show_no_candidate_message.assert_called_once_with(NO_CANDIDATE_MESSAGE)

    def test_cancelled_confirmation_does_not_submit(self):
        candidate = make_candidate(5)
        self.matcher.record_feed(candidate)
        self.matcher.record_media_request(
            candidate.media_url,
            observed_at=candidate.observed_at,
            request_headers=candidate.request_headers,
        )
        client = Mock()
        queue = Mock()
        presenter = Mock()
        presenter.choose_candidate.return_value = None
        coordinator = CaptureCoordinator(self.matcher, presenter, client, queue)

        result = coordinator.handle_hotkey()

        self.assertFalse(result)
        presenter.choose_candidate.assert_called_once()
        client.submit.assert_not_called()
        queue.enqueue.assert_not_called()

    def test_ui_text_constants_match_expected_utf8_strings(self):
        self.assertEqual(NO_CANDIDATE_MESSAGE, "请播放目标视频几秒后重试")
        self.assertEqual(APP_TITLE, "微信视频号捕获")
        self.assertEqual(SELECTION_WINDOW_TITLE, "选择要保存的视频")
        self.assertEqual(CONFIRM_BUTTON_TEXT, "确认保存")
        self.assertEqual(CANCEL_BUTTON_TEXT, "取消")
        self.assertEqual(AUTHOR_LABEL, "作者")
        self.assertEqual(DURATION_LABEL, "时长")
        self.assertEqual(CAPTURE_TIME_LABEL, "捕获时间")

    def test_cover_download_rejects_local_address_and_non_http_scheme(self):
        opener = Mock()

        self.assertIsNone(load_cover_image("http://127.0.0.1/cover.png", opener=opener))
        self.assertIsNone(load_cover_image("ftp://wxsmw.wxs.qq.com/cover.png", opener=opener))

        opener.assert_not_called()

    def test_cover_download_rejects_oversized_response_body(self):
        opener = Mock(return_value=SimpleNamespace(read=lambda amount=None: b"x" * (5 * 1024 * 1024 + 1)))

        image = load_cover_image("https://wxsmw.wxs.qq.com/cover/demo.jpg", opener=opener)

        self.assertIsNone(image)
        self.assertEqual(opener.call_args.kwargs["timeout"], 3.0)

    @unittest.skipUnless(
        importlib.util.find_spec("PIL") is not None,
        "Pillow only available in isolated host environment",
    )
    def test_cover_download_uses_three_second_timeout(self):
        from PIL import Image

        self.assertIsNotNone(load_cover_image)
        response = io.BytesIO()
        Image.new("RGB", (1, 1), color=(255, 0, 0)).save(response, format="PNG")
        response.seek(0)
        opener = Mock(return_value=response)

        image = load_cover_image("https://wxsmw.wxs.qq.com/cover/demo.jpg", opener=opener)

        self.assertEqual(opener.call_args.kwargs["timeout"], 3.0)
        self.assertEqual(image.size, (1, 1))


class ControlWindowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(NativeCaptureControlWindow)

    def test_visible_button_checks_current_video_and_updates_status(self):
        root = Mock()
        on_capture = Mock(return_value=False)
        frame = Mock()
        service_label = Mock()
        status_label = Mock()
        button = Mock()

        with (
            patch("tkinter.Frame", return_value=frame),
            patch(
                "tkinter.Label",
                side_effect=[service_label, status_label],
            ),
            patch("tkinter.Button", return_value=button) as button_constructor,
        ):
            window = NativeCaptureControlWindow(root, on_capture)
            window.show()
            button_constructor.call_args.kwargs["command"]()

        root.title.assert_called_once_with(APP_TITLE)
        root.resizable.assert_called_once_with(False, False)
        root.geometry.assert_called_once_with("400x170")
        root.deiconify.assert_called_once_with()
        self.assertEqual(
            button_constructor.call_args.kwargs["text"],
            CONTROL_BUTTON_TEXT,
        )
        on_capture.assert_called_once_with()
        self.assertEqual(
            status_label.configure.call_args_list,
            [
                call(text=CONTROL_CHECKING_TEXT),
                call(text=CONTROL_EMPTY_TEXT),
            ],
        )

    def test_callback_failure_restores_error_status_and_shows_message(self):
        root = Mock()
        on_capture = Mock(side_effect=RuntimeError("capture failed"))
        status_label = Mock()

        with (
            patch("tkinter.Frame", return_value=Mock()),
            patch(
                "tkinter.Label",
                side_effect=[Mock(), status_label],
            ),
            patch("tkinter.Button", return_value=Mock()),
            patch("tkinter.messagebox.showerror") as showerror,
        ):
            window = NativeCaptureControlWindow(root, on_capture)
            window.show()
            try:
                result = window.check_current_video()
            except RuntimeError as exc:
                self.fail(f"control window leaked callback error: {exc}")

        self.assertFalse(result)
        self.assertEqual(
            status_label.configure.call_args_list,
            [
                call(text=CONTROL_CHECKING_TEXT),
                call(text=CONTROL_ERROR_TEXT),
            ],
        )
        showerror.assert_called_once_with(
            APP_TITLE,
            CONTROL_ERROR_MESSAGE,
            parent=root,
        )


class HostLifecycleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(WechatCaptureHost)

    def test_host_registers_ctrl_alt_s_polls_tk_and_cleans_up_on_shutdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = FakeTkRoot()
            hotkeys = FakeHotkeyManager()
            proxy_worker = FakeProxyWorker()
            coordinator = Mock()
            coordinator.handle_hotkey.return_value = True
            paths = CapturePaths(Path(tmp))

            with patch("wechat_capture.host.build_proxy_options", return_value=object()) as build_proxy_options:
                host = WechatCaptureHost(
                    root=root,
                    matcher=CaptureMatcher(max_age_seconds=30),
                    paths=paths,
                    token="local-token",
                    coordinator=coordinator,
                    hotkeys=hotkeys,
                    proxy_worker=proxy_worker,
                )

                host.start()
                hotkeys.triggered = True
                host.poll_hotkey()
                host.stop()

            build_proxy_options.assert_not_called()
            self.assertEqual(hotkeys.register_calls, [(HOTKEY_MODIFIERS, HOTKEY_VK)])
            self.assertEqual(root.after_calls[0][0], 50)
            coordinator.handle_hotkey.assert_called_once()
            self.assertEqual(hotkeys.unregister_calls, [99])
            self.assertEqual(proxy_worker.start_calls, 1)
            self.assertEqual(proxy_worker.stop_calls, 1)
            self.assertEqual(proxy_worker.join_calls, [5.0])
            self.assertTrue(root.destroyed)

    def test_hotkey_registration_failure_rolls_back_proxy_without_polling(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = FakeTkRoot()
            hotkeys = FakeHotkeyManager(error=OSError("register failed"))
            proxy_worker = FakeProxyWorker()
            paths = CapturePaths(Path(tmp))

            with patch("wechat_capture.host.build_proxy_options", return_value=object()):
                host = WechatCaptureHost(
                    root=root,
                    matcher=CaptureMatcher(max_age_seconds=30),
                    paths=paths,
                    token="local-token",
                    hotkeys=hotkeys,
                    proxy_worker=proxy_worker,
                )

                with self.assertRaises(OSError):
                    host.start()

            self.assertEqual(proxy_worker.start_calls, 1)
            self.assertEqual(proxy_worker.stop_calls, 1)
            self.assertEqual(proxy_worker.join_calls, [5.0])
            self.assertEqual(root.after_calls, [])

    def test_proxy_startup_failure_rolls_back_before_hotkey_registration(self):
        class FailedProxyWorker(FakeProxyWorker):
            def wait_ready(self, timeout: float) -> None:
                raise RuntimeError("proxy bind failed")

        with tempfile.TemporaryDirectory() as tmp:
            root = FakeTkRoot()
            hotkeys = FakeHotkeyManager()
            proxy_worker = FailedProxyWorker()
            with patch("wechat_capture.host.build_proxy_options", return_value=object()):
                host = WechatCaptureHost(
                    root=root,
                    matcher=CaptureMatcher(max_age_seconds=30),
                    paths=CapturePaths(Path(tmp)),
                    token="local-token",
                    hotkeys=hotkeys,
                    proxy_worker=proxy_worker,
                )
                with self.assertRaisesRegex(RuntimeError, "proxy bind failed"):
                    host.start()

            self.assertEqual(hotkeys.register_calls, [])
            self.assertEqual(proxy_worker.stop_calls, 1)
            self.assertEqual(proxy_worker.join_calls, [5.0])

    def test_busy_primary_hotkey_uses_ctrl_alt_shift_s_fallback(self):
        class BusyPrimaryHotkeys(FakeHotkeyManager):
            def register(self, modifiers: int, vk: int) -> int:
                self.register_calls.append((modifiers, vk))
                if modifiers == HOTKEY_MODIFIERS:
                    error = OSError("busy")
                    error.winerror = 1409
                    raise error
                return 100

        with tempfile.TemporaryDirectory() as tmp:
            root = FakeTkRoot()
            hotkeys = BusyPrimaryHotkeys()
            proxy_worker = FakeProxyWorker()
            notices = []
            with patch("wechat_capture.host.build_proxy_options", return_value=object()):
                host = WechatCaptureHost(
                    root=root,
                    matcher=CaptureMatcher(max_age_seconds=30),
                    paths=CapturePaths(Path(tmp)),
                    token="local-token",
                    coordinator=Mock(),
                    hotkeys=hotkeys,
                    proxy_worker=proxy_worker,
                    hotkey_fallback_notifier=notices.append,
                )
                host.start()
                host.stop()

            self.assertEqual(
                hotkeys.register_calls,
                [
                    (HOTKEY_MODIFIERS, HOTKEY_VK),
                    (HOTKEY_FALLBACK_MODIFIERS, HOTKEY_VK),
                ],
            )
            self.assertEqual(hotkeys.unregister_calls, [100])
            self.assertEqual(notices, ["Ctrl+Alt+Shift+S"])

    def test_busy_primary_and_fallback_keeps_control_window_available(self):
        class AllBusyHotkeys(FakeHotkeyManager):
            def register(self, modifiers: int, vk: int) -> int:
                self.register_calls.append((modifiers, vk))
                error = OSError("busy")
                error.winerror = 1409
                raise error

        with tempfile.TemporaryDirectory() as tmp:
            root = FakeTkRoot()
            hotkeys = AllBusyHotkeys()
            proxy_worker = FakeProxyWorker()
            with patch("wechat_capture.host.build_proxy_options", return_value=object()):
                host = WechatCaptureHost(
                    root=root,
                    matcher=CaptureMatcher(max_age_seconds=30),
                    paths=CapturePaths(Path(tmp)),
                    token="local-token",
                    coordinator=Mock(),
                    hotkeys=hotkeys,
                    proxy_worker=proxy_worker,
                )
                with self.assertLogs("wechat_capture", level="WARNING") as captured:
                    try:
                        host.start()
                    except OSError as exc:
                        self.fail(f"hotkey conflict aborted control window: {exc}")
                host.stop()

            self.assertEqual(
                hotkeys.register_calls,
                [
                    (HOTKEY_MODIFIERS, HOTKEY_VK),
                    (HOTKEY_FALLBACK_MODIFIERS, HOTKEY_VK),
                ],
            )
            self.assertEqual(hotkeys.unregister_calls, [])
            self.assertIn("use the control window", captured.output[0])
            self.assertEqual(root.after_calls[0][0], 50)
            self.assertEqual(proxy_worker.stop_calls, 1)
            self.assertTrue(root.destroyed)

    def test_host_rejects_duplicate_start_and_stop_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = FakeTkRoot()
            hotkeys = FakeHotkeyManager()
            proxy_worker = FakeProxyWorker()
            coordinator = Mock()
            paths = CapturePaths(Path(tmp))

            with patch("wechat_capture.host.build_proxy_options", return_value=object()):
                host = WechatCaptureHost(
                    root=root,
                    matcher=CaptureMatcher(max_age_seconds=30),
                    paths=paths,
                    token="local-token",
                    coordinator=coordinator,
                    hotkeys=hotkeys,
                    proxy_worker=proxy_worker,
                )

                host.start()
                with self.assertRaises(RuntimeError):
                    host.start()
                host.stop()
                host.stop()
                with self.assertRaises(RuntimeError):
                    host.start()

            self.assertEqual(hotkeys.register_calls, [(HOTKEY_MODIFIERS, HOTKEY_VK)])
            self.assertEqual(hotkeys.unregister_calls, [99])
            self.assertEqual(proxy_worker.start_calls, 1)
            self.assertEqual(proxy_worker.stop_calls, 1)
            self.assertEqual(proxy_worker.join_calls, [5.0])

    def test_hotkey_unregister_failure_still_stops_proxy_and_destroys_window(self):
        class FailedUnregisterHotkeys(FakeHotkeyManager):
            def unregister(self, hotkey_id: int) -> None:
                self.unregister_calls.append(hotkey_id)
                raise RuntimeError("unregister failed")

        with tempfile.TemporaryDirectory() as tmp:
            root = FakeTkRoot()
            hotkeys = FailedUnregisterHotkeys()
            proxy_worker = FakeProxyWorker()
            with patch("wechat_capture.host.build_proxy_options", return_value=object()):
                host = WechatCaptureHost(
                    root=root,
                    matcher=CaptureMatcher(max_age_seconds=30),
                    paths=CapturePaths(Path(tmp)),
                    token="local-token",
                    coordinator=Mock(),
                    hotkeys=hotkeys,
                    proxy_worker=proxy_worker,
                )
                host.start()
                with self.assertRaisesRegex(RuntimeError, "unregister failed"):
                    host.stop()

            self.assertEqual(hotkeys.unregister_calls, [99])
            self.assertEqual(proxy_worker.stop_calls, 1)
            self.assertEqual(proxy_worker.join_calls, [5.0])
            self.assertTrue(root.destroyed)


class WindowsHotkeyManagerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(WindowsHotkeyManager)

    def test_listener_thread_owns_hotkey_lifecycle(self):
        fake_user32 = FakeUser32()
        manager = WindowsHotkeyManager()
        manager._user32 = fake_user32

        hotkey_id = manager.register(HOTKEY_MODIFIERS, HOTKEY_VK)
        try:
            self.assertEqual(len(fake_user32.register_thread_ids), 1)
            self.assertNotEqual(
                fake_user32.register_thread_ids[0],
                threading.get_ident(),
            )

            fake_user32.send_hotkey(hotkey_id)
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if manager.pop_triggered(hotkey_id):
                    break
                time.sleep(0.01)
            else:
                self.fail("listener did not deliver WM_HOTKEY")
        finally:
            manager.unregister(hotkey_id)

        self.assertEqual(
            fake_user32.unregister_thread_ids,
            fake_user32.register_thread_ids,
        )


class HotkeyFallbackNotifierTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(notify_hotkey_fallback)

    def test_fallback_notification_is_non_modal(self):
        window = Mock()
        label = Mock()
        button = Mock()

        with (
            patch("tkinter.Toplevel", return_value=window) as toplevel,
            patch("tkinter.Label", return_value=label),
            patch("tkinter.Button", return_value=button),
            patch("tkinter.messagebox.showwarning") as modal_warning,
        ):
            notify_hotkey_fallback("Ctrl+Alt+Shift+S")

        modal_warning.assert_not_called()
        toplevel.assert_called_once_with()
        window.attributes.assert_called_once_with("-topmost", True)
        label.pack.assert_called_once()
        button.pack.assert_called_once()


class HostProxyRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.assertIsNotNone(MitmProxyWorker)

    def test_stop_before_master_creation_shuts_master_down_and_exits_thread(self):
        build_started = threading.Event()
        allow_master_creation = threading.Event()
        run_entered_without_shutdown = threading.Event()
        created_masters = []

        class FakeMaster:
            def __init__(self) -> None:
                self.shutdown_calls = 0

            async def run(self) -> None:
                if self.shutdown_calls == 0:
                    run_entered_without_shutdown.set()

            def shutdown(self) -> None:
                self.shutdown_calls += 1

        def build_master():
            build_started.set()
            allow_master_creation.wait(timeout=2.0)
            master = FakeMaster()
            created_masters.append(master)
            return master

        worker = MitmProxyWorker(
            build_master=build_master,
            thread_name="test-proxy-worker-stop-before-master",
        )

        worker.start()
        self.assertTrue(build_started.wait(timeout=1.0))
        worker.stop()
        allow_master_creation.set()
        worker.join(timeout=1.0)

        self.assertEqual(len(created_masters), 1)
        self.assertEqual(created_masters[0].shutdown_calls, 1)
        self.assertFalse(run_entered_without_shutdown.is_set())
        self.assertFalse(worker.is_alive())

    def test_worker_cannot_restart_after_join(self):
        worker = MitmProxyWorker(
            build_master=lambda: SimpleNamespace(
                run=_async_noop,
                shutdown=lambda: None,
            ),
            thread_name="test-proxy-worker-no-restart",
        )

        worker.start()
        worker.join(timeout=1.0)

        with self.assertRaises(RuntimeError):
            worker.start()

    def test_proxy_worker_bootstrap_keeps_asyncio_off_main_thread(self):
        runner_thread_ids: list[int] = []
        shutdown_calls: list[str] = []

        class FakeMaster:
            def __init__(self) -> None:
                self.shutdown_called = False

            async def run(self) -> None:
                runner_thread_ids.append(threading.get_ident())

            def shutdown(self) -> None:
                self.shutdown_called = True
                shutdown_calls.append("shutdown")

        worker = MitmProxyWorker(
            build_master=lambda: FakeMaster(),
            thread_name="test-proxy-worker",
        )

        worker.start()
        worker.join(timeout=5.0)
        worker.stop()

        self.assertEqual(len(runner_thread_ids), 1)
        self.assertNotEqual(runner_thread_ids[0], threading.get_ident())
        self.assertEqual(shutdown_calls, ["shutdown"])
        self.assertFalse(worker.is_alive())


async def _async_noop() -> None:
    return None


if __name__ == "__main__":
    unittest.main()
