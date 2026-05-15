"""Resume-on-failure checkpointing for bulk pipeline runs.

A `Checkpoint` is a JSON-lines ledger at `<dir>/.comfybulk-checkpoint.jsonl`.
Header line records the run intent (source, variants, quantity, started_at).
Each subsequent line records one completed (variant, iteration) tuple.
On `--resume`, the runner reloads the ledger, skips already-completed
iterations, and appends the rest. Idempotent: marking the same key twice
is a no-op.
"""
from __future__ import annotations
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable


CHECKPOINT_FILENAME = ".comfybulk-checkpoint.jsonl"


def iteration_key(variant: str, iteration: int) -> str:
    return f"{variant}#{iteration}"


@dataclass
class Checkpoint:
    path: Path
    run_id: str = ""
    started_at: str = ""
    source: str = ""
    variants: list[str] = field(default_factory=list)
    quantity: int = 0
    processed: dict[str, dict] = field(default_factory=dict)

    @classmethod
    def for_dir(cls, base_dir: str | Path) -> "Checkpoint":
        p = Path(base_dir) / CHECKPOINT_FILENAME
        return cls(path=p)

    def exists(self) -> bool:
        return self.path.is_file()

    def load(self) -> "Checkpoint":
        """Read header + processed entries from disk. Tolerant of missing/empty."""
        self.processed = {}
        if not self.path.is_file():
            return self
        with self.path.open("r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("event") == "run_start":
                    self.run_id = rec.get("run_id", self.run_id)
                    self.started_at = rec.get("started_at", self.started_at)
                    self.source = rec.get("source", self.source)
                    self.variants = list(rec.get("variants") or self.variants)
                    self.quantity = int(rec.get("quantity") or self.quantity)
                elif rec.get("event") == "iteration_done":
                    key = rec.get("key")
                    if key:
                        self.processed[key] = rec
        return self

    def start_run(self, *, source: str, variants: Iterable[str], quantity: int,
                  run_id: str | None = None, resume: bool = False) -> "Checkpoint":
        """Stamp a fresh run header. With `resume=True`, keep existing entries."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if resume and self.path.is_file():
            self.load()
            return self
        self.processed = {}
        self.run_id = run_id or datetime.now().strftime("run_%Y%m%d_%H%M%S_%f")
        self.started_at = datetime.now().isoformat(timespec="seconds")
        self.source = str(source)
        self.variants = list(variants)
        self.quantity = int(quantity)
        with self.path.open("w", encoding="utf-8") as f:
            f.write(json.dumps({
                "event": "run_start",
                "run_id": self.run_id,
                "started_at": self.started_at,
                "source": self.source,
                "variants": self.variants,
                "quantity": self.quantity,
            }, sort_keys=True) + "\n")
        return self

    def is_processed(self, variant: str, iteration: int) -> bool:
        return iteration_key(variant, iteration) in self.processed

    def mark_processed(self, variant: str, iteration: int,
                       outputs: list[str] | None = None) -> None:
        key = iteration_key(variant, iteration)
        if key in self.processed:
            return
        rec = {
            "event": "iteration_done",
            "key": key,
            "variant": variant,
            "iteration": iteration,
            "outputs": list(outputs or []),
            "completed_at": datetime.now().isoformat(timespec="seconds"),
        }
        self.processed[key] = rec
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, sort_keys=True) + "\n")

    def remaining(self, variants: Iterable[str], quantity: int) -> list[tuple[str, int]]:
        out: list[tuple[str, int]] = []
        for v in variants:
            for i in range(int(quantity)):
                if not self.is_processed(v, i):
                    out.append((v, i))
        return out

    def processed_keys(self) -> list[str]:
        return list(self.processed.keys())
