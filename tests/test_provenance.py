"""Provenance hash injection — round-2 item 3.

Acceptance: bulk run emits a sidecar with `provenance: <64 hex>` that matches
the recomputed sha256 of the output bytes.
"""
from __future__ import annotations
import hashlib, json, os
from pathlib import Path

import pytest

from comfybulk import provenance
from comfybulk import pipeline as pipeline_mod


def _sha256_bytes(b: bytes) -> str:
    h = hashlib.sha256()
    h.update(b)
    return h.hexdigest()


def _recompute_sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


# ---------- unit: pure module ----------

def test_sidecar_path_suffix(tmp_path):
    out = tmp_path / "single_cta_audio_20260514.mp4"
    out.write_bytes(b"\x00\x01\x02")
    sc = provenance.sidecar_path(out)
    assert sc.name == "single_cta_audio_20260514.mp4.prov.json"
    assert sc.parent == out.parent


def test_write_sidecar_stores_matching_sha256(tmp_path):
    out = tmp_path / "out.mp4"
    payload = os.urandom(4096)
    out.write_bytes(payload)
    sc = provenance.write_sidecar(out, context={"variant": "single", "seed": 42})
    data = json.loads(sc.read_text(encoding="utf-8"))
    assert data["algorithm"] == "sha256"
    assert isinstance(data["provenance"], str)
    assert len(data["provenance"]) == 64
    assert all(c in "0123456789abcdef" for c in data["provenance"])
    assert data["provenance"] == _sha256_bytes(payload)
    assert data["variant"] == "single"
    assert data["seed"] == 42
    assert data["size_bytes"] == len(payload)


def test_verify_detects_tamper(tmp_path):
    out = tmp_path / "tamper.mp4"
    out.write_bytes(b"original-bytes")
    provenance.write_sidecar(out)
    assert provenance.verify(out) is True
    out.write_bytes(b"tampered-bytes")
    assert provenance.verify(out) is False


def test_write_sidecars_skips_missing(tmp_path):
    real = tmp_path / "real.mp4"
    real.write_bytes(b"hello")
    ghost = tmp_path / "ghost.mp4"
    sidecars = provenance.write_sidecars([str(real), str(ghost)])
    assert len(sidecars) == 1
    assert sidecars[0].name == "real.mp4.prov.json"


def test_context_does_not_clobber_required_fields(tmp_path):
    out = tmp_path / "x.mp4"
    out.write_bytes(b"abc")
    provenance.write_sidecar(out, context={
        "provenance": "0" * 64,
        "algorithm": "md5",
        "variant": "grid",
    })
    data = provenance.read_sidecar(out)
    assert data["provenance"] == _sha256_bytes(b"abc")
    assert data["algorithm"] == "sha256"
    assert data["variant"] == "grid"


# ---------- integration: pipeline emits sidecars ----------

def _stub_clean_single(input_path, finals_dir, ts, **kwargs):
    """Stand in for variants.clean_single — drop a small mp4-ish file."""
    out_a = Path(finals_dir) / f"single_cta_{ts}.mp4"
    out_b = Path(finals_dir) / f"single_nocta_{ts}.mp4"
    out_a.write_bytes(b"FAKE-MP4-CTA-" + os.urandom(64))
    out_b.write_bytes(b"FAKE-MP4-NOCTA-" + os.urandom(64))
    return [str(out_a), str(out_b)]


def test_pipeline_no_effects_writes_provenance_sidecars(tmp_path, monkeypatch):
    """End-to-end-ish: drive run_one with --no-effects, stub the heavy variant
    builder, prove each output gets a sidecar whose `provenance` matches the
    recomputed sha256 of the file bytes."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    clip = src_dir / "clip_seed1234567890_a.mp4"
    clip.write_bytes(b"fake-source-clip")

    monkeypatch.setattr(pipeline_mod.variants, "clean_single", _stub_clean_single)
    monkeypatch.setattr(pipeline_mod, "_select_clips", lambda *a, **k: [clip])
    monkeypatch.setattr(pipeline_mod, "extract_one", lambda *a, **k: True)

    from comfybulk.config import Config, Paths, Encode, Font, Ollama
    cfg = Config(
        paths=Paths(
            assembly_root=str(tmp_path / "assembly"),
            favorites_root=str(tmp_path / "favorites"),
            audio_folder=str(tmp_path / "audio"),
            neg_audio=str(tmp_path / "neg"),
            cta_folder=str(tmp_path / "cta"),
            templates=str(tmp_path / "tpl"),
            metadata_csv=str(tmp_path / "metadata.csv"),
            ai_prompts_csv=str(tmp_path / "ai.csv"),
            captions_csv=str(tmp_path / "captions.csv"),
        ),
        ollama=Ollama(host="http://localhost:11434", model="x", gguf_path=""),
        encode=Encode(crf=23, preset="fast", target_w=1080, target_h=1920, fps=30),
        font=Font(file=str(tmp_path / "font.ttf"), size=72, border_w=4),
    )

    outs = pipeline_mod.run_one("single", str(clip), cfg, no_effects=True, seed=42)
    assert len(outs) == 2
    for o in outs:
        op = Path(o)
        assert op.exists(), f"missing output {op}"
        sc = provenance.sidecar_path(op)
        assert sc.exists(), f"missing sidecar for {op}"
        payload = json.loads(sc.read_text(encoding="utf-8"))
        assert "provenance" in payload
        h = payload["provenance"]
        assert isinstance(h, str) and len(h) == 64
        assert h == _recompute_sha256(op), f"sha256 mismatch for {op.name}"
        assert payload["variant"] == "single"
        assert payload["seed"] == 42
        assert payload["no_effects"] is True
