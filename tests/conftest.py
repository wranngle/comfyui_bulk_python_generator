import os, shutil
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def has_ffmpeg() -> bool:
    return shutil.which("ffmpeg.exe") is not None or shutil.which("ffmpeg") is not None


@pytest.fixture
def tmp_workdir(tmp_path):
    yield tmp_path


@pytest.fixture(scope="session")
def real_test_clip() -> str | None:
    """A small real ComfyUI-generated clip for integration tests.

    Set COMFYBULK_REAL_TEST_CLIP to opt into tests that need real media.
    """
    c = os.environ.get("COMFYBULK_REAL_TEST_CLIP")
    if c and Path(c).exists():
        return c
    return None
