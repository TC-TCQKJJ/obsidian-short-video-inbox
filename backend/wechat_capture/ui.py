from __future__ import annotations

import io
from dataclasses import dataclass
from datetime import datetime
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import urlopen

from wechat_capture.model import CaptureCandidate
from wechat_capture.security import is_allowed_host


APP_TITLE = "微信视频号捕获"
NO_CANDIDATE_MESSAGE = "请播放目标视频几秒后重试"
SELECTION_WINDOW_TITLE = "选择要保存的视频"
CONFIRM_WINDOW_TITLE = "确认保存"
CONFIRM_BUTTON_TEXT = "确认保存"
CANCEL_BUTTON_TEXT = "取消"
AUTHOR_LABEL = "作者"
DURATION_LABEL = "时长"
CAPTURE_TIME_LABEL = "捕获时间"
CONTROL_BUTTON_TEXT = "检查当前视频"
CONTROL_SERVICE_TEXT = "服务运行中"
CONTROL_IDLE_TEXT = "等待检查"
CONTROL_CHECKING_TEXT = "正在检查"
CONTROL_SUCCESS_TEXT = "已提交处理"
CONTROL_EMPTY_TEXT = "未发现候选"
CONTROL_ERROR_TEXT = "检查失败"
CONTROL_ERROR_MESSAGE = "检查失败，请重试"
MAX_COVER_BYTES = 5 * 1024 * 1024
ALLOWED_COVER_SCHEMES = {"http", "https"}


def format_duration(duration_ms: int) -> str:
    seconds = max(0, int(duration_ms // 1000))
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def format_capture_time(observed_at: float) -> str:
    return datetime.fromtimestamp(observed_at).strftime("%Y-%m-%d %H:%M:%S")


def load_cover_image(url: str, *, opener=urlopen, timeout: float = 3.0):
    if not _is_allowed_cover_url(url):
        return None
    response = None
    try:
        response = opener(url, timeout=timeout)
        data = response.read(MAX_COVER_BYTES + 1)
        if len(data) > MAX_COVER_BYTES:
            return None
        from PIL import Image

        image = Image.open(io.BytesIO(data))
        image.load()
        return image
    except (ModuleNotFoundError, OSError, URLError, ValueError):
        return None
    finally:
        close = getattr(response, "close", None)
        if callable(close):
            close()


def _is_allowed_cover_url(url: str) -> bool:
    if not url:
        return False
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in ALLOWED_COVER_SCHEMES:
        return False
    return is_allowed_host(parsed.hostname or "")


class CaptureCoordinator:
    def __init__(self, matcher, presenter, client, queue) -> None:
        self.matcher = matcher
        self.presenter = presenter
        self.client = client
        self.queue = queue

    def handle_hotkey(self) -> bool:
        candidates = self.matcher.recent_candidates()
        if not candidates:
            notify = getattr(self.presenter, "show_no_candidate_message", None)
            if callable(notify):
                notify(NO_CANDIDATE_MESSAGE)
            return False

        candidate = self.presenter.choose_candidate(candidates)
        if candidate is None:
            return False

        self.queue.enqueue(candidate)
        self.queue.flush(self.client.submit)
        return True


@dataclass
class NativeCaptureControlWindow:
    root: object
    on_capture: object
    service_status: object | None = None

    def __post_init__(self) -> None:
        self._service_label = None
        self._status_label = None

    def show(self) -> None:
        import tkinter as tk

        self.root.title(APP_TITLE)
        self.root.resizable(False, False)
        self.root.geometry("400x170")

        frame = tk.Frame(self.root, padx=20, pady=18)
        frame.pack(fill="both", expand=True)
        self._service_label = tk.Label(
            frame,
            text=CONTROL_SERVICE_TEXT,
            fg="#167C3B",
            anchor="w",
        )
        self._service_label.pack(fill="x")
        self._status_label = tk.Label(
            frame,
            text=CONTROL_IDLE_TEXT,
            anchor="w",
        )
        self._status_label.pack(fill="x", pady=(6, 0))
        tk.Button(
            frame,
            text=CONTROL_BUTTON_TEXT,
            command=self.check_current_video,
            width=24,
            height=2,
        ).pack(fill="x", pady=(16, 0))
        self.poll_service_status()
        self.root.deiconify()

    def poll_service_status(self) -> None:
        if self._service_label is not None and callable(self.service_status):
            try:
                text = str(self.service_status())
            except Exception:
                text = CONTROL_SERVICE_TEXT
            self._service_label.configure(text=text)
        self.root.after(1000, self.poll_service_status)

    def check_current_video(self) -> bool:
        if self._status_label is not None:
            self._status_label.configure(text=CONTROL_CHECKING_TEXT)
            self.root.update_idletasks()
        try:
            handled = bool(self.on_capture())
        except Exception:
            if self._status_label is not None:
                self._status_label.configure(text=CONTROL_ERROR_TEXT)
            from tkinter import messagebox

            messagebox.showerror(
                APP_TITLE,
                CONTROL_ERROR_MESSAGE,
                parent=self.root,
            )
            return False
        if self._status_label is not None:
            self._status_label.configure(
                text=CONTROL_SUCCESS_TEXT if handled else CONTROL_EMPTY_TEXT,
            )
        return handled


@dataclass
class NativeCapturePresenter:
    root: object

    def show_no_candidate_message(self, message: str) -> None:
        from tkinter import messagebox

        messagebox.showinfo(APP_TITLE, message, parent=self.root)

    def choose_candidate(self, candidates: list[CaptureCandidate]) -> CaptureCandidate | None:
        if not candidates:
            return None
        if len(candidates) == 1:
            return _ConfirmDialog(self.root, candidates[0]).show()
        selected = _SelectionDialog(self.root, candidates).show()
        if selected is None:
            return None
        return _ConfirmDialog(self.root, selected).show()


class _SelectionDialog:
    def __init__(self, root, candidates: list[CaptureCandidate]) -> None:
        import tkinter as tk

        self._selected: CaptureCandidate | None = None
        self._window = tk.Toplevel(root)
        self._window.title(SELECTION_WINDOW_TITLE)
        self._window.attributes("-topmost", True)
        self._window.transient(root)
        self._window.protocol("WM_DELETE_WINDOW", self._cancel)

        self._listbox = tk.Listbox(self._window, width=72, height=min(8, len(candidates)))
        for index, candidate in enumerate(candidates):
            self._listbox.insert(index, f"{candidate.title} - {candidate.author}")
        self._listbox.pack(fill="both", expand=True, padx=12, pady=12)

        button_bar = tk.Frame(self._window)
        button_bar.pack(fill="x", padx=12, pady=(0, 12))
        tk.Button(
            button_bar,
            text=CONFIRM_BUTTON_TEXT,
            command=lambda: self._confirm(candidates),
        ).pack(side="right")
        tk.Button(button_bar, text=CANCEL_BUTTON_TEXT, command=self._cancel).pack(
            side="right",
            padx=(0, 8),
        )

    def show(self) -> CaptureCandidate | None:
        self._window.grab_set()
        self._window.wait_window()
        return self._selected

    def _confirm(self, candidates: list[CaptureCandidate]) -> None:
        selection = self._listbox.curselection()
        if not selection:
            return
        self._selected = candidates[selection[0]]
        self._window.destroy()

    def _cancel(self) -> None:
        self._selected = None
        self._window.destroy()


class _ConfirmDialog:
    def __init__(self, root, candidate: CaptureCandidate) -> None:
        import tkinter as tk

        self._candidate = candidate
        self._result: CaptureCandidate | None = None
        self._window = tk.Toplevel(root)
        self._window.title(CONFIRM_WINDOW_TITLE)
        self._window.attributes("-topmost", True)
        self._window.transient(root)
        self._window.protocol("WM_DELETE_WINDOW", self._cancel)

        frame = tk.Frame(self._window)
        frame.pack(fill="both", expand=True, padx=12, pady=12)
        tk.Label(frame, text=candidate.title, anchor="w", justify="left", wraplength=420).pack(fill="x")
        tk.Label(frame, text=f"{AUTHOR_LABEL}：{candidate.author}", anchor="w").pack(fill="x", pady=(8, 0))
        tk.Label(
            frame,
            text=f"{DURATION_LABEL}：{format_duration(candidate.duration_ms)}",
            anchor="w",
        ).pack(fill="x")
        tk.Label(
            frame,
            text=f"{CAPTURE_TIME_LABEL}：{format_capture_time(candidate.observed_at)}",
            anchor="w",
        ).pack(fill="x")

        image = load_cover_image(candidate.cover_url)
        if image is not None:
            from PIL import ImageTk

            image.thumbnail((420, 240))
            self._image = ImageTk.PhotoImage(image)
            tk.Label(frame, image=self._image).pack(pady=(12, 8))

        button_bar = tk.Frame(frame)
        button_bar.pack(fill="x", pady=(8, 0))
        tk.Button(button_bar, text=CONFIRM_BUTTON_TEXT, command=self._confirm).pack(side="right")
        tk.Button(button_bar, text=CANCEL_BUTTON_TEXT, command=self._cancel).pack(
            side="right",
            padx=(0, 8),
        )

    def show(self) -> CaptureCandidate | None:
        self._window.grab_set()
        self._window.wait_window()
        return self._result

    def _confirm(self) -> None:
        self._result = self._candidate
        self._window.destroy()

    def _cancel(self) -> None:
        self._result = None
        self._window.destroy()
