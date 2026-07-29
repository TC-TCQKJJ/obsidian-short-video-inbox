from __future__ import annotations

import asyncio
import argparse
import ctypes
import logging
import queue
import socket
import threading
import time
from dataclasses import dataclass

from wechat_capture.addon import PassiveWechatCaptureAddon, build_proxy_options
from wechat_capture.client import CaptureBackendClient
from wechat_capture.pending_queue import PendingCaptureQueue
from wechat_capture.matcher import CaptureMatcher
from wechat_capture.process_capture import ProcessCaptureWorker
from wechat_capture.security import CapturePaths, ensure_local_token
from wechat_capture.ui import (
    CaptureCoordinator,
    NativeCaptureControlWindow,
    NativeCapturePresenter,
)


HOTKEY_MOD_ALT = 0x0001
HOTKEY_MOD_CONTROL = 0x0002
HOTKEY_MOD_SHIFT = 0x0004
HOTKEY_MODIFIERS = HOTKEY_MOD_ALT | HOTKEY_MOD_CONTROL
HOTKEY_FALLBACK_MODIFIERS = HOTKEY_MODIFIERS | HOTKEY_MOD_SHIFT
HOTKEY_VK = ord("S")
HOTKEY_POLL_MS = 50


class _Point(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _Msg(ctypes.Structure):
    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint),
        ("pt", _Point),
        ("lPrivate", ctypes.c_uint),
    ]


class WindowsHotkeyManager:
    WM_HOTKEY = 0x0312
    WM_QUIT = 0x0012
    THREAD_TIMEOUT_SECONDS = 5.0

    def __init__(self) -> None:
        self._user32 = ctypes.windll.user32
        self._next_hotkey_id = 1
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._active_hotkey_id: int | None = None
        self._ready = threading.Event()
        self._register_error: BaseException | None = None
        self._triggered_hotkey_ids: queue.SimpleQueue[int] = queue.SimpleQueue()

    def register(self, modifiers: int, vk: int) -> int:
        if self._thread is not None and self._thread.is_alive():
            raise RuntimeError("A hotkey is already registered")

        hotkey_id = self._next_hotkey_id
        self._next_hotkey_id += 1
        self._ready = threading.Event()
        self._register_error = None
        self._thread_id = None
        self._thread = threading.Thread(
            target=self._listen,
            args=(hotkey_id, modifiers, vk),
            name="wechat-capture-hotkey",
            daemon=True,
        )
        self._thread.start()

        if not self._ready.wait(timeout=self.THREAD_TIMEOUT_SECONDS):
            self._request_stop()
            self._thread.join(timeout=self.THREAD_TIMEOUT_SECONDS)
            raise TimeoutError("Hotkey listener did not finish registration")
        if self._register_error is not None:
            self._thread.join(timeout=self.THREAD_TIMEOUT_SECONDS)
            error = self._register_error
            self._thread = None
            self._thread_id = None
            raise error

        self._active_hotkey_id = hotkey_id
        return hotkey_id

    def unregister(self, hotkey_id: int) -> None:
        if hotkey_id != self._active_hotkey_id or self._thread is None:
            return
        self._request_stop()
        self._thread.join(timeout=self.THREAD_TIMEOUT_SECONDS)
        if self._thread.is_alive():
            raise TimeoutError("Hotkey listener did not stop")
        self._thread = None
        self._thread_id = None
        self._active_hotkey_id = None

    def pop_triggered(self, hotkey_id: int) -> bool:
        try:
            while True:
                if self._triggered_hotkey_ids.get_nowait() == hotkey_id:
                    return True
        except queue.Empty:
            return False

    def _listen(self, hotkey_id: int, modifiers: int, vk: int) -> None:
        registered = False
        self._thread_id = threading.get_native_id()
        try:
            if not self._user32.RegisterHotKey(None, hotkey_id, modifiers, vk):
                raise ctypes.WinError()
            registered = True
            self._ready.set()

            message = _Msg()
            while True:
                result = self._user32.GetMessageW(
                    ctypes.byref(message),
                    None,
                    0,
                    0,
                )
                if result == -1:
                    raise ctypes.WinError()
                if result == 0:
                    break
                if (
                    message.message == self.WM_HOTKEY
                    and int(message.wParam) == hotkey_id
                ):
                    self._triggered_hotkey_ids.put(hotkey_id)
        except BaseException as exc:
            self._register_error = exc
            self._ready.set()
        finally:
            if registered:
                self._user32.UnregisterHotKey(None, hotkey_id)

    def _request_stop(self) -> None:
        if self._thread_id is not None:
            self._user32.PostThreadMessageW(
                self._thread_id,
                self.WM_QUIT,
                0,
                0,
            )


class MitmProxyWorker:
    def __init__(
        self,
        *,
        build_master,
        thread_name: str = "wechat-capture-proxy",
    ) -> None:
        self._build_master = build_master
        self._thread_name = thread_name
        self._thread: threading.Thread | None = None
        self._master = None
        self._error: BaseException | None = None
        self._stop_requested = threading.Event()
        self._started_once = False

    def start(self) -> None:
        if self._started_once:
            raise RuntimeError("MitmProxyWorker can only be started once")
        self._started_once = True
        self._stop_requested.clear()
        self._thread = threading.Thread(target=self._run, name=self._thread_name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_requested.set()
        if self._master is not None:
            self._master.shutdown()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            if not self._thread.is_alive():
                self._thread = None

    def is_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def wait_ready(self, timeout: float) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._error is not None:
                raise RuntimeError("Capture proxy failed to start") from self._error
            try:
                with socket.create_connection(("127.0.0.1", 2023), timeout=0.2):
                    return
            except OSError:
                if self._thread is not None and not self._thread.is_alive():
                    break
                time.sleep(0.05)
        if self._error is not None:
            raise RuntimeError("Capture proxy failed to start") from self._error
        raise TimeoutError("Capture proxy did not listen on 127.0.0.1:2023")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)

            async def run_master() -> None:
                self._master = self._build_master()
                if self._stop_requested.is_set():
                    self._master.shutdown()
                    return
                await self._master.run()

            loop.run_until_complete(run_master())
        except BaseException as exc:
            self._error = exc
        finally:
            try:
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                asyncio.set_event_loop(None)
                loop.close()


@dataclass
class WechatCaptureHost:
    root: object
    matcher: object
    paths: CapturePaths
    token: str
    coordinator: object | None = None
    hotkeys: object | None = None
    proxy_worker: object | None = None
    hotkey_fallback_notifier: object | None = None
    proxy_join_timeout: float = 5.0

    def __post_init__(self) -> None:
        self.hotkeys = self.hotkeys or WindowsHotkeyManager()
        self.coordinator = self.coordinator or CaptureCoordinator(
            self.matcher,
            NativeCapturePresenter(self.root),
            CaptureBackendClient(token=self.token),
            PendingCaptureQueue(self.paths.queue_dir / "pending-captures.dat"),
        )
        self._hotkey_id: int | None = None
        self._proxy_worker = self.proxy_worker
        self.hotkey_fallback_notifier = (
            self.hotkey_fallback_notifier or _notify_hotkey_fallback
        )
        self._started = False
        self._stopped = False

    def start(self) -> None:
        if self._started or self._stopped:
            raise RuntimeError("WechatCaptureHost can only be started once")
        if self._proxy_worker is None:
            proxy_options = build_proxy_options(self.paths)
            self._proxy_worker = MitmProxyWorker(
                build_master=lambda: _build_dump_master(proxy_options, self.matcher),
            )
        self._proxy_worker.start()
        try:
            wait_ready = getattr(self._proxy_worker, "wait_ready", None)
            if callable(wait_ready):
                wait_ready(self.proxy_join_timeout)
            try:
                self._hotkey_id = self.hotkeys.register(
                    HOTKEY_MODIFIERS,
                    HOTKEY_VK,
                )
            except OSError as exc:
                if getattr(exc, "winerror", None) != 1409:
                    raise
                try:
                    self._hotkey_id = self.hotkeys.register(
                        HOTKEY_FALLBACK_MODIFIERS,
                        HOTKEY_VK,
                    )
                except OSError as fallback_exc:
                    if getattr(fallback_exc, "winerror", None) != 1409:
                        raise
                    logging.getLogger("wechat_capture").warning(
                        "Global capture hotkeys are unavailable; use the control window."
                    )
                else:
                    self.hotkey_fallback_notifier("Ctrl+Alt+Shift+S")
        except Exception:
            self._stopped = True
            self._proxy_worker.stop()
            self._proxy_worker.join(timeout=self.proxy_join_timeout)
            raise
        self._started = True
        self.root.protocol("WM_DELETE_WINDOW", self.stop)
        self.root.after(HOTKEY_POLL_MS, self.poll_hotkey)

    def poll_hotkey(self) -> None:
        if self._stopped:
            return
        if self._hotkey_id is not None and self.hotkeys.pop_triggered(self._hotkey_id):
            self.coordinator.handle_hotkey()
        self.root.after(HOTKEY_POLL_MS, self.poll_hotkey)

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            try:
                if self._hotkey_id is not None:
                    self.hotkeys.unregister(self._hotkey_id)
            finally:
                self._hotkey_id = None
                if self._started and self._proxy_worker is not None:
                    self._proxy_worker.stop()
                    self._proxy_worker.join(timeout=self.proxy_join_timeout)
        finally:
            if self._started:
                self.root.destroy()


def _build_dump_master(proxy_options, matcher):
    from mitmproxy.tools.dump import DumpMaster

    master = DumpMaster(proxy_options, with_termlog=False, with_dumper=False)
    master.addons.add(PassiveWechatCaptureAddon(matcher))
    return master


def _notify_hotkey_fallback(shortcut: str) -> None:
    import tkinter as tk

    window = tk.Toplevel()
    window.title("微信视频号捕获")
    window.attributes("-topmost", True)
    window.resizable(False, False)
    window.protocol("WM_DELETE_WINDOW", window.destroy)
    tk.Label(
        window,
        text=f"Ctrl+Alt+S 已被其他程序占用，本次请使用 {shortcut}。",
        padx=20,
        pady=16,
    ).pack()
    tk.Button(
        window,
        text="知道了",
        command=window.destroy,
        width=10,
    ).pack(
        pady=(0, 16),
    )


def initialize_capture_host(paths: CapturePaths) -> None:
    from mitmproxy import certs

    certs.CertStore.from_store(paths.mitm_dir, "mitmproxy", 2048)
    ensure_local_token(paths)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Xiaolou WeChat capture host")
    parser.add_argument(
        "--init",
        action="store_true",
        help="Generate the unique CA and local API token, then exit.",
    )
    args = parser.parse_args(argv)
    paths = CapturePaths()
    if args.init:
        initialize_capture_host(paths)
        return 0

    import tkinter as tk

    initialize_capture_host(paths)
    capture_logger = logging.getLogger("wechat_capture")
    capture_logger.setLevel(logging.INFO)
    capture_logger.propagate = False
    file_handler = logging.FileHandler(paths.log_path, encoding="utf-8")
    file_handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    )
    capture_logger.addHandler(file_handler)
    root = tk.Tk()
    root.withdraw()
    matcher = CaptureMatcher(
        max_age_seconds=120,
        logger=capture_logger,
        require_active=True,
    )
    token = ensure_local_token(paths)
    process_worker = ProcessCaptureWorker(
        paths=paths,
        token=token,
        matcher=matcher,
        logger=capture_logger,
    )
    host = WechatCaptureHost(
        root=root,
        matcher=matcher,
        paths=paths,
        token=token,
        proxy_worker=process_worker,
        proxy_join_timeout=30.0,
    )
    try:
        host.start()
    except Exception:
        capture_logger.exception("WeChat process capture failed to start")
        from tkinter import messagebox

        messagebox.showerror(
            "微信视频号捕获",
            f"捕获驱动启动失败。\n请查看日志：\n{paths.log_path}",
            parent=root,
        )
        root.destroy()
        return 1
    control_window = NativeCaptureControlWindow(
        root,
        host.coordinator.handle_hotkey,
        service_status=process_worker.processor.diagnostic_text,
    )
    control_window.show()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
