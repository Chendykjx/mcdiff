import json
import sys
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcdiff.mcp import (  # noqa: E402
    McdAuthError,
    McdBusinessError,
    McdMCPClient,
    McdTransportError,
)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):  # silence
        pass

    def _send(self, status: int, body: str, content_type: str = "application/json") -> None:
        data = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        payload = json.loads(self.rfile.read(length) or b"{}")
        responder = self.server.responder  # type: ignore[attr-defined]
        status, body, content_type = responder(payload, self.headers)
        self._send(status, body, content_type)


class Serve:
    def __init__(self, responder):
        self.responder = responder

    def __enter__(self) -> str:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.httpd.responder = self.responder  # type: ignore[attr-defined]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        host, port = self.httpd.server_address
        return f"http://{host}:{port}"

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)


def ok(payload, **result):
    return (200, json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": result}), "application/json")


class ClientTests(unittest.TestCase):
    def test_missing_token_raises(self):
        with self.assertRaises(McdAuthError):
            McdMCPClient(token="")

    def test_initialize_and_list_tools(self):
        def responder(payload, headers):
            if payload["method"] == "initialize":
                return ok(payload, protocolVersion="2025-03-26", serverInfo={"name": "mcd-mcp"})
            if payload["method"] == "tools/list":
                return ok(payload, tools=[{"name": "query-meals"}, {"name": "calculate-price"}])
            return ok(payload, content=[{"type": "text", "text": "hi"}], structuredContent={"success": True, "data": {}})

        with Serve(responder) as url:
            client = McdMCPClient(token="t", url=url, min_interval=0)
            tools = client.list_tools()
        self.assertEqual([tool["name"] for tool in tools], ["query-meals", "calculate-price"])

    def test_call_returns_structured_and_text(self):
        def responder(payload, headers):
            if payload["method"] == "initialize":
                return ok(payload, protocolVersion="2025-03-26")
            return ok(
                payload,
                content=[{"type": "text", "text": "### hello"}],
                structuredContent={"success": True, "code": 200, "data": {"x": 1}, "traceId": "abc"},
            )

        with Serve(responder) as url:
            client = McdMCPClient(token="t", url=url, min_interval=0)
            result = client.call("now-time-info")
        self.assertEqual(result.data, {"x": 1})
        self.assertEqual(result.text, "### hello")
        self.assertEqual(result.trace_id, "abc")

    def test_business_error_is_raised(self):
        def responder(payload, headers):
            if payload["method"] == "initialize":
                return ok(payload, protocolVersion="2025-03-26")
            return ok(
                payload,
                content=[{"type": "text", "text": "门店可能已关闭"}],
                structuredContent={"success": False, "code": 600057, "message": "门店可能已关闭或不在营业时间"},
            )

        with Serve(responder) as url:
            client = McdMCPClient(token="t", url=url, min_interval=0)
            with self.assertRaises(McdBusinessError) as ctx:
                client.call("query-meals", {"storeCode": "1"})
        self.assertEqual(ctx.exception.code, 600057)
        self.assertIn("营业时间", str(ctx.exception))

    def test_401_becomes_auth_error(self):
        def responder(payload, headers):
            return (401, "当前鉴权码不存在或已失效!", "text/plain")

        with Serve(responder) as url:
            with self.assertRaises(McdAuthError):
                McdMCPClient(token="bad", url=url, min_interval=0)

    def test_403_becomes_auth_error(self):
        def responder(payload, headers):
            return (403, '{"code":"0001","msg":"系统错误"}', "application/json")

        with Serve(responder) as url:
            with self.assertRaises(McdAuthError):
                McdMCPClient(token="bad", url=url, min_interval=0)

    def test_sse_framed_response_is_decoded(self):
        def responder(payload, headers):
            body = json.dumps({"jsonrpc": "2.0", "id": payload.get("id"), "result": {"ok": True}})
            if payload["method"] == "initialize":
                return ok(payload, protocolVersion="x")
            return (200, f"event: message\ndata: {body}\n\n", "text/event-stream")

        with Serve(responder) as url:
            client = McdMCPClient(token="t", url=url, min_interval=0)
            reply = client._rpc("tools/list", {})  # noqa: SLF001 - testing the wire layer
        self.assertTrue(reply["result"]["ok"])

    def test_retry_on_429_then_success(self):
        calls = {"n": 0}

        def responder(payload, headers):
            calls["n"] += 1
            if payload["method"] == "initialize":
                return ok(payload, protocolVersion="x")
            if calls["n"] == 2:
                return (429, "slow down", "text/plain")
            return ok(payload, content=[{"text": "ok"}], structuredContent={"success": True, "data": 1})

        with Serve(responder) as url:
            client = McdMCPClient(token="t", url=url, min_interval=0, max_retries=2)
            result = client.call("now-time-info")
        self.assertEqual(result.data, 1)

    def test_non_json_reply_is_transport_error(self):
        def responder(payload, headers):
            if payload["method"] == "initialize":
                return ok(payload, protocolVersion="x")
            return (200, "<html>nope</html>", "text/html")

        with Serve(responder) as url:
            client = McdMCPClient(token="t", url=url, min_interval=0, max_retries=0)
            with self.assertRaises(McdTransportError):
                client.call("now-time-info")

    def test_missing_structured_content_is_error(self):
        def responder(payload, headers):
            if payload["method"] == "initialize":
                return ok(payload, protocolVersion="x")
            return ok(payload, content=[{"text": "只有 markdown"}])

        with Serve(responder) as url:
            client = McdMCPClient(token="t", url=url, min_interval=0)
            with self.assertRaises(McdTransportError):
                client.call("query-meals")

    def test_rate_limiter_spaces_requests(self):
        def responder(payload, headers):
            return ok(payload, protocolVersion="x", tools=[])

        with Serve(responder) as url:
            client = McdMCPClient(token="t", url=url, min_interval=0.15)
            start = time.monotonic()
            client.list_tools()
            client.list_tools()
            elapsed = time.monotonic() - start
        # three requests total (initialize + 2) → at least two gaps
        self.assertGreaterEqual(elapsed, 0.25)

    def test_nearby_stores_wraps_payload(self):
        def responder(payload, headers):
            if payload["method"] == "initialize":
                return ok(payload, protocolVersion="x")
            return ok(
                payload,
                content=[{"text": "stores"}],
                structuredContent={"success": True, "data": [{"storeCode": "1420576"}]},
            )

        with Serve(responder) as url:
            client = McdMCPClient(token="t", url=url, min_interval=0)
            stores = client.nearby_stores("深圳市", "麦当劳")
        self.assertEqual(stores[0]["storeCode"], "1420576")


if __name__ == "__main__":
    unittest.main()
