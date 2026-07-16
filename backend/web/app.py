#!/usr/bin/env python3
"""Web 入口：浏览器内输入抖音链接并提取文案。"""

from __future__ import annotations

import json
import hmac
import os
import sys
import tempfile
from pathlib import Path
from urllib.parse import unquote, urlparse

_ROOT = Path(__file__).resolve().parent.parent


def _reexec_with_venv_if_needed() -> None:
    try:
        import flask  # noqa: F401
    except ImportError:
        venv_python = _ROOT / ".venv" / "bin" / "python"
        if venv_python.is_file():
            os.execv(
                str(venv_python),
                [str(venv_python), str(Path(__file__).resolve()), *sys.argv[1:]],
            )
        print(
            "缺少依赖。请先执行：\n"
            "  source .venv/bin/activate && pip install -r requirements.txt\n"
            "或运行：./run-web.sh",
            file=sys.stderr,
        )
        raise SystemExit(1)


_reexec_with_venv_if_needed()

if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import requests
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    render_template,
    request,
    send_from_directory,
    url_for,
)

from script.audio_extractor import convert_audio
from script.config import Settings
from script.doubao_transcriber import transcribe_audio_url
from script.douyin_resolver import resolve_douyin_share
from script.fns_audio_host import host_audio
from script.local_audio_recorder import (
    RecordingSession,
    choose_loopback_device,
    list_dshow_audio_devices,
    start_recording,
    stop_recording,
)
from script.paths import OUTPUT_DIR
from script.pipeline import process_douyin_share
from script.transcriber import transcribe_audio
from script.wechat_capture_jobs import JobStore
from script.wechat_radium_scanner import (
    find_recent_wechat_channels_links,
    find_recent_wechat_channels_media_urls,
)
from wechat_capture.security import CapturePaths, ensure_local_token, is_allowed_host

app = Flask(__name__, template_folder="templates")
app.config["MAX_CONTENT_LENGTH"] = 2 * 1024 * 1024

WHISPER_MODELS = ["tiny", "base", "small", "medium", "large-v2", "large-v3"]
LOCAL_RECORDING: RecordingSession | None = None
WECHAT_CAPTURE_JOB_STORE: JobStore | None = None


def _read_transcript(out_dir: Path) -> str:
    path = out_dir / "transcript.txt"
    if not path.exists():
        return ""
    text = path.read_text(encoding="utf-8")
    marker = "--- 文案 ---"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text.strip()


def _list_images(out_dir: Path) -> list[str]:
    images_dir = out_dir / "images"
    if not images_dir.is_dir():
        return []
    rel = out_dir.relative_to(OUTPUT_DIR)
    files = sorted(images_dir.glob("*"))
    return [
        url_for("serve_output", subpath=f"{rel.as_posix()}/images/{f.name}")
        for f in files
        if f.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp", ".gif"}
    ]


def _meta_payload(meta) -> dict:
    return {
        "success": True,
        "video_id": meta.aweme_id,
        "aweme_id": meta.aweme_id,
        "title": meta.title,
        "author": meta.author,
        "content_type": meta.content_type,
        "aweme_type": meta.aweme_type,
        "download_url": meta.download_url,
        "cover_url": meta.cover_url,
        "image_urls": meta.image_urls,
        "image_count": len(meta.image_urls),
        "source_url": meta.source_url,
    }


def _render_index_page(*, result=None, error=None):
    return render_template(
        "index.html",
        models=WHISPER_MODELS,
        default_model="small",
        result=result,
        error=error,
    )


def _wechat_capture_store() -> JobStore:
    configured = app.config.get("WECHAT_CAPTURE_JOB_STORE")
    if configured is not None:
        return configured

    global WECHAT_CAPTURE_JOB_STORE
    if WECHAT_CAPTURE_JOB_STORE is None:
        paths = CapturePaths()
        WECHAT_CAPTURE_JOB_STORE = JobStore(
            paths.base_dir / "jobs",
            OUTPUT_DIR,
        )
    return WECHAT_CAPTURE_JOB_STORE


def _wechat_capture_token() -> str:
    configured = app.config.get("WECHAT_CAPTURE_TOKEN")
    if configured is not None:
        return str(configured)
    return ensure_local_token(CapturePaths())


def _wechat_capture_authorized() -> bool:
    supplied = request.headers.get("X-Xiaolou-Capture-Token", "")
    expected = _wechat_capture_token()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def _require_wechat_capture_auth():
    if not _wechat_capture_authorized():
        abort(401)


@app.route("/", methods=["GET", "POST"])
def index():
    """GET 渲染 SPA；POST 兼容旧版表单或缓存页面提交到 / 的情况。"""
    if request.method == "GET":
        resp = make_response(_render_index_page())
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate"
        return resp

    # 旧表单字段 share_text / model
    share_text = (request.form.get("share_text") or request.form.get("url") or "").strip()
    if not share_text and request.is_json:
        data = request.get_json(silent=True) or {}
        share_text = (data.get("url") or data.get("share_text") or "").strip()

    if not share_text:
        return _render_index_page(error="请输入抖音分享链接或分享文案"), 400

    model = request.form.get("model") or "small"
    if model not in WHISPER_MODELS:
        model = "small"

    try:
        out_dir = process_douyin_share(
            share_text,
            settings=Settings(output_dir=OUTPUT_DIR, whisper_model=model),
        )
        meta_path = out_dir / "meta.json"
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        rel = out_dir.relative_to(OUTPUT_DIR).as_posix()
        result = {
            "content_type": meta.get("content_type", "video"),
            "aweme_id": meta.get("aweme_id", ""),
            "title": meta.get("title", ""),
            "author": meta.get("author", ""),
            "out_dir": str(out_dir.resolve()),
            "rel_dir": rel,
            "transcript": _read_transcript(out_dir),
            "images": _list_images(out_dir),
        }
        return _render_index_page(result=result)
    except Exception as e:
        return _render_index_page(error=str(e)), 500


@app.route("/api/health")
def api_health():
    """本地 Whisper 模式，无需云端 API Key。"""
    return jsonify(
        {
            "success": True,
            "api_key_configured": True,
            "engine": "local",
            "whisper": "faster-whisper",
            "doubao": "volc.seedasr.auc",
            "doubao_resources": ["volc.seedasr.auc", "volc.bigasr.auc"],
            "models": WHISPER_MODELS,
        }
    )


@app.route("/api/video/info", methods=["POST"])
def api_video_info():
    data = request.get_json(silent=True) or {}
    share_text = (data.get("url") or "").strip()
    if not share_text:
        return jsonify({"success": False, "error": "请输入分享链接"}), 400
    try:
        meta = resolve_douyin_share(share_text)
        return jsonify(_meta_payload(meta))
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/audio/transcribe", methods=["POST"])
def api_audio_transcribe():
    data = request.get_json(silent=True) or {}
    audio_path = Path(data.get("audio_path") or "").resolve()
    api_key = (data.get("doubao_api_key") or "").strip()
    resource_id = (data.get("doubao_resource_id") or "volc.seedasr.auc").strip()
    if not api_key:
        return jsonify({"success": False, "error": "豆包 API Key 尚未填写"}), 400
    if not audio_path.is_file() or audio_path.suffix.lower() != ".mp3":
        return jsonify({"success": False, "error": "找不到要转写的 MP3 音频"}), 400

    try:
        with tempfile.TemporaryDirectory(prefix="doubao-audio-") as tmp_dir:
            normalized_audio = Path(tmp_dir) / "audio.mp3"
            convert_audio(
                audio_path,
                normalized_audio,
                sample_rate=16000,
                audio_format="mp3",
            )
            with host_audio(normalized_audio) as audio_url:
                result = transcribe_audio_url(
                    audio_url,
                    api_key=api_key,
                    title=(data.get("title") or "").strip(),
                    author=(data.get("author") or "").strip(),
                    cover_url=(data.get("cover_url") or "").strip() or None,
                    resource_id=resource_id,
                )
        return jsonify(
            {
                "success": True,
                "text": result.text,
                "segments": result.segments,
                "transcription_engine": "doubao",
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/local-audio/devices")
def api_local_audio_devices():
    try:
        devices = list_dshow_audio_devices()
        return jsonify(
            {
                "success": True,
                "devices": devices,
                "recommended_device": choose_loopback_device(devices) or "",
                "recording": LOCAL_RECORDING is not None
                and LOCAL_RECORDING.process.poll() is None,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/local-audio/start", methods=["POST"])
def api_local_audio_start():
    global LOCAL_RECORDING
    if LOCAL_RECORDING is not None and LOCAL_RECORDING.process.poll() is None:
        return jsonify({"success": False, "error": "已有一段本地录音正在进行"}), 409

    data = request.get_json(silent=True) or {}
    try:
        LOCAL_RECORDING = start_recording(
            OUTPUT_DIR,
            device=(data.get("device") or "").strip() or None,
            allow_microphone=bool(data.get("allow_microphone")),
        )
        return jsonify(
            {
                "success": True,
                "session_id": LOCAL_RECORDING.session_id,
                "device": LOCAL_RECORDING.device,
                "out_dir": str(LOCAL_RECORDING.out_dir.resolve()),
                "audio_path": str(LOCAL_RECORDING.audio_path.resolve()),
            }
        )
    except Exception as e:
        LOCAL_RECORDING = None
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/local-audio/stop", methods=["POST"])
def api_local_audio_stop():
    global LOCAL_RECORDING
    if LOCAL_RECORDING is None:
        return jsonify({"success": False, "error": "没有正在进行的本地录音"}), 409

    data = request.get_json(silent=True) or {}
    session = LOCAL_RECORDING
    LOCAL_RECORDING = None
    try:
        audio_path = stop_recording(session)
        model = data.get("model") or "base"
        if model not in WHISPER_MODELS:
            model = "base"
        api_key = (data.get("doubao_api_key") or "").strip()
        resource_id = (data.get("doubao_resource_id") or "volc.seedasr.auc").strip()
        title = (data.get("title") or "本地播放录音").strip()
        author = (data.get("author") or "").strip()
        if api_key:
            with host_audio(audio_path) as audio_url:
                result = transcribe_audio_url(
                    audio_url,
                    api_key=api_key,
                    title=title,
                    author=author,
                    resource_id=resource_id,
                )
            engine = "doubao"
        else:
            result = transcribe_audio(
                audio_path,
                model_size=model,
                device="cpu",
                compute_type="int8",
            )
            engine = "whisper"

        meta = {
            "aweme_id": session.session_id,
            "title": title,
            "author": author,
            "source_url": data.get("source") or "",
            "content_type": "video",
            "platform": data.get("platform") or "local_audio",
            "transcription_engine": engine,
            "files": {"audio": audio_path.name, "transcript": "transcript.txt"},
        }
        (session.out_dir / "transcript.txt").write_text(result.text + "\n", encoding="utf-8")
        (session.out_dir / "transcript_segments.json").write_text(
            json.dumps(
                {"meta": meta, "segments": result.segments, "full_text": result.text},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        (session.out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return jsonify(
            {
                "success": True,
                "session_id": session.session_id,
                "video_id": session.session_id,
                "platform": meta["platform"],
                "title": title,
                "author": author,
                "content_type": "video",
                "text": result.text,
                "segments": result.segments,
                "transcription_engine": engine,
                "out_dir": str(session.out_dir.resolve()),
                "audio_path": str(audio_path.resolve()),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/video/extract", methods=["POST"])
def api_video_extract():
    data = request.get_json(silent=True) or {}
    share_text = (data.get("url") or "").strip()
    model = data.get("model") or "small"
    if model not in WHISPER_MODELS:
        model = "small"
    skip_transcribe = bool(
        data.get("skip_transcribe") or data.get("mode") == "video_only"
    )
    transcription_engine = data.get("transcription_engine") or "whisper"
    if not share_text:
        return jsonify({"success": False, "error": "请输入分享链接"}), 400
    try:
        out_dir = process_douyin_share(
            share_text,
            settings=Settings(
                output_dir=OUTPUT_DIR,
                whisper_model=model,
                whisper_device="cpu",
                whisper_compute_type="int8",
                audio_format="mp3",
                audio_sample_rate=16000,
                skip_transcribe=skip_transcribe,
                transcription_engine=transcription_engine,
                doubao_api_key=data.get("doubao_api_key") or "",
                doubao_resource_id=data.get("doubao_resource_id")
                or "volc.seedasr.auc",
                whisper_fallback=bool(data.get("whisper_fallback", True)),
            ),
        )
        meta_path = out_dir / "meta.json"
        meta = {}
        if meta_path.exists():
            meta = json.loads(meta_path.read_text(encoding="utf-8"))

        video_dl = ""
        if meta.get("content_type") == "video":
            dl_path = out_dir / "download_url.txt"
            if dl_path.exists():
                video_dl = dl_path.read_text(encoding="utf-8").strip()
            elif meta.get("download_url"):
                video_dl = meta["download_url"]

        return jsonify(
            {
                "success": True,
                "video_id": meta.get("aweme_id", ""),
                "title": meta.get("title", ""),
                "author": meta.get("author", ""),
                "platform": meta.get("platform", "douyin"),
                "content_type": meta.get("content_type", "video"),
                "download_url": video_dl,
                "text": _read_transcript(out_dir),
                "transcription_engine": meta.get(
                    "transcription_engine", transcription_engine
                ),
                "transcription_error": meta.get("transcription_error") or "",
                "out_dir": str(out_dir.resolve()),
                "images": _list_images(out_dir),
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/wechat-radium/recent")
def api_wechat_radium_recent():
    try:
        minutes = int(request.args.get("minutes", "240"))
        limit = int(request.args.get("limit", "10"))
        links = find_recent_wechat_channels_links(minutes=minutes, limit=limit)
        return jsonify(
            {
                "success": True,
                "links": [
                    {
                        "url": link.url,
                        "path": link.path,
                        "modified_at": link.modified_at,
                        "size": link.size,
                    }
                    for link in links
                ],
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/wechat-capture/jobs", methods=["POST"])
def api_wechat_capture_create_job():
    _require_wechat_capture_auth()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "error": "Invalid JSON payload"}), 400

    required = {"capture_id", "media_url"}
    if not required.issubset(data) or not all(data.get(name) for name in required):
        return jsonify({"success": False, "error": "Missing capture fields"}), 400

    parsed = urlparse(str(data["media_url"]))
    if (
        parsed.scheme not in {"http", "https"}
        or not is_allowed_host(parsed.hostname or "")
    ):
        return jsonify({"success": False, "error": "Media host is not allowed"}), 400

    try:
        job = _wechat_capture_store().create(data)
        return jsonify({"success": True, "job": job}), 201
    except (KeyError, TypeError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400


@app.route("/api/wechat-capture/jobs", methods=["GET"])
def api_wechat_capture_list_jobs():
    _require_wechat_capture_auth()
    return jsonify({"success": True, "jobs": _wechat_capture_store().list()})


@app.route("/api/wechat-capture/jobs/<job_id>", methods=["GET"])
def api_wechat_capture_get_job(job_id: str):
    _require_wechat_capture_auth()
    job = _wechat_capture_store().get(job_id)
    if job is None:
        abort(404)
    return jsonify({"success": True, "job": job})


@app.route("/api/wechat-capture/jobs/<job_id>/start", methods=["POST"])
def api_wechat_capture_start_job(job_id: str):
    _require_wechat_capture_auth()
    settings = request.get_json(silent=True) or {}
    if not isinstance(settings, dict):
        return jsonify({"success": False, "error": "Invalid settings"}), 400
    try:
        job = _wechat_capture_store().start(job_id, settings)
    except KeyError:
        abort(404)
    except (FileNotFoundError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    return jsonify({"success": True, "job": job}), 202


@app.route("/api/wechat-capture/jobs/<job_id>/ack", methods=["POST"])
def api_wechat_capture_ack_job(job_id: str):
    _require_wechat_capture_auth()
    try:
        job = _wechat_capture_store().acknowledge(job_id)
    except KeyError:
        abort(404)
    except ValueError as exc:
        return jsonify({"success": False, "error": str(exc)}), 409
    return jsonify({"success": True, "job": job})


def _radium_candidate_payload(link, index: int) -> dict:
    parsed = urlparse(link.url)
    path = Path(link.path)
    return {
        "index": index,
        "url": link.url,
        "host": parsed.netloc,
        "file": path.name,
        "path": link.path,
        "modified_at": link.modified_at,
        "size": link.size,
        "offset": getattr(link, "offset", 0),
    }


@app.route("/api/wechat-radium/candidates", methods=["POST"])
def api_wechat_radium_candidates():
    data = request.get_json(silent=True) or {}
    anchor_url = (data.get("url") or "").strip()
    minutes = int(data.get("minutes") or 240)
    limit = int(data.get("limit") or 5)
    try:
        links = find_recent_wechat_channels_media_urls(
            minutes=minutes,
            limit=limit,
            anchor_url=anchor_url,
            require_anchor=bool(anchor_url),
        )
        return jsonify(
            {
                "success": True,
                "candidates": [
                    _radium_candidate_payload(link, index + 1)
                    for index, link in enumerate(links)
                ],
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500


@app.route("/api/wechat-radium/extract", methods=["POST"])
def api_wechat_radium_extract():
    data = request.get_json(silent=True) or {}
    link = (data.get("url") or "").strip()
    explicit_link = bool(link)
    if not link:
        links = find_recent_wechat_channels_links(
            minutes=int(data.get("minutes") or 240),
            limit=1,
        )
        if not links:
            return jsonify(
                {
                    "success": False,
                    "error": "没有在 PC 微信缓存中找到最近的视频号授权链接。请先在 PC 微信里打开目标视频并播放几秒。",
                }
            ), 404
        link = links[0].url

    model = data.get("model") or "small"
    if model not in WHISPER_MODELS:
        model = "small"
    transcription_engine = data.get("transcription_engine") or "whisper"
    settings = Settings(
        output_dir=OUTPUT_DIR,
        whisper_model=model,
        whisper_device="cpu",
        whisper_compute_type="int8",
        audio_format="mp3",
        audio_sample_rate=16000,
        transcription_engine=transcription_engine,
        doubao_api_key=data.get("doubao_api_key") or "",
        doubao_resource_id=data.get("doubao_resource_id") or "volc.seedasr.auc",
        whisper_fallback=bool(data.get("whisper_fallback", True)),
    )
    media_url = ""
    try:
        try:
            out_dir = process_douyin_share(link, settings=settings)
        except Exception as page_error:
            if "media URL was not exposed" not in str(page_error):
                raise
            media_links = find_recent_wechat_channels_media_urls(
                minutes=int(data.get("minutes") or 240),
                limit=1,
                anchor_url=link,
                require_anchor=explicit_link,
            )
            if not media_links:
                if explicit_link:
                    raise RuntimeError(
                        "没有在 PC 微信缓存中找到当前笔记链接对应的媒体流。请在 PC 微信打开这条链接并播放几秒后重试。"
                    )
                raise
            media_url = media_links[0].url
            out_dir = process_douyin_share(media_url, settings=settings)
        meta_path = out_dir / "meta.json"
        meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        dl_path = out_dir / "download_url.txt"
        return jsonify(
            {
                "success": True,
                "video_id": meta.get("aweme_id", ""),
                "title": meta.get("title", ""),
                "author": meta.get("author", ""),
                "platform": meta.get("platform", "wechat_channels"),
                "content_type": meta.get("content_type", "video"),
                "download_url": dl_path.read_text(encoding="utf-8").strip()
                if dl_path.exists()
                else meta.get("download_url", ""),
                "text": _read_transcript(out_dir),
                "transcription_engine": meta.get(
                    "transcription_engine", transcription_engine
                ),
                "transcription_error": meta.get("transcription_error") or "",
                "out_dir": str(out_dir.resolve()),
                "images": _list_images(out_dir),
                "radium_url": link,
                "media_url": media_url,
            }
        )
    except Exception as e:
        return jsonify({"success": False, "error": str(e), "radium_url": link}), 500


@app.route("/api/video/download")
def api_video_download():
    """代理下载无水印视频直链。"""
    raw_url = request.args.get("url", "")
    filename = request.args.get("filename", "video.mp4")
    if not raw_url:
        abort(400)
    safe_name = Path(filename).name
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15"
        ),
        "Referer": "https://www.iesdouyin.com/",
    }
    upstream = requests.get(raw_url, headers=headers, stream=True, timeout=120)
    upstream.raise_for_status()

    def generate():
        for chunk in upstream.iter_content(chunk_size=1024 * 256):
            if chunk:
                yield chunk

    resp = Response(generate(), content_type="video/mp4")
    resp.headers["Content-Disposition"] = f'attachment; filename="{safe_name}"'
    return resp


@app.route("/files/<path:subpath>")
def serve_output(subpath: str):
    """预览已下载的图片、视频等资源。"""
    subpath = unquote(subpath)
    target = (OUTPUT_DIR / subpath).resolve()
    root = OUTPUT_DIR.resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        abort(404)
    return send_from_directory(target.parent, target.name)


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.environ.get("PORT", "5050"))
    print(f"打开浏览器访问: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, debug=True)
