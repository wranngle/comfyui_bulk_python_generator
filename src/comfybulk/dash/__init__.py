"""Progress dashboard for in-flight pipeline runs.

A tiny stdlib-only HTTP UI that exposes `/api/state` (JSON) and `/` (HTML).
The state surface is a `ProgressState` object the pipeline updates by
calling `mark_started`, `mark_item_done`, `mark_item_failed`, `mark_finished`.
The HTTP server reads the same object under a lock — no IPC, no extra deps.
"""
from __future__ import annotations

from .state import ProgressState, snapshot_from_manifest
from .server import DashboardServer, serve_forever

__all__ = [
    "ProgressState",
    "DashboardServer",
    "serve_forever",
    "snapshot_from_manifest",
]
