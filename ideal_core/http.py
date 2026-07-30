from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class HttpResponse:
    status: int
    url: str
    headers: dict[str, str]
    content: bytes

    @property
    def text(self) -> str:
        charset = "utf-8"
        content_type = self.headers.get("Content-Type", "")
        if "charset=" in content_type:
            charset = content_type.split("charset=", 1)[1].split(";", 1)[0].strip()
        return self.content.decode(charset, errors="replace")

    def json(self) -> Any:
        return json.loads(self.text)


class HttpClient:
    def __init__(self, timeout: int = 20, retries: int = 2, user_agent: str = ""):
        self.timeout = timeout
        self.retries = retries
        self.user_agent = user_agent or "iDeal"
        self.get_attempts = 0
        self.post_attempts = 0

    def get(
        self,
        url: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        if params:
            separator = "&" if "?" in url else "?"
            url = url + separator + urllib.parse.urlencode(params)
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "*/*",
        }
        request_headers.update(headers or {})
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self.get_attempts += 1
            try:
                req = urllib.request.Request(url, headers=request_headers)
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    return HttpResponse(
                        status=int(response.status),
                        url=response.geturl(),
                        headers=dict(response.headers.items()),
                        content=response.read(),
                    )
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    raise
                retry_after = exc.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else (2**attempt)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                delay = 2**attempt
            if attempt < self.retries:
                time.sleep(min(delay + random.random() * 0.3, 8))
        raise RuntimeError(f"请求失败: {url}: {last_error}") from last_error

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> HttpResponse:
        request_headers = {
            "User-Agent": self.user_agent,
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        request_headers.update(headers or {})
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            self.post_attempts += 1
            try:
                req = urllib.request.Request(
                    url,
                    data=body,
                    headers=request_headers,
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=self.timeout) as response:
                    return HttpResponse(
                        status=int(response.status),
                        url=response.geturl(),
                        headers=dict(response.headers.items()),
                        content=response.read(),
                    )
            except urllib.error.HTTPError as exc:
                last_error = exc
                if exc.code not in {429, 500, 502, 503, 504}:
                    error_body = exc.read().decode("utf-8", errors="replace")[:500]
                    raise RuntimeError(
                        f"AI 请求返回 HTTP {exc.code}: {error_body}"
                    ) from exc
                retry_after = exc.headers.get("Retry-After", "")
                delay = float(retry_after) if retry_after.isdigit() else (2**attempt)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                last_error = exc
                delay = 2**attempt
            if attempt < self.retries:
                time.sleep(min(delay + random.random() * 0.3, 8))
        raise RuntimeError(f"POST 请求失败: {url}: {last_error}") from last_error
