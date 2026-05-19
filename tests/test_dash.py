"""Progress dashboard tests.

Cover the three things the spec promises and nothing more:

  1. /api/state returns JSON with `processed`, `total`, `eta_seconds` keys.
  2. ETA reflects real progress (decreasing as items complete; null before
     work starts; zero after finish).
  3. The server binds to an ephemeral port so parallel test runs do not
     collide on a fixed port.

We also cover the manifest fallback path because that is the surface a
user actually points at when running `comfybulk dash --manifest finals/...`.
"""
from __future__ import annotations

import json
import time
import urllib.request

import pytest

from comfybulk.dash import DashboardServer, ProgressState
from comfybulk.dash.state import snapshot_from_manifest


def _fetch_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=2.0) as r:
        assert r.status == 200
        return json.loads(r.read().decode("utf-8"))


# --- /api/state contract ---------------------------------------------------


def test_api_state_includes_required_keys():
    state = ProgressState()
    state.mark_started(total=3)
    with DashboardServer(state=state) as srv:
        data = _fetch_json(f"{srv.url}/api/state")
    for k in ("processed", "total", "eta_seconds"):
        assert k in data, f"missing required key: {k}"
    assert data["total"] == 3
    assert data["processed"] == 0


def test_ephemeral_port_assigned_and_unique():
    state_a, state_b = ProgressState(), ProgressState()
    with DashboardServer(state=state_a) as a, DashboardServer(state=state_b) as b:
        assert a.port > 0
        assert b.port > 0
        assert a.port != b.port


def test_api_state_reflects_progress_updates():
    state = ProgressState()
    state.mark_started(total=4)
    with DashboardServer(state=state) as srv:
        d0 = _fetch_json(f"{srv.url}/api/state")
        assert d0["processed"] == 0
        state.mark_item_done(output="a.mp4")
        state.mark_item_done(output="b.mp4")
        d1 = _fetch_json(f"{srv.url}/api/state")
        assert d1["processed"] == 2
        assert d1["last_output"] == "b.mp4"
        assert d1["status"] == "running"


def test_status_lifecycle_idle_running_done():
    state = ProgressState()
    with DashboardServer(state=state) as srv:
        assert _fetch_json(f"{srv.url}/api/state")["status"] == "idle"
        state.mark_started(total=1)
        assert _fetch_json(f"{srv.url}/api/state")["status"] == "running"
        state.mark_item_done()
        state.mark_finished()
        final = _fetch_json(f"{srv.url}/api/state")
        assert final["status"] == "done"
        assert final["eta_seconds"] == 0.0


# --- ETA semantics ---------------------------------------------------------


def test_eta_is_null_before_any_progress():
    state = ProgressState()
    state.mark_started(total=10)
    snap = state.snapshot()
    assert snap["eta_seconds"] is None
    assert snap["processed"] == 0
    assert snap["total"] == 10


def test_eta_decreases_as_work_progresses():
    state = ProgressState()
    state.mark_started(total=10)
    time.sleep(0.05)
    state.mark_item_done()
    snap_a = state.snapshot()
    time.sleep(0.05)
    state.mark_item_done()
    state.mark_item_done()
    snap_b = state.snapshot()
    assert snap_a["eta_seconds"] is not None
    assert snap_b["eta_seconds"] is not None
    assert snap_b["eta_seconds"] < snap_a["eta_seconds"]


def test_eta_is_zero_when_finished():
    state = ProgressState()
    state.mark_started(total=1)
    state.mark_item_done()
    state.mark_finished()
    assert state.snapshot()["eta_seconds"] == 0.0


# --- routes / 404 ----------------------------------------------------------


def test_root_serves_html_dashboard():
    state = ProgressState()
    with DashboardServer(state=state) as srv:
        with urllib.request.urlopen(f"{srv.url}/", timeout=2.0) as r:
            body = r.read().decode("utf-8")
            ctype = r.headers.get("Content-Type", "")
    assert "text/html" in ctype
    assert "comfybulk" in body.lower()
    assert "/api/state" in body  # the page must point at the JSON endpoint


def test_unknown_path_404():
    state = ProgressState()
    with DashboardServer(state=state) as srv:
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(f"{srv.url}/does-not-exist", timeout=2.0)
    assert exc.value.code == 404


def test_health_endpoint():
    state = ProgressState()
    with DashboardServer(state=state) as srv:
        data = _fetch_json(f"{srv.url}/api/health")
    assert data == {"ok": True}


# --- manifest fallback (file-driven snapshot) ------------------------------


def test_snapshot_from_manifest_counts_outputs(tmp_path):
    manifest = tmp_path / "pipeline_manifest.jsonl"
    manifest.write_text(
        "\n".join([
            json.dumps({"kind": "run_start", "ts": 1000.0, "total": 3, "run_id": "r1"}),
            json.dumps({"kind": "output", "ts": 1001.0, "output": "out/a.mp4"}),
            json.dumps({"kind": "output", "ts": 1002.0, "output": "out/b.mp4"}),
            json.dumps({"kind": "failure", "ts": 1003.0, "reason": "boom"}),
            json.dumps({"kind": "run_end", "ts": 1004.0}),
        ]) + "\n",
        encoding="utf-8",
    )
    snap = snapshot_from_manifest(manifest)
    assert snap["processed"] == 2
    assert snap["failed"] == 1
    assert snap["total"] == 3
    assert snap["last_output"] == "out/b.mp4"
    assert snap["run_id"] == "r1"
    # finished + at least one failure within total -> failed
    assert snap["status"] == "failed"
    assert snap["eta_seconds"] == 0.0


def test_snapshot_from_manifest_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        snapshot_from_manifest(tmp_path / "nope.jsonl")


def test_snapshot_from_manifest_tolerates_garbage_lines(tmp_path):
    manifest = tmp_path / "m.jsonl"
    manifest.write_text(
        "\n".join([
            "not-json",
            json.dumps({"kind": "output", "output": "x.mp4"}),
            "",
            "{",
            json.dumps({"kind": "output", "output": "y.mp4"}),
        ]) + "\n",
        encoding="utf-8",
    )
    snap = snapshot_from_manifest(manifest)
    assert snap["processed"] == 2
    assert snap["last_output"] == "y.mp4"


def test_server_uses_snapshot_fn_for_live_manifest_tailing(tmp_path):
    manifest = tmp_path / "live.jsonl"
    manifest.write_text(
        json.dumps({"kind": "run_start", "ts": 1.0, "total": 5}) + "\n",
        encoding="utf-8",
    )

    def snapshot_fn():
        return snapshot_from_manifest(manifest)

    with DashboardServer(snapshot_fn=snapshot_fn) as srv:
        d0 = _fetch_json(f"{srv.url}/api/state")
        assert d0["total"] == 5 and d0["processed"] == 0
        with manifest.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"kind": "output", "output": "live.mp4"}) + "\n")
        d1 = _fetch_json(f"{srv.url}/api/state")
        assert d1["processed"] == 1
        assert d1["last_output"] == "live.mp4"


# --- constructor guard -----------------------------------------------------


def test_constructor_requires_state_or_snapshot_fn():
    with pytest.raises(ValueError):
        DashboardServer()
