"""Append-only provenance ledger for bulk runs.

One JSON line per completed bulk operation, written under
`<ledger_dir>/ledger.jsonl`. Each line carries the schema:

    input_hash      sha256 of concatenated source-clip bytes (deterministic
                    ordering: lexicographic by absolute path)
    recipe          variant + key encode params + effects toggle
    output_hash     sha256 of concatenated output bytes (lexicographic order)
    model_version   tag identifying the model/recipe binding for this run
    timestamp       ISO-8601 UTC, second precision

Semantics:

* Append-only. Lines are never rewritten or reordered.
* Best-effort on the runner hook side — a ledger write that fails MUST NOT
  abort a pipeline run. The ledger is observability, not gating.
* Companion to PR #4's determinism contract: PR #4 proves that the same seed
  yields byte-identical outputs; the ledger turns each run into a portable
  receipt that lets a third party reproduce the determinism check from
  hashes alone (compare two ledger entries with matching input_hash + recipe
  + model_version: their output_hash should match).

The orthogonal `provenance/` module (PR for round-2 item 3) writes per-output
sidecar JSON for *integrity verification* of a single file. This ledger
captures the *run-level* receipt across all inputs and outputs.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Mapping


SCHEMA_FIELDS = (
    "input_hash",
    "recipe",
    "output_hash",
    "model_version",
    "timestamp",
)

LEDGER_FILENAME = "ledger.jsonl"


def _sha256_of_files(paths: Iterable[str | Path]) -> str:
    """sha256 of concatenated bytes, deterministic by lexicographic abs-path order.

    Missing or non-file entries are skipped silently; the resulting digest is
    over whatever readable files remained. An empty input produces the digest
    of the empty byte string (which is itself well-defined and useful as a
    sentinel: "no inputs read").
    """
    h = hashlib.sha256()
    resolved = sorted(str(Path(p).resolve()) for p in paths)
    for p in resolved:
        try:
            with open(p, "rb") as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    h.update(chunk)
        except (OSError, FileNotFoundError):
            continue
    return h.hexdigest()


def hash_inputs(paths: Iterable[str | Path]) -> str:
    return _sha256_of_files(paths)


def hash_outputs(paths: Iterable[str | Path]) -> str:
    return _sha256_of_files(paths)


def ledger_path(ledger_dir: str | Path) -> Path:
    return Path(ledger_dir) / LEDGER_FILENAME


def append(
    ledger_dir: str | Path,
    *,
    input_hash: str,
    recipe: Mapping[str, object],
    output_hash: str,
    model_version: str,
    timestamp: str | None = None,
) -> Path:
    """Append one entry to the ledger. Returns the ledger file path.

    `timestamp` defaults to now() in UTC, second precision. Caller-supplied
    timestamps are written verbatim so deterministic tests can fix them.
    """
    d = Path(ledger_dir)
    d.mkdir(parents=True, exist_ok=True)
    entry = {
        "input_hash": input_hash,
        "recipe": dict(recipe),
        "output_hash": output_hash,
        "model_version": model_version,
        "timestamp": timestamp or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    line = json.dumps(entry, sort_keys=True, separators=(",", ":"))
    path = ledger_path(d)
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")
    return path


def record_run(
    ledger_dir: str | Path,
    *,
    inputs: Iterable[str | Path],
    outputs: Iterable[str | Path],
    recipe: Mapping[str, object],
    model_version: str,
    timestamp: str | None = None,
) -> Path:
    """Compute hashes from real files and append. Convenience for runner hook."""
    return append(
        ledger_dir,
        input_hash=hash_inputs(inputs),
        recipe=recipe,
        output_hash=hash_outputs(outputs),
        model_version=model_version,
        timestamp=timestamp,
    )


def read_entries(ledger_dir: str | Path) -> list[dict]:
    """Read all ledger entries. Returns [] if absent."""
    path = ledger_path(ledger_dir)
    if not path.exists():
        return []
    out: list[dict] = []
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            raw = raw.strip()
            if raw:
                out.append(json.loads(raw))
    return out


__all__ = [
    "SCHEMA_FIELDS",
    "LEDGER_FILENAME",
    "append",
    "record_run",
    "read_entries",
    "hash_inputs",
    "hash_outputs",
    "ledger_path",
]
