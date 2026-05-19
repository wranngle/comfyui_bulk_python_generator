"""Thread-safe progress state for the dashboard.

`ProgressState` is the in-memory ledger the pipeline updates and the HTTP
server reads. ETA is derived from elapsed time per processed item; it is
intentionally simple (linear extrapolation) so the dashboard stays honest
when item durations vary — agents should treat it as a hint, not a contract.
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class ProgressState:
    total: int = 0
    processed: int = 0
    failed: int = 0
    started_at: float | None = None
    finished_at: float | None = None
    current: str | None = None
    last_output: str | None = None
    run_id: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def mark_started(self, total: int, run_id: str | None = None) -> None:
        with self._lock:
            self.total = max(int(total), 0)
            self.processed = 0
            self.failed = 0
            self.started_at = time.time()
            self.finished_at = None
            self.current = None
            self.last_output = None
            self.run_id = run_id

    def mark_current(self, name: str | None) -> None:
        with self._lock:
            self.current = name

    def mark_item_done(self, output: str | None = None) -> None:
        with self._lock:
            self.processed += 1
            if output is not None:
                self.last_output = str(output)
            self.current = None

    def mark_item_failed(self) -> None:
        with self._lock:
            self.failed += 1
            self.current = None

    def mark_finished(self) -> None:
        with self._lock:
            self.finished_at = time.time()
            self.current = None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            started = self.started_at
            finished = self.finished_at
            processed = self.processed
            total = self.total
            failed = self.failed
            current = self.current
            last_output = self.last_output
            run_id = self.run_id

        elapsed = 0.0
        if started is not None:
            ref = finished if finished is not None else now
            elapsed = max(ref - started, 0.0)

        eta = _compute_eta(processed, total, elapsed, finished is not None)
        status = _compute_status(started, finished, processed, total, failed)

        return {
            "status": status,
            "processed": processed,
            "total": total,
            "failed": failed,
            "remaining": max(total - processed, 0),
            "percent": _percent(processed, total),
            "elapsed_seconds": round(elapsed, 3),
            "eta_seconds": eta,
            "started_at": started,
            "finished_at": finished,
            "current": current,
            "last_output": last_output,
            "run_id": run_id,
        }


def _percent(processed: int, total: int) -> float:
    if total <= 0:
        return 0.0
    return round(min(processed / total, 1.0) * 100.0, 2)


def _compute_eta(processed: int, total: int, elapsed: float, finished: bool) -> float | None:
    if finished:
        return 0.0
    if total <= 0 or processed <= 0:
        return None
    remaining = total - processed
    if remaining <= 0:
        return 0.0
    rate = processed / elapsed if elapsed > 0 else 0.0
    if rate <= 0:
        return None
    return round(remaining / rate, 3)


def _compute_status(started: float | None, finished: float | None,
                    processed: int, total: int, failed: int) -> str:
    if started is None:
        return "idle"
    if finished is not None:
        return "failed" if failed > 0 else "done"
    return "running"


def snapshot_from_manifest(path: str | Path) -> dict[str, Any]:
    """Build a snapshot dict directly from a pipeline manifest JSONL file.

    Lets the dashboard run after-the-fact against `finals/pipeline_manifest.jsonl`
    without needing the pipeline process. Records are one-line JSON objects;
    we count those whose `kind` is `output`, treat `kind=run_start` as the
    started_at anchor, and `kind=run_end` as finished_at.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    state = ProgressState()
    total = 0
    processed = 0
    failed = 0
    started_at: float | None = None
    finished_at: float | None = None
    last_output: str | None = None
    run_id: str | None = None

    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            kind = rec.get("kind") or rec.get("event")
            if kind == "run_start":
                started_at = rec.get("ts") or rec.get("started_at") or started_at
                total = int(rec.get("total") or rec.get("planned") or total)
                run_id = rec.get("run_id") or run_id
            elif kind == "output":
                processed += 1
                last_output = rec.get("output") or rec.get("path") or last_output
            elif kind == "failure":
                failed += 1
            elif kind == "run_end":
                finished_at = rec.get("ts") or rec.get("finished_at") or finished_at

    if total <= 0:
        total = processed + failed

    state.total = total
    state.processed = processed
    state.failed = failed
    state.started_at = started_at
    state.finished_at = finished_at
    state.last_output = last_output
    state.run_id = run_id
    return state.snapshot()
