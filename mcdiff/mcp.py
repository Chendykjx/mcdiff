"""A tiny, dependency-free client for the McDonald's China MCP server.

The server speaks JSON-RPC 2.0 over Streamable HTTP.  In practice it answers
with plain ``application/json`` (no SSE, no ``Mcp-Session-Id``), returns
business errors as HTTP 200 with ``success: false``, and only uses HTTP status
codes for authentication problems.

Design notes:
    * only the standard library is used (urllib);
    * every tool result is returned twice: the human-facing markdown
      (``content[0].text``) and the machine-readable ``structuredContent``;
    * a global minimum interval between requests keeps us far below the
      documented 600 requests/minute limit.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Mapping

DEFAULT_URL = "https://mcp.mcd.cn"
DEFAULT_TOKEN_ENV = "MCD_MCP_TOKEN"
#: 600 req/min is the documented ceiling; 0.11s keeps us at ~545/min.
DEFAULT_MIN_INTERVAL = 0.11


class McdMCPError(Exception):
    """Base class for every error raised by this module."""


class McdAuthError(McdMCPError):
    """The token is missing, malformed or rejected by the server."""


class McdRateLimitError(McdMCPError):
    """The server answered 429 (too many requests)."""


class McdTransportError(McdMCPError):
    """Network / HTTP level failure that is not auth related."""


class McdBusinessError(McdMCPError):
    """A tool answered ``success: false`` with a business error code."""

    def __init__(self, tool: str, code: Any, message: str, trace_id: str | None = None):
        self.tool = tool
        self.code = code
        self.message = message
        self.trace_id = trace_id
        super().__init__(f"{tool} failed: [{code}] {message}")


@dataclass(frozen=True)
class ToolResult:
    """A successful tool call."""

    tool: str
    structured: dict[str, Any]
    text: str
    raw: dict[str, Any]

    @property
    def data(self) -> Any:
        return self.structured.get("data")

    @property
    def trace_id(self) -> str | None:
        return self.structured.get("traceId")


class McdMCPClient:
    """Minimal MCP client: initialize, list_tools, call."""

    def __init__(
        self,
        token: str | None = None,
        url: str = DEFAULT_URL,
        *,
        timeout: float = 60.0,
        max_retries: int = 3,
        min_interval: float = DEFAULT_MIN_INTERVAL,
    ) -> None:
        token = (token or os.environ.get(DEFAULT_TOKEN_ENV) or "").strip()
        if not token:
            raise McdAuthError(
                "缺少 MCP Token。请先到 https://open.mcd.cn/mcp 申请，"
                f"然后设置环境变量 {DEFAULT_TOKEN_ENV}。"
            )
        self._token = token
        self.url = url
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self._last_request_at = 0.0
        self._request_count = 0
        self._initialize()

    # ------------------------------------------------------------------ plumbing
    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {self._token}",
        }

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request_at)
        if wait > 0:
            time.sleep(wait)
        self._last_request_at = time.monotonic()

    @staticmethod
    def _decode(raw: str) -> dict[str, Any]:
        """Accept both plain JSON and a JSON-RPC message framed as SSE."""
        stripped = raw.lstrip()
        if stripped.startswith("event:") or stripped.startswith("data:"):
            chunks = [line[5:].strip() for line in raw.splitlines() if line.startswith("data:")]
            raw = chunks[-1] if chunks else raw
        return json.loads(raw)

    def _request(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            request = urllib.request.Request(self.url, data=body, headers=self._headers(), method="POST")
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    self._request_count += 1
                    return self._decode(response.read().decode("utf-8", "replace"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[:200]
                if exc.code in (401, 403):
                    raise McdAuthError(
                        f"鉴权失败（HTTP {exc.code}）：{detail or 'token 无效或缺失'}"
                    ) from exc
                if exc.code == 429:
                    last_error = McdRateLimitError("触发限流（429），请降低请求频率")
                else:
                    last_error = McdTransportError(f"HTTP {exc.code}: {detail}")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = McdTransportError(f"{type(exc).__name__}: {exc}")
            if attempt < self.max_retries:
                time.sleep(0.8 * (2**attempt))
        assert last_error is not None
        raise last_error

    def _rpc(self, method: str, params: Mapping[str, Any] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"jsonrpc": "2.0", "id": self._request_count + 1, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        reply = self._request(payload)
        if "error" in reply:
            raise McdTransportError(f"JSON-RPC error: {reply['error']}")
        return reply

    def _initialize(self) -> None:
        self._rpc(
            "initialize",
            {
                "protocolVersion": "2025-03-26",
                "capabilities": {},
                "clientInfo": {"name": "mcdiff", "version": "0.1.0"},
            },
        )

    # --------------------------------------------------------------------- api
    def list_tools(self) -> list[dict[str, Any]]:
        reply = self._rpc("tools/list", {})
        return reply.get("result", {}).get("tools", [])

    def call(self, tool: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
        reply = self._rpc("tools/call", {"name": tool, "arguments": dict(arguments or {})})
        result = reply.get("result") or {}
        structured = result.get("structuredContent") or {}
        content = result.get("content") or []
        text = "\n".join(block.get("text", "") for block in content if isinstance(block, dict))
        if not structured:
            raise McdTransportError(f"{tool} 没有返回 structuredContent，无法作为数据使用")
        if structured.get("success") is False:
            raise McdBusinessError(
                tool,
                structured.get("code"),
                str(structured.get("message") or "unknown business error"),
                structured.get("traceId"),
            )
        return ToolResult(tool=tool, structured=structured, text=text, raw=reply)

    # ---------------------------------------------------------------- high level
    def nearby_stores(self, city: str, keyword: str, *, be_type: int = 1, search_type: int = 2) -> list[dict[str, Any]]:
        result = self.call(
            "query-nearby-stores",
            {"beType": be_type, "searchType": search_type, "city": city, "keyword": keyword},
        )
        return list(result.data or [])

    def now(self) -> dict[str, Any]:
        return dict(self.call("now-time-info").data or {})

    def stats(self) -> dict[str, int]:
        return {"requests": self._request_count}


__all__ = [
    "DEFAULT_TOKEN_ENV",
    "DEFAULT_URL",
    "McdAuthError",
    "McdBusinessError",
    "McdMCPClient",
    "McdMCPError",
    "McdRateLimitError",
    "McdTransportError",
    "ToolResult",
]
