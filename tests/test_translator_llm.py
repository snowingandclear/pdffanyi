"""Translator LLM 引擎单元/集成测试.

集成测试使用本地模拟的 OpenAI 兼容服务器, 不依赖真实 API key。
运行: python -m pytest tests/ 或 python tests/test_translator_llm.py
"""
import json
import re
import sys
import threading
import time
import builtins
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pdf_translate.translator import Translator  # noqa: E402

MOCK_MODEL = "mock-llm"
MOCK_KEY = "mock-key-123"


class MockLLMHandler(BaseHTTPRequestHandler):
    requests_log = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        MockLLMHandler.requests_log.append(body)
        user_text = next(
            (m.get("content", "") for m in body.get("messages", [])
             if m.get("role") == "user"),
            "",
        )
        lines = [l for l in user_text.splitlines() if l.strip()]
        out = []
        for l in lines:
            m = re.match(r"^(\d+)\. (.*)$", l)
            if m:
                idx, text = m.group(1), m.group(2)
                if "FAIL" in text:
                    out.append(f"漏号行: {text}")
                else:
                    out.append(f"{idx}. 译文: {text}")
            else:
                out.append(f"单行译: {l}")
        content = "\n".join(out)
        resp = {
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": MOCK_MODEL,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 10,
                "total_tokens": 20,
            },
        }
        data = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


class MockServer:
    def __init__(self):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockLLMHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    @property
    def base_url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}/v1"

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


def make_llm() -> Translator:
    return Translator(
        engine="llm",
        api_key=MOCK_KEY,
        base_url="http://127.0.0.1:1/v1",
        model=MOCK_MODEL,
        temperature=0.0,
    )


def test_parse_batch_json_list():
    t = Translator()
    content = json.dumps(["译文a", "译文b", "译文c"])
    assert t._parse_batch(content, 3) == ["译文a", "译文b", "译文c"]


def test_parse_batch_json_dict():
    t = Translator()
    content = json.dumps({"0": "甲", "1": "乙", "2": "丙"})
    assert t._parse_batch(content, 3) == ["甲", "乙", "丙"]


def test_parse_batch_numbered_lines():
    t = Translator()
    content = "0. 甲\n1. 乙\n2. 丙"
    assert t._parse_batch(content, 3) == ["甲", "乙", "丙"]


def test_parse_batch_plain_lines():
    t = Translator()
    content = "甲\n乙\n丙"
    assert t._parse_batch(content, 3) == ["甲", "乙", "丙"]


def test_parse_batch_empty():
    t = Translator()
    assert t._parse_batch("", 3) == ["", "", ""]


def test_no_key_falls_back_to_google():
    import os
    saved = os.environ.pop("LLM_API_KEY", None)
    try:
        t = Translator(engine="llm", api_key="", max_batch_chars=2000,
                       max_batch_lines=40)
    finally:
        if saved is not None:
            os.environ["LLM_API_KEY"] = saved
    assert t.engine == "google"
    assert t.max_batch_chars == 400
    assert t.max_batch_lines == 12


def test_llm_engine_sets_params():
    t = Translator(engine="llm", api_key="k", base_url="http://x/v1",
                   model="m", max_batch_chars=1234, max_batch_lines=12)
    assert t.engine == "llm"
    assert t.max_batch_chars == 1234
    assert t.max_batch_lines == 12


def test_large_batch_split_and_alignment():
    """LLM 引擎: 多批次请求 + 行号对齐正确。"""
    with MockServer() as srv:
        t = Translator(engine="llm", api_key=MOCK_KEY, base_url=srv.base_url,
                       model=MOCK_MODEL, temperature=0.0,
                       max_batch_chars=10**9, max_batch_lines=40)
        lines = [f"原文行{i}" for i in range(85)]
        out = t.translate_lines(lines)
        assert len(out) == 85
        batches = len(MockLLMHandler.requests_log)
        assert batches == 3, f"85 行 / 40 行每批应为 3 批, 实际 {batches}"
        for i, zh in enumerate(out):
            assert zh == f"译文: 原文行{i}", f"第 {i} 行错位: {zh!r}"


def test_llm_fallback_on_missing_lines():
    """模拟 LLM 漏译某行(缺编号)时逐行补译生效。"""
    with MockServer() as srv:
        t = Translator(engine="llm", api_key=MOCK_KEY, base_url=srv.base_url,
                       model=MOCK_MODEL, temperature=0.0)
        out = t.translate_lines(["正常行", "FAIL该行"])
        assert out == ["译文: 正常行", "单行译: FAIL该行"], out


def test_system_prompt_mentions_numbering():
    t = Translator(engine="llm", api_key="k", base_url="http://x/v1", model="m")
    prompt = t._llm_system_prompt("日文", "简体中文")
    assert "数字." in prompt and "逐行" in prompt


class MockOpenCodeServer:
    def __init__(self):
        self.sessions = []

    def __enter__(self):
        self.server = ThreadingHTTPServer(
            ("127.0.0.1", 0), MockOpenCodeHandler
        )
        self.server.mock = self
        self.thread = threading.Thread(
            target=self.server.serve_forever, daemon=True
        )
        self.thread.start()
        return self

    @property
    def base_url(self):
        host, port = self.server.server_address
        return f"http://{host}:{port}"

    def __exit__(self, *args):
        self.server.shutdown()
        self.server.server_close()


class MockOpenCodeHandler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def _json(self, code, obj):
        data = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        if self.path == "/session":
            sid = f"sess-{len(self.server.mock.sessions)}"
            self.server.mock.sessions.append(sid)
            self._json(200, {"id": sid, "title": ""})
            return
        if self.path.endswith("/message"):
            user_text = ""
            for part in body.get("parts", []):
                if isinstance(part, dict) and part.get("type") == "text":
                    user_text += part.get("text", "")
            lines = [l for l in user_text.splitlines() if l.strip()]
            out = []
            for l in lines:
                m = re.match(r"^(\d+)\. (.*)$", l)
                out.append(f"{m.group(1)}. oc译文: {m.group(2)}" if m else f"单行: {l}")
            self._json(200, {
                "parts": [{"type": "text", "text": "\n".join(out)}],
                "info": {},
            })
            return
        self._json(404, {"error": "not found"})

    def do_DELETE(self):
        if self.path.startswith("/session/"):
            self._json(200, True)
            return
        self._json(404, {"error": "not found"})


def test_opencode_engine_direct():
    with MockOpenCodeServer() as srv:
        t = Translator(engine="opencode", opencode_url=srv.base_url)
        out = t.translate_lines(["甲行", "乙行"])
        assert out == ["oc译文: 甲行", "oc译文: 乙行"], out


def test_llm_failure_switches_to_opencode_fallback():
    with MockOpenCodeServer() as srv:
        t = Translator(
            engine="llm",
            api_key="bad-key",
            base_url="http://127.0.0.1:9/v1",  # 必然连接失败
            model="x",
            fallback_engine="opencode",
            opencode_url=srv.base_url,
        )
        out = t.translate_lines(["甲行", "乙行"])
        assert out == ["oc译文: 甲行", "oc译文: 乙行"], out
        assert len(srv.sessions) >= 1


def test_prompt_engine_llm_with_key():
    import config as cfg
    from pdf_translate.pipeline import _prompt_engine

    answers = iter(["3", "sk-test-xxx", "", ""])  # llm, key, 默认 base, 默认 model
    orig_input = builtins.input
    builtins.input = lambda prompt="": next(answers)
    try:
        kw = _prompt_engine(cfg)
    finally:
        builtins.input = orig_input
    assert kw["engine"] == "llm"
    assert kw["api_key"] == "sk-test-xxx"
    assert kw["base_url"]
    assert kw["model"]


def test_prompt_engine_google_no_key():
    import config as cfg
    from pdf_translate.pipeline import _prompt_engine

    answers = iter(["1"])
    orig_input = builtins.input
    builtins.input = lambda prompt="": next(answers)
    try:
        kw = _prompt_engine(cfg)
    finally:
        builtins.input = orig_input
    assert kw == {"engine": "google"}


if __name__ == "__main__":
    failures = 0
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"PASS {name}")
            except Exception as e:
                failures += 1
                print(f"FAIL {name}: {e}")
    sys.exit(1 if failures else 0)