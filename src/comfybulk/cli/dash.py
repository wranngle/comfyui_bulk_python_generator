"""`comfybulk dash` — serve the progress dashboard.

Two modes:

  --manifest <path>   Tail a `pipeline_manifest.jsonl` file. The server
                      rereads it on every /api/state hit, so an in-flight
                      pipeline writing the manifest is reflected live.
  --demo              Run with a synthetic in-memory ticker; useful for
                      smoke-testing the UI without spinning up a real
                      pipeline. Advances 1 item/second up to --demo-total.

Either mode binds to host:port (default 127.0.0.1:0 — ephemeral).
"""
from __future__ import annotations

import argparse
import sys
import threading
import time
from pathlib import Path

from ..dash import DashboardServer, ProgressState
from ..dash.state import snapshot_from_manifest


def _add_arguments(p: argparse.ArgumentParser) -> None:
    src = p.add_mutually_exclusive_group(required=True)
    src.add_argument("--manifest", help="Path to pipeline_manifest.jsonl to tail")
    src.add_argument("--demo", action="store_true",
                     help="Run with a synthetic in-memory progress ticker")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=0,
                   help="Bind port (default 0 = ephemeral; printed on start)")
    p.add_argument("--demo-total", type=int, default=10,
                   help="(demo only) Total items to simulate")
    p.add_argument("--demo-interval", type=float, default=1.0,
                   help="(demo only) Seconds between simulated item completions")


def _run_demo(state: ProgressState, total: int, interval: float, stop: threading.Event) -> None:
    state.mark_started(total, run_id="demo")
    for i in range(total):
        if stop.is_set():
            break
        state.mark_current(f"demo-item-{i + 1}.mp4")
        if stop.wait(interval):
            break
        state.mark_item_done(output=f"out/demo-item-{i + 1}.mp4")
    state.mark_finished()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="comfybulk dash",
        description="Serve the comfybulk progress dashboard.",
    )
    _add_arguments(parser)
    args = parser.parse_args(argv)

    state = ProgressState()
    demo_stop = threading.Event()
    demo_thread: threading.Thread | None = None

    if args.demo:
        snapshot_fn = state.snapshot
        demo_thread = threading.Thread(
            target=_run_demo, args=(state, args.demo_total, args.demo_interval, demo_stop),
            daemon=True, name="comfybulk-dash-demo",
        )
        demo_thread.start()
    else:
        manifest_path = Path(args.manifest)
        if not manifest_path.exists():
            print(f"manifest not found: {manifest_path}", file=sys.stderr)
            return 2

        def snapshot_fn() -> dict:
            try:
                return snapshot_from_manifest(manifest_path)
            except FileNotFoundError:
                return state.snapshot()

    srv = DashboardServer(snapshot_fn=snapshot_fn, host=args.host, port=args.port)
    srv.start()
    print(f"[comfybulk-dash] {srv.url}", flush=True)
    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        pass
    finally:
        demo_stop.set()
        if demo_thread is not None:
            demo_thread.join(timeout=2.0)
        srv.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
