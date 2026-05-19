"""Append-only provenance ledger tests.

Central promise: two bulk operations produce exactly two ledger.jsonl entries,
each carrying the full schema (input_hash, recipe, output_hash, model_version,
timestamp). Mock-model substitution avoids ffmpeg/ollama dependency.

Companion to PR #4's determinism contract: PR #4 proves same-seed runs yield
byte-identical outputs; this ledger turns that contract into a portable
receipt — two ledger entries with matching input_hash + recipe + model_version
should also match on output_hash. The test below asserts that property.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from comfybulk import ledger as ledger_mod
from comfybulk.config import Config, Paths, Ollama, Encode, Font


def _mock_cfg() -> Config:
    return Config(
        paths=Paths(
            assembly_root="", favorites_root="", audio_folder="", neg_audio="",
            cta_folder="", templates="", metadata_csv="", ai_prompts_csv="",
            captions_csv="",
        ),
        ollama=Ollama(host="", model="mock-model:latest", gguf_path="", auto_launch=False),
        encode=Encode(crf=18, preset="veryfast", target_w=1080, target_h=1920, fps=60),
        font=Font(file="", size=120, border_w=36),
    )


# ---- unit: hash determinism ----

def test_hash_inputs_lexicographic_order(tmp_path: Path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"alpha")
    b.write_bytes(b"beta")
    h1 = ledger_mod.hash_inputs([a, b])
    h2 = ledger_mod.hash_inputs([b, a])
    assert h1 == h2
    assert len(h1) == 64


def test_hash_inputs_changes_with_content(tmp_path: Path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"v1")
    h1 = ledger_mod.hash_inputs([f])
    f.write_bytes(b"v2")
    h2 = ledger_mod.hash_inputs([f])
    assert h1 != h2


def test_hash_inputs_missing_files_are_skipped(tmp_path: Path):
    h_empty = ledger_mod.hash_inputs([tmp_path / "ghost.bin"])
    # sha256 of empty byte string
    assert h_empty == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"


# ---- unit: append schema + append-only semantics ----

def test_append_writes_schema_fields(tmp_path: Path):
    path = ledger_mod.append(
        tmp_path,
        input_hash="a" * 64,
        recipe={"variant": "single", "seed": 7},
        output_hash="b" * 64,
        model_version="mock-model/0.0.1",
        timestamp="2026-05-14T22:00:00Z",
    )
    entries = ledger_mod.read_entries(tmp_path)
    assert path.name == "ledger.jsonl"
    assert len(entries) == 1
    e = entries[0]
    for field in ledger_mod.SCHEMA_FIELDS:
        assert field in e, f"missing required field: {field}"
    assert e["input_hash"] == "a" * 64
    assert e["recipe"] == {"variant": "single", "seed": 7}
    assert e["output_hash"] == "b" * 64
    assert e["model_version"] == "mock-model/0.0.1"
    assert e["timestamp"] == "2026-05-14T22:00:00Z"


def test_append_only_two_calls_two_lines(tmp_path: Path):
    for i in range(2):
        ledger_mod.append(
            tmp_path,
            input_hash=str(i) * 64,
            recipe={"variant": "single", "seed": i},
            output_hash=str(i + 5) * 64,
            model_version="mock-model/0.0.1",
            timestamp=f"2026-05-14T22:0{i}:00Z",
        )
    raw = (tmp_path / "ledger.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(raw) == 2
    # Append-only: line 0 unchanged after line 1 is added.
    line0 = json.loads(raw[0])
    assert line0["input_hash"] == "0" * 64
    assert line0["timestamp"] == "2026-05-14T22:00:00Z"


def test_record_run_computes_hashes_from_files(tmp_path: Path):
    src = tmp_path / "src.bin"
    out = tmp_path / "out.bin"
    src.write_bytes(b"source clip bytes")
    out.write_bytes(b"rendered output bytes")
    ledger_mod.record_run(
        tmp_path,
        inputs=[src],
        outputs=[out],
        recipe={"variant": "single", "seed": 1, "no_effects": True},
        model_version="mock-model/0.0.1",
        timestamp="2026-05-14T22:00:00Z",
    )
    [e] = ledger_mod.read_entries(tmp_path)
    assert e["input_hash"] == ledger_mod.hash_inputs([src])
    assert e["output_hash"] == ledger_mod.hash_outputs([out])
    assert e["recipe"]["variant"] == "single"


# ---- integration: two bulk ops -> two ledger entries (mock pipeline) ----

def _fake_run_one(call_idx: int, *, finals: Path, clips: list[Path], cfg) -> str:
    """Stand-in for the heavy pipeline.run_one body. Emits a fake output and
    calls _emit_ledger exactly as the real runner would."""
    from comfybulk.pipeline import _emit_ledger

    out = finals / f"mock_output_{call_idx}.mp4"
    out.write_bytes(f"output-bytes-{call_idx}".encode())
    _emit_ledger(
        finals=finals,
        clips=clips,
        outputs=[str(out)],
        variant="single",
        seed=42 + call_idx,
        no_effects=True,
        cfg=cfg,
        reversal_speed=None,
    )
    return str(out)


def test_two_bulk_operations_produce_two_ledger_entries(tmp_path: Path):
    cfg = _mock_cfg()
    finals = tmp_path / "finals"
    finals.mkdir()

    # Two distinct "bulk operations" — different source clips, same recipe shape.
    clips_a = [tmp_path / "clip_a1.mp4", tmp_path / "clip_a2.mp4"]
    clips_b = [tmp_path / "clip_b1.mp4"]
    for c in clips_a + clips_b:
        c.write_bytes(f"bytes-of-{c.name}".encode())

    _fake_run_one(0, finals=finals, clips=clips_a, cfg=cfg)
    _fake_run_one(1, finals=finals, clips=clips_b, cfg=cfg)

    entries = ledger_mod.read_entries(finals)
    assert len(entries) == 2, "expected exactly 2 ledger entries for 2 bulk ops"
    for e in entries:
        for field in ledger_mod.SCHEMA_FIELDS:
            assert field in e, f"missing required field: {field}"
        assert len(e["input_hash"]) == 64
        assert len(e["output_hash"]) == 64
        assert e["model_version"].startswith("comfybulk-pipeline/")
        assert e["recipe"]["variant"] == "single"
        assert "encode" in e["recipe"]
    # Two operations against different input sets → different input hashes.
    assert entries[0]["input_hash"] != entries[1]["input_hash"]
    assert entries[0]["output_hash"] != entries[1]["output_hash"]


def test_determinism_receipt_matches_pr4_contract(tmp_path: Path):
    """Receipt of the PR #4 determinism contract: same inputs + same recipe +
    same model_version → same output_hash. The ledger captures this as a
    portable, file-free check."""
    from comfybulk.pipeline import _emit_ledger
    cfg = _mock_cfg()
    finals = tmp_path / "finals"
    finals.mkdir()

    clip = tmp_path / "src.mp4"
    clip.write_bytes(b"identical-source-clip")

    # Deterministic mock: same input bytes, same output bytes (the PR #4 promise).
    for run_idx in range(2):
        out = finals / f"run_{run_idx}.mp4"
        out.write_bytes(b"identical-output-bytes")
        _emit_ledger(
            finals=finals,
            clips=[clip],
            outputs=[str(out)],
            variant="single",
            seed=42,
            no_effects=True,
            cfg=cfg,
            reversal_speed=None,
        )

    entries = ledger_mod.read_entries(finals)
    assert len(entries) == 2
    a, b = entries
    assert a["input_hash"] == b["input_hash"]
    assert a["output_hash"] == b["output_hash"]
    assert a["recipe"] == b["recipe"]
    assert a["model_version"] == b["model_version"]


def test_ledger_failure_is_best_effort(tmp_path: Path, monkeypatch, capsys):
    """Pipeline runs must not abort if ledger write fails."""
    from comfybulk import pipeline as pipe_mod
    cfg = _mock_cfg()
    finals = tmp_path / "finals"
    finals.mkdir()

    def boom(*_a, **_kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(pipe_mod.ledger_mod, "record_run", boom)
    # Must not raise; should print a skipped message.
    pipe_mod._emit_ledger(
        finals=finals, clips=[], outputs=[], variant="single",
        seed=1, no_effects=True, cfg=cfg, reversal_speed=None,
    )
    out = capsys.readouterr().out
    assert "[LEDGER]" in out
