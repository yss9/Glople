"""가짜 OpenAI 호환 서버(design.md 15.1): mlx_lm.server(training/.venv/.../mlx_lm/server.py)의 응답 모양을 흉내 낸다.

표준 라이브러리만 쓴다(GPU·모델 없음). 실제 ChatOpenAI(langchain-openai 1.6.7 → openai 3.24.0 → httpx2)가 HTTP로
왕복하는지, 요청 JSON이 학습 분포 그대로인지 동작 고정 테스트(tests/test_pins_openai_http.py)가 확인한다.

흉내 낸 것(server.py 읽고 맞춤):
- POST /v1/chat/completions, /chat/completions. Content-Length가 없으면 411, JSON이 아니면 400
- 비스트리밍: {"id": "chatcmpl-<uuid>", "object": "chat.completion", "model": 요청 model, "choices": [{"index": 0,
  "finish_reason", "message": {"role": "assistant", "content": 글 또는 null, "tool_calls"?}}], "usage"}
- tool_calls 원소: {"function": {"name", "arguments": json.dumps(인자, ensure_ascii=False)}, "type": "function",
  "id": uuid4}. 호출이 있으면 finish_reason "tool_calls", 병렬이면 호출 사이 줄바꿈이 content("\\n")로 남음
- 깨진 호출은 서버가 경고만 남기고 버림 → tool_calls 없음 + finish_reason "tool_calls"(Reply.dropped)
- 스트리밍: text/event-stream, ": keepalive n/m" 주석 줄, "data: {chunk}" (delta에 role·content, tool_calls에 index),
  마지막 청크에 finish_reason, 그리고 "data: [DONE]". HTTP/1.0이라 Content-Length 없이 연결을 닫음
- GET /v1/models, GET /health
- quirk: 응답 없이 끊기(비스트리밍 생성 중 KeyError), 헤더 뒤 끊기(스트리밍 생성 중 KeyError), 느린 응답, HTTP 오류

쓰는 법:
    python scripts/fake_openai_server.py --port 0          # 빈 포트로 띄우고 주소를 찍음(8080·8090·8091 거부)
    server = serve(); server.replies.append(Reply.calls([("search_reviews", {"query": "바나힐"})])); ...
테스트는 같은 프로세스의 스레드로 띄우고(serve(port=0)) server.requests로 받은 요청을 본다.
"""
import argparse
import json
import socket
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

FORBIDDEN_PORTS = frozenset({8080, 8090, 8091})   # Spring·모델 서버 자리(띄우지 않음)
SYSTEM_FINGERPRINT = "fake-mlx-lm-0.0.0"


@dataclass
class Reply:
    """한 요청에 대한 서버 답."""

    content: str | None = None
    tool_calls: list[tuple[str, Any]] = field(default_factory=list)   # (이름, 인자 dict 또는 그대로 보낼 문자열)
    finish_reason: str | None = None
    kind: str = "ok"            # ok | drop | disconnect | http_error
    status: int = 200
    delay: float = 0.0          # 답하기 전에 쉬는 초(타임아웃 시험)
    keepalive: int = 0          # 스트리밍 앞에 보낼 ': keepalive' 주석 줄 수

    @classmethod
    def text(cls, content: str, finish_reason: str = "stop", **kw: Any) -> "Reply":
        return cls(content=content, finish_reason=finish_reason, **kw)

    @classmethod
    def calls(cls, calls: list[tuple[str, Any]], **kw: Any) -> "Reply":
        # 병렬이면 블록 사이 줄바꿈이 글로 남는다(server.py 텍스트 상태 기계)
        content = "\n" * (len(calls) - 1) if len(calls) > 1 else None
        return cls(content=content, tool_calls=list(calls), finish_reason="tool_calls", **kw)

    @classmethod
    def dropped(cls) -> "Reply":
        return cls(content=None, finish_reason="tool_calls")   # JSON이 깨져 서버가 호출을 버림

    @classmethod
    def length(cls, content: str = "") -> "Reply":
        return cls(content=content or None, finish_reason="length")

    @classmethod
    def drop(cls) -> "Reply":
        """아무것도 보내지 않고 끊음. mlx 비스트리밍 경로는 헤더를 버퍼에만 두고 생성이 끝나야 보내므로,
        생성 중 예외(KeyError)가 나면 클라이언트는 응답 없이 끊긴 연결을 본다."""
        return cls(kind="drop")

    @classmethod
    def disconnect(cls) -> "Reply":
        """200 헤더까지 보내고 끊음. mlx 스트리밍 경로(헤더를 먼저 보냄)에서 생성 중 예외가 난 경우."""
        return cls(kind="disconnect")

    @classmethod
    def http_error(cls, status: int = 500, message: str = "boom") -> "Reply":
        return cls(kind="http_error", status=status, content=message)


Script = Callable[[dict], Reply]


def _tool_call(name: str, args: Any, index: int | None = None) -> dict:
    raw = args if isinstance(args, str) else json.dumps(args, ensure_ascii=False)
    out: dict[str, Any] = {"function": {"name": name, "arguments": raw}, "type": "function", "id": str(uuid.uuid4())}
    if index is not None:
        out["index"] = index
    return out


class FakeOpenAIServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], script: Script | None = None) -> None:
        super().__init__(address, _Handler)
        self.replies: deque[Reply] = deque()     # 앞에서부터 하나씩 씀. 비면 script
        self.script = script
        self.requests: list[dict] = []           # {"path", "headers", "body", "raw"}
        self.lock = threading.Lock()

    @property
    def base_url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/v1"

    def next_reply(self, body: dict) -> Reply:
        with self.lock:
            if self.replies:
                return self.replies.popleft()
        if self.script is not None:
            return self.script(body)
        return Reply.text("ok")


class _Handler(BaseHTTPRequestHandler):
    server: FakeOpenAIServer
    protocol_version = "HTTP/1.0"   # mlx_lm.server(BaseHTTPRequestHandler 기본값)와 같게

    def log_message(self, format: str, *args: Any) -> None:   # 테스트 출력을 어지럽히지 않음
        pass

    def _send_json(self, status: int, obj: Any) -> None:
        data = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:
        if self.path.startswith("/v1/models"):
            self._send_json(200, {"object": "list", "data": [{"id": "default_model", "object": "model",
                                                               "created": int(time.time())}]})
        elif self.path == "/health":
            self._send_json(200, {"status": "ok"})
        else:
            self._send_json(404, {"error": "Not Found"})

    def do_POST(self) -> None:
        if self.path not in ("/v1/chat/completions", "/chat/completions"):
            self._send_json(404, {"error": "Not Found"})
            return
        length = self.headers.get("Content-Length")
        if length is None:
            self._send_json(411, {"error": "Content-Length header is required"})
            return
        raw = self.rfile.read(int(length))
        try:
            body = json.loads(raw.decode())
        except (ValueError, UnicodeDecodeError) as e:
            self._send_json(400, {"error": f"Invalid JSON in request body: {e}"})
            return
        headers = {k.lower(): v for k, v in self.headers.items()}
        with self.server.lock:
            self.server.requests.append({"path": self.path, "headers": headers, "body": body, "raw": raw})
        reply = self.server.next_reply(body)
        if reply.delay:
            time.sleep(reply.delay)
        if reply.kind == "http_error":
            self._send_json(reply.status, {"error": reply.content})
            return
        if reply.kind == "drop":   # 응답 없이 끊음(비스트리밍 생성 중 예외)
            self.close_connection = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
        if reply.kind == "disconnect":   # 헤더까지 보내고 끊음(스트리밍 생성 중 예외)
            self.send_response(200)
            self.send_header("Content-type", "text/event-stream" if body.get("stream") else "application/json")
            self.end_headers()
            self.wfile.flush()
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            return
        if body.get("stream"):
            self._stream(body, reply)
        else:
            self._complete(body, reply)

    def _base(self, body: dict, object_type: str) -> dict:
        return {"id": f"chatcmpl-{uuid.uuid4()}", "system_fingerprint": SYSTEM_FINGERPRINT, "object": object_type,
                "model": body.get("model", "default_model"), "created": int(time.time())}

    def _complete(self, body: dict, reply: Reply) -> None:
        message: dict[str, Any] = {"role": "assistant", "content": reply.content if reply.content else None}
        if reply.tool_calls:
            message["tool_calls"] = [_tool_call(n, a) for n, a in reply.tool_calls]
        prompt_tokens = sum(len(str(m.get("content") or "")) for m in body.get("messages") or [])
        completion_tokens = len(reply.content or "") + 10 * len(reply.tool_calls)
        self._send_json(200, {**self._base(body, "chat.completion"),
                              "choices": [{"index": 0, "finish_reason": reply.finish_reason or "stop",
                                           "message": message}],
                              "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                                        "total_tokens": prompt_tokens + completion_tokens}})

    def _stream(self, body: dict, reply: Reply) -> None:
        self.send_response(200)
        self.send_header("Content-type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        for i in range(reply.keepalive):
            self.wfile.write(f": keepalive {i + 1}/{reply.keepalive}\n\n".encode())
            self.wfile.flush()

        def chunk(delta_extra: dict, finish: str | None) -> None:
            obj = {**self._base(body, "chat.completion.chunk"),
                   "choices": [{"index": 0, "finish_reason": finish, "delta": {"role": "assistant", **delta_extra}}]}
            self.wfile.write(f"data: {json.dumps(obj)}\n\n".encode())
            self.wfile.flush()

        text = reply.content or ""
        for ch in text if not reply.tool_calls else []:   # 글은 한 글자씩(토큰 흉내)
            chunk({"content": ch}, None)
        calls = [_tool_call(n, a, index=i) for i, (n, a) in enumerate(reply.tool_calls)]
        if calls:   # 서버는 호출 블록이 끝난 뒤 그 사이 글과 함께 한 번에 낸다
            chunk({"content": text, "tool_calls": calls} if text else {"tool_calls": calls}, None)
        chunk({}, reply.finish_reason or "stop")
        self.wfile.write(b"data: [DONE]\n\n")
        self.wfile.flush()


def serve(port: int = 0, host: str = "127.0.0.1", script: Script | None = None) -> FakeOpenAIServer:
    """스레드로 띄운 서버를 돌려준다. 끝나면 server.shutdown(); server.server_close()."""
    if port in FORBIDDEN_PORTS:
        raise ValueError(f"{port}번은 Spring·모델 서버 자리라 쓰지 않습니다")
    if host not in ("127.0.0.1", "localhost", "::1"):
        raise ValueError("가짜 서버는 로컬 주소에만 띄웁니다")
    server = FakeOpenAIServer((host, port), script)
    threading.Thread(target=server.serve_forever, name="fake-openai", daemon=True).start()
    return server


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="mlx_lm.server 모양을 흉내 내는 가짜 OpenAI 호환 서버(표준 라이브러리)")
    p.add_argument("--port", type=int, default=0, help="0이면 빈 포트(8080·8090·8091은 거부)")
    p.add_argument("--host", default="127.0.0.1")
    args = p.parse_args(argv)
    server = serve(args.port, args.host)
    print(f"가짜 OpenAI 서버: {server.base_url} (Ctrl+C로 끝)", flush=True)
    try:
        while True:
            time.sleep(3600)
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
