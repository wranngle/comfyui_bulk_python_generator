"""Local LLM router.

Abstracts text generation behind a single `LocalLLM` class so callers can swap
between Ollama, llama.cpp (HTTP server in OpenAI-compatible mode), or a no-op
backend without changing call sites.

Backends:
- ``none``     — deterministic empty-string sink. Useful in CI, tests, and any
                 path that wants to disable LLM calls without code changes.
- ``ollama``   — POST /api/generate against an Ollama daemon.
- ``llamacpp`` — POST /completion against a llama.cpp server (the simple
                 built-in HTTP server, not the OpenAI-compatible one).

The class is intentionally small: one ``generate`` method plus a ``caption``
convenience wrapper. Networking failures are translated into ``LocalLLMError``
with a message that names the backend and host so users can diagnose without
reading a stack trace.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import requests


Backend = Literal["ollama", "llamacpp", "none"]
_VALID_BACKENDS: tuple[Backend, ...] = ("ollama", "llamacpp", "none")


class LocalLLMError(RuntimeError):
    """Raised when a backend is unreachable or returns a non-OK response."""


@dataclass
class LocalLLM:
    backend: Backend = "none"
    host: str = "http://127.0.0.1:11434"
    model: str = ""
    timeout: int = 600

    def __post_init__(self) -> None:
        if self.backend not in _VALID_BACKENDS:
            raise ValueError(
                f"unknown backend {self.backend!r}; expected one of {_VALID_BACKENDS}"
            )

    def generate(
        self,
        prompt: str,
        *,
        temperature: float = 0.7,
        top_p: float = 0.9,
        repeat_penalty: float = 1.05,
        num_predict: int = 200,
    ) -> str:
        if self.backend == "none":
            return ""
        if self.backend == "ollama":
            return self._ollama(
                prompt,
                temperature=temperature,
                top_p=top_p,
                repeat_penalty=repeat_penalty,
                num_predict=num_predict,
            )
        if self.backend == "llamacpp":
            return self._llamacpp(
                prompt,
                temperature=temperature,
                top_p=top_p,
                repeat_penalty=repeat_penalty,
                num_predict=num_predict,
            )
        raise ValueError(f"unreachable backend {self.backend!r}")

    def caption(self, image_path: str) -> str:
        """Convenience for caption-style use; ``none`` returns ``""``.

        Non-none backends use the image filename as a prompt seed; callers that
        need real vision should build their own prompt and call ``generate``.
        """
        if self.backend == "none":
            return ""
        return self.generate(f"Write a one-sentence caption for: {image_path}")

    def _ollama(
        self,
        prompt: str,
        *,
        temperature: float,
        top_p: float,
        repeat_penalty: float,
        num_predict: int,
    ) -> str:
        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "top_p": top_p,
                "repeat_penalty": repeat_penalty,
                "num_predict": num_predict,
            },
        }
        try:
            r = requests.post(f"{self.host}/api/generate", json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise LocalLLMError(
                f"ollama backend unreachable at {self.host}: {e}. "
                f"Start the daemon with `ollama serve` or pick a different backend."
            ) from e
        if not r.ok:
            raise LocalLLMError(
                f"ollama backend at {self.host} returned HTTP {r.status_code}: {r.text[:200]}"
            )
        return (r.json().get("response") or "").strip()

    def _llamacpp(
        self,
        prompt: str,
        *,
        temperature: float,
        top_p: float,
        repeat_penalty: float,
        num_predict: int,
    ) -> str:
        body = {
            "prompt": prompt,
            "temperature": temperature,
            "top_p": top_p,
            "repeat_penalty": repeat_penalty,
            "n_predict": num_predict,
            "stream": False,
        }
        try:
            r = requests.post(f"{self.host}/completion", json=body, timeout=self.timeout)
        except requests.RequestException as e:
            raise LocalLLMError(
                f"llamacpp backend unreachable at {self.host}: {e}. "
                f"Start `llama-server` on that host or pick a different backend."
            ) from e
        if not r.ok:
            raise LocalLLMError(
                f"llamacpp backend at {self.host} returned HTTP {r.status_code}: {r.text[:200]}"
            )
        return (r.json().get("content") or "").strip()
