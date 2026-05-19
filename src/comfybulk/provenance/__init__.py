"""Provenance hash injection for pipeline outputs.

Each final asset gets a `<basename>.prov.json` sidecar with the sha256 of the
bytes plus contextual fields (timestamp, variant, seed, source clips). The
sidecar lives next to the output so downstream consumers can verify integrity
without rehashing the entire pipeline state.

Sidecar JSON is used rather than PNG tEXt because the pipeline emits mp4 video
containers; mp4 has no analogous low-cost metadata slot that survives copy/move
the way a sibling file does.
"""
from __future__ import annotations
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SIDECAR_SUFFIX = ".prov.json"
SCHEMA_VERSION = 1


def sha256_file(path: str | Path, chunk_size: int = 1 << 20) -> str:
    """Return the lowercase hex sha256 of the file at `path`."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk_size)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def sidecar_path(output_path: str | Path) -> Path:
    """Path of the provenance sidecar for a given output file."""
    return Path(output_path).with_suffix(Path(output_path).suffix + SIDECAR_SUFFIX)


def write_sidecar(output_path: str | Path, context: dict[str, Any] | None = None) -> Path:
    """Hash the output file and write a `.prov.json` sidecar next to it.

    `context` is merged into the sidecar under top-level keys (variant, seed,
    source clips, etc.). Returns the sidecar path. The 'provenance' field is
    the canonical 64-char hex sha256 — this is what tests verify.
    """
    out = Path(output_path)
    digest = sha256_file(out)
    payload: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "provenance": digest,
        "algorithm": "sha256",
        "output": out.name,
        "size_bytes": out.stat().st_size,
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if context:
        for k, v in context.items():
            if k not in payload:
                payload[k] = v
    sc = sidecar_path(out)
    sc.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return sc


def write_sidecars(output_paths: list[str], context: dict[str, Any] | None = None) -> list[Path]:
    """Write sidecars for a batch of outputs. Missing files are skipped silently
    so a partial pipeline run still emits provenance for whatever landed."""
    sidecars: list[Path] = []
    for p in output_paths:
        op = Path(p)
        if not op.is_file():
            continue
        try:
            sidecars.append(write_sidecar(op, context))
        except OSError as e:
            print(f"[PROVENANCE] skip {op.name}: {e}")
    return sidecars


def read_sidecar(output_path: str | Path) -> dict[str, Any]:
    """Load a sidecar payload for an output file."""
    return json.loads(sidecar_path(output_path).read_text(encoding="utf-8"))


def verify(output_path: str | Path) -> bool:
    """Return True iff the sidecar's stored hash matches the file's current hash."""
    payload = read_sidecar(output_path)
    stored = payload.get("provenance")
    if not isinstance(stored, str) or len(stored) != 64:
        return False
    return sha256_file(output_path) == stored
