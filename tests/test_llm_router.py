"""LocalLLM router: backend dispatch, deterministic no-op, clear error on unreachable host."""
from __future__ import annotations

import pytest
import requests

from comfybulk.llm import LocalLLM, LocalLLMError


# ---- backend="none" is deterministic and offline ----

def test_none_caption_returns_empty_string():
    assert LocalLLM(backend="none").caption("foo.png") == ""


def test_none_generate_returns_empty_string():
    assert LocalLLM(backend="none").generate("anything") == ""


def test_none_is_deterministic_across_calls():
    llm = LocalLLM(backend="none")
    assert llm.caption("a.png") == llm.caption("b.png") == llm.generate("xyz") == ""


def test_none_does_not_touch_network(monkeypatch):
    def explode(*a, **k):
        raise AssertionError("backend='none' must not call requests.post")
    monkeypatch.setattr(requests, "post", explode)
    LocalLLM(backend="none").caption("foo.png")


# ---- input validation ----

def test_unknown_backend_raises():
    with pytest.raises(ValueError, match="unknown backend"):
        LocalLLM(backend="banana")  # type: ignore[arg-type]


# ---- backend="ollama" success path (mocked) ----

class _FakeResponse:
    def __init__(self, payload: dict, ok: bool = True, status_code: int = 200, text: str = ""):
        self._payload = payload
        self.ok = ok
        self.status_code = status_code
        self.text = text

    def json(self) -> dict:
        return self._payload


def test_ollama_success_returns_response_field(monkeypatch):
    captured = {}

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["body"] = json
        return _FakeResponse({"response": "  hello world  "})

    monkeypatch.setattr(requests, "post", fake_post)
    out = LocalLLM(backend="ollama", host="http://h:1", model="m").generate("p")
    assert out == "hello world"
    assert captured["url"] == "http://h:1/api/generate"
    assert captured["body"]["model"] == "m"
    assert captured["body"]["stream"] is False


# ---- backend="ollama" failure paths surface a clear error ----

def test_ollama_unreachable_raises_clear_error(monkeypatch):
    def fake_post(*a, **k):
        raise requests.ConnectionError("connection refused")

    monkeypatch.setattr(requests, "post", fake_post)
    llm = LocalLLM(backend="ollama", host="http://127.0.0.1:11434", model="m")
    with pytest.raises(LocalLLMError) as excinfo:
        llm.generate("p")
    msg = str(excinfo.value)
    assert "ollama" in msg
    assert "http://127.0.0.1:11434" in msg
    assert "ollama serve" in msg  # actionable remediation hint


def test_ollama_non_ok_response_raises(monkeypatch):
    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: _FakeResponse({}, ok=False, status_code=500, text="boom"),
    )
    with pytest.raises(LocalLLMError, match="HTTP 500"):
        LocalLLM(backend="ollama", host="http://h:1", model="m").generate("p")


# ---- backend="llamacpp" mirrors the same contract ----

def test_llamacpp_success(monkeypatch):
    monkeypatch.setattr(
        requests, "post",
        lambda *a, **k: _FakeResponse({"content": "abc"}),
    )
    assert LocalLLM(backend="llamacpp", host="http://h:2").generate("p") == "abc"


def test_llamacpp_unreachable_raises_clear_error(monkeypatch):
    def fake_post(*a, **k):
        raise requests.ConnectionError("nope")

    monkeypatch.setattr(requests, "post", fake_post)
    with pytest.raises(LocalLLMError) as excinfo:
        LocalLLM(backend="llamacpp", host="http://h:2").generate("p")
    assert "llamacpp" in str(excinfo.value)
    assert "llama-server" in str(excinfo.value)
