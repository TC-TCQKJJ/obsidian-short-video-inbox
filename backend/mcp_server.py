from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from agent_tools.wechat_capture_client import (
    CaptureBackendError,
    WechatCaptureClient,
)


mcp = FastMCP(
    "Xiaolou WeChat Capture",
    instructions=(
        "Operate the local Windows WeChat Channels capture pipeline. "
        "A target video must first be opened and played in PC WeChat so the "
        "capture helper can create a waiting job. Never guess a job id. List "
        "jobs, select by title and author, then start only after the user asks "
        "to process that target. Starting a Doubao job may incur API charges."
    ),
)
_capture_client: WechatCaptureClient | None = None


def _client() -> WechatCaptureClient:
    global _capture_client
    if _capture_client is None:
        _capture_client = WechatCaptureClient()
    return _capture_client


def _run(callable_, *args, **kwargs):
    try:
        return callable_(*args, **kwargs)
    except CaptureBackendError as exc:
        raise ValueError(str(exc)) from None


@mcp.tool()
def wechat_capture_status() -> dict:
    """Check backend readiness, API-key availability, and capture job counts."""
    return _run(_client().status)


@mcp.tool()
def wechat_capture_list(status: str = "", limit: int = 10) -> dict:
    """List recent unacknowledged jobs without URLs, secrets, or local paths."""
    return _run(_client().list_jobs, status=status, limit=limit)


@mcp.tool()
def wechat_capture_start(
    job_id: str,
    transcription_engine: str = "doubao",
    whisper_model: str = "base",
) -> dict:
    """Start one previously listed job. This downloads and transcribes media."""
    return _run(
        _client().start_job,
        job_id,
        transcription_engine=transcription_engine,
        whisper_model=whisper_model,
    )


@mcp.tool()
def wechat_capture_get(job_id: str) -> dict:
    """Get one job's safe metadata and current processing status."""
    return _run(_client().get_job, job_id)


@mcp.tool()
def wechat_capture_read_transcript(
    job_id: str,
    offset: int = 0,
    limit: int = 8000,
) -> dict:
    """Read a completed transcript in bounded chunks of at most 12000 chars."""
    return _run(_client().read_transcript, job_id, offset=offset, limit=limit)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
