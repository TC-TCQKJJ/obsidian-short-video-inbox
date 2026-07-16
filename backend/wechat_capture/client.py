from __future__ import annotations

import json
import logging
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from wechat_capture.model import CaptureCandidate


class CaptureBackendClient:
    def __init__(
        self,
        *,
        token: str,
        endpoint: str = "http://127.0.0.1:5050/api/wechat-capture/jobs",
        timeout: float = 5.0,
        opener=urlopen,
        logger: logging.Logger | None = None,
    ) -> None:
        self.token = token
        self.endpoint = endpoint
        self.timeout = timeout
        self.opener = opener
        self.logger = logger or logging.getLogger(__name__)

    def submit(self, candidate: CaptureCandidate) -> bool:
        body = json.dumps(
            candidate.to_private_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        request = Request(self.endpoint, data=body, method="POST")
        request.headers["Content-Type"] = "application/json"
        request.headers["X-Xiaolou-Capture-Token"] = self.token
        try:
            response = self.opener(request, timeout=self.timeout)
        except (HTTPError, OSError, URLError) as exc:
            self.logger.warning(
                "Failed to submit WeChat capture %s: %s",
                candidate.capture_id,
                exc,
            )
            return False
        status = int(getattr(response, "status", 200) or 200)
        return 200 <= status < 300


def submit_candidate(
    candidate: CaptureCandidate,
    token: str,
    *,
    endpoint: str = "http://127.0.0.1:5050/api/wechat-capture/jobs",
    timeout: float = 5.0,
    opener=urlopen,
    logger: logging.Logger | None = None,
) -> bool:
    client = CaptureBackendClient(
        token=token,
        endpoint=endpoint,
        timeout=timeout,
        opener=opener,
        logger=logger,
    )
    return client.submit(candidate)
