"""Platform gating for to_win path conversion.

The package was originally WSL-only; to_win unconditionally rewrote
`/mnt/<drive>/...` into Windows form. On plain Linux or macOS that
mangled real paths. These tests pin the new contract: WSL converts,
everything else passes through unchanged.
"""
from __future__ import annotations

import pytest

from comfybulk import ffmpeg as ffmpeg_mod
from comfybulk.ffmpeg import to_win


@pytest.fixture
def wsl_env(monkeypatch):
    monkeypatch.setattr(ffmpeg_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ffmpeg_mod.sys, "platform", "linux")
    monkeypatch.setattr(ffmpeg_mod, "_is_wsl", lambda: True)


@pytest.fixture
def linux_env(monkeypatch):
    monkeypatch.setattr(ffmpeg_mod.platform, "system", lambda: "Linux")
    monkeypatch.setattr(ffmpeg_mod.sys, "platform", "linux")
    monkeypatch.setattr(ffmpeg_mod, "_is_wsl", lambda: False)


@pytest.fixture
def macos_env(monkeypatch):
    monkeypatch.setattr(ffmpeg_mod.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(ffmpeg_mod.sys, "platform", "darwin")
    monkeypatch.setattr(ffmpeg_mod, "_is_wsl", lambda: False)


def test_wsl_converts_mnt_path_to_windows(wsl_env):
    assert to_win("/mnt/d/foo/bar.mp4") == r"D:\foo\bar.mp4"


def test_linux_passthrough_does_not_convert(linux_env):
    assert to_win("/home/x/foo.mp4") == "/home/x/foo.mp4"


def test_macos_passthrough_does_not_convert(macos_env):
    assert to_win("/Users/x/foo.mp4") == "/Users/x/foo.mp4"
