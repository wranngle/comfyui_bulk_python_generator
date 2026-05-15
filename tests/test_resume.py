"""Resume-on-failure checkpoint tests.

Central promise: a batch interrupted after K iterations and resumed
processes exactly the remaining (N - K), not the original N.

The heavy graph step (`pipeline.run_one`) is mocked — checkpoint logic
is the system under test, not ffmpeg.
"""
from __future__ import annotations
import json
from pathlib import Path

import pytest

from comfybulk import pipeline
from comfybulk.checkpoint import Checkpoint, iteration_key, CHECKPOINT_FILENAME


class _KillAfter:
    """Stub run_one that returns deterministic outputs and raises after `n` calls."""

    def __init__(self, kill_after: int | None = None):
        self.kill_after = kill_after
        self.calls: list[tuple[str, str, dict]] = []

    def __call__(self, variant, source, cfg, **kwargs):
        if self.kill_after is not None and len(self.calls) >= self.kill_after:
            raise RuntimeError("simulated mid-batch failure")
        self.calls.append((variant, source, kwargs))
        out = f"{source}/finals/{variant}_{len(self.calls)}.mp4"
        return [out]


@pytest.fixture
def src_dir(tmp_path: Path) -> Path:
    d = tmp_path / "src"
    d.mkdir()
    return d


def test_killed_run_records_partial_progress(src_dir, monkeypatch):
    killer = _KillAfter(kill_after=5)
    monkeypatch.setattr(pipeline, "run_one", killer)

    with pytest.raises(RuntimeError, match="simulated"):
        pipeline.run(["single"], str(src_dir), quantity=10, cfg=object())

    ledger = src_dir / CHECKPOINT_FILENAME
    assert ledger.is_file(), "checkpoint ledger should be written eagerly"

    ckpt = Checkpoint.for_dir(src_dir).load()
    assert len(ckpt.processed) == 5
    for i in range(5):
        assert ckpt.is_processed("single", i)
    for i in range(5, 10):
        assert not ckpt.is_processed("single", i)


def test_resume_processes_only_remaining(src_dir, monkeypatch):
    killer = _KillAfter(kill_after=5)
    monkeypatch.setattr(pipeline, "run_one", killer)
    with pytest.raises(RuntimeError):
        pipeline.run(["single"], str(src_dir), quantity=10, cfg=object())
    assert len(killer.calls) == 5

    resumer = _KillAfter(kill_after=None)
    monkeypatch.setattr(pipeline, "run_one", resumer)
    outs = pipeline.run(["single"], str(src_dir), quantity=10, cfg=object(), resume=True)

    assert len(resumer.calls) == 5, "resume must only process the remaining 5, not all 10"
    assert len(outs) == 5

    ckpt = Checkpoint.for_dir(src_dir).load()
    assert len(ckpt.processed) == 10
    for i in range(10):
        assert ckpt.is_processed("single", i)


def test_resume_on_clean_ledger_runs_everything(src_dir, monkeypatch):
    runner = _KillAfter(kill_after=None)
    monkeypatch.setattr(pipeline, "run_one", runner)
    outs = pipeline.run(["single"], str(src_dir), quantity=3, cfg=object(), resume=True)
    assert len(runner.calls) == 3
    assert len(outs) == 3


def test_fresh_run_clobbers_old_ledger(src_dir, monkeypatch):
    first = _KillAfter(kill_after=None)
    monkeypatch.setattr(pipeline, "run_one", first)
    pipeline.run(["single"], str(src_dir), quantity=4, cfg=object())

    second = _KillAfter(kill_after=None)
    monkeypatch.setattr(pipeline, "run_one", second)
    pipeline.run(["single"], str(src_dir), quantity=4, cfg=object(), resume=False)

    assert len(second.calls) == 4, "non-resume run must re-process everything"


def test_mark_processed_is_idempotent(src_dir):
    ckpt = Checkpoint.for_dir(src_dir).start_run(
        source=str(src_dir), variants=["single"], quantity=2,
    )
    ckpt.mark_processed("single", 0, outputs=["a.mp4"])
    ckpt.mark_processed("single", 0, outputs=["a.mp4"])  # duplicate
    ckpt.mark_processed("single", 0, outputs=["a-different.mp4"])  # still duplicate

    reloaded = Checkpoint.for_dir(src_dir).load()
    assert len(reloaded.processed) == 1
    iteration_records = [
        l for l in (src_dir / CHECKPOINT_FILENAME).read_text().splitlines()
        if l and json.loads(l).get("event") == "iteration_done"
    ]
    assert len(iteration_records) == 1


def test_remaining_computes_diff_across_variants(src_dir):
    ckpt = Checkpoint.for_dir(src_dir).start_run(
        source=str(src_dir), variants=["single", "grid"], quantity=2,
    )
    ckpt.mark_processed("single", 0, outputs=[])
    ckpt.mark_processed("grid", 1, outputs=[])
    remaining = ckpt.remaining(["single", "grid"], 2)
    assert remaining == [("single", 1), ("grid", 0)]


def test_ledger_records_run_header(src_dir):
    ckpt = Checkpoint.for_dir(src_dir).start_run(
        source=str(src_dir), variants=["single", "montage"], quantity=3,
    )
    first = (src_dir / CHECKPOINT_FILENAME).read_text().splitlines()[0]
    rec = json.loads(first)
    assert rec["event"] == "run_start"
    assert rec["variants"] == ["single", "montage"]
    assert rec["quantity"] == 3
    assert rec["source"] == str(src_dir)
    assert ckpt.run_id


def test_iteration_key_format():
    assert iteration_key("single", 0) == "single#0"
    assert iteration_key("grid", 7) == "grid#7"


def test_checkpoint_dir_override(src_dir, tmp_path, monkeypatch):
    """--checkpoint-dir routes the ledger elsewhere (e.g. read-only source)."""
    runner = _KillAfter(kill_after=None)
    monkeypatch.setattr(pipeline, "run_one", runner)
    sidecar = tmp_path / "ckpts"
    pipeline.run(["single"], str(src_dir), quantity=2, cfg=object(),
                 checkpoint_dir=str(sidecar))
    assert (sidecar / CHECKPOINT_FILENAME).is_file()
    assert not (src_dir / CHECKPOINT_FILENAME).exists()
