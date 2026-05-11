"""End-to-end integration tests against real media.

Set COMFYBULK_REAL_TEST_CLIP to an MP4 with ComfyUI metadata, then run
`pytest -m integration`.

When running under WSL with Windows ffmpeg.exe, keep COMFYBULK_INTEGRATION_TMP
on a Windows-visible mount such as the repo checkout or /mnt/c.
"""
import os, shutil, uuid
from pathlib import Path

import pytest

from comfybulk.extract import process_file
from comfybulk.ffmpeg import probe_duration, probe_dims, to_posix


pytestmark = pytest.mark.integration


@pytest.fixture
def win_tmp_path(repo_root):
    """A scratch dir visible to the ffmpeg binary used by integration tests."""
    configured = os.environ.get("COMFYBULK_INTEGRATION_TMP")
    base = Path(configured) if configured else repo_root / ".testtmp"
    base.mkdir(parents=True, exist_ok=True)
    d = base / uuid.uuid4().hex[:12]
    d.mkdir()
    yield d
    shutil.rmtree(d, ignore_errors=True)


def test_probe_real_clip(real_test_clip):
    if not real_test_clip:
        pytest.skip("no real test clip available")
    d = probe_duration(real_test_clip)
    assert d > 0
    w, h = probe_dims(real_test_clip)
    assert w > 0 and h > 0


def test_extract_from_real_clip(win_tmp_path, real_test_clip):
    if not real_test_clip:
        pytest.skip("no real test clip available")
    csv_path = str(win_tmp_path / "metadata.csv")
    # Real clips may not include parseable ComfyUI metadata; this is a smoke test.
    result = process_file(real_test_clip, csv_path, test_mode=True)
    assert isinstance(result, bool)


def test_pipeline_single_no_effects(win_tmp_path, real_test_clip):
    """Run the full pipeline on a single clip with --no-effects to keep it fast."""
    if not real_test_clip:
        pytest.skip("no real test clip available")

    src_dir = win_tmp_path / "source"
    src_dir.mkdir()
    staged = src_dir / "input.mp4"
    shutil.copyfile(to_posix(real_test_clip), str(staged))

    from comfybulk.config import load as load_cfg
    from comfybulk.pipeline import run

    cfg = load_cfg()
    cfg.paths.metadata_csv = str(win_tmp_path / "metadata.csv")
    cfg.paths.favorites_root = str(win_tmp_path / "favorites")

    outs = run(["single"], str(staged), quantity=1, cfg=cfg, no_effects=True)
    assert outs, "pipeline produced no outputs"
    for o in outs:
        op = Path(to_posix(o))
        assert op.exists()
        assert op.stat().st_size > 100_000
