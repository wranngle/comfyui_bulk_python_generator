"""HTTP server for the progress dashboard.

Stdlib-only. Binds to an ephemeral port by default (host="127.0.0.1", port=0)
so tests and parallel runs do not collide on a fixed port. The chosen port
is exposed via `DashboardServer.port` after construction.

Routes:
  GET /            -> HTML dashboard (auto-refreshing)
  GET /api/state   -> JSON snapshot (always includes processed/total/eta_seconds)
  GET /api/health  -> {"ok": true}
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from .state import ProgressState


SnapshotFn = Callable[[], dict[str, Any]]


_HTML_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>comfybulk dashboard</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.4 system-ui, sans-serif; margin: 2rem; max-width: 720px; }
  h1 { font-size: 1.1rem; margin: 0 0 1rem; }
  .row { display: flex; gap: 1rem; flex-wrap: wrap; margin: .25rem 0; }
  .k { opacity: .65; min-width: 7.5rem; }
  .bar { background: #ddd2; border-radius: 6px; height: 14px; overflow: hidden; margin: .75rem 0; }
  .bar > div { background: #4a8; height: 100%; transition: width .3s; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 10px;
          background: #4a8; color: #fff; font-size: 12px; }
  .pill.idle { background: #999; }
  .pill.failed { background: #c44; }
  .pill.done { background: #4a8; }
  .pill.running { background: #38a; }
  pre { background: #0001; padding: 6px 8px; border-radius: 4px; overflow-x: auto; }
</style>
</head>
<body>
<h1>comfybulk &mdash; <span id="status" class="pill idle">idle</span></h1>
<div class="bar"><div id="bar" style="width:0%"></div></div>
<div class="row"><span class="k">progress</span><span id="prog">0 / 0</span></div>
<div class="row"><span class="k">percent</span><span id="pct">0%</span></div>
<div class="row"><span class="k">eta</span><span id="eta">&mdash;</span></div>
<div class="row"><span class="k">elapsed</span><span id="elapsed">0s</span></div>
<div class="row"><span class="k">failed</span><span id="failed">0</span></div>
<div class="row"><span class="k">current</span><span id="current">&mdash;</span></div>
<div class="row"><span class="k">last output</span><span id="last">&mdash;</span></div>
<div class="row"><span class="k">run id</span><span id="rid">&mdash;</span></div>
<details><summary>raw /api/state</summary><pre id="raw"></pre></details>
<script>
function fmt(sec) {
  if (sec == null) return '&mdash;';
  sec = Math.max(0, Math.round(sec));
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
  return h ? `${h}h${m}m${s}s` : m ? `${m}m${s}s` : `${s}s`;
}
async function tick() {
  try {
    const r = await fetch('/api/state', { cache: 'no-store' });
    const s = await r.json();
    const status = document.getElementById('status');
    status.textContent = s.status;
    status.className = 'pill ' + s.status;
    document.getElementById('bar').style.width = (s.percent || 0) + '%';
    document.getElementById('prog').textContent = `${s.processed} / ${s.total}`;
    document.getElementById('pct').textContent = (s.percent || 0) + '%';
    document.getElementById('eta').innerHTML = fmt(s.eta_seconds);
    document.getElementById('elapsed').textContent = fmt(s.elapsed_seconds);
    document.getElementById('failed').textContent = s.failed;
    document.getElementById('current').textContent = s.current || '—';
    document.getElementById('last').textContent = s.last_output || '—';
    document.getElementById('rid').textContent = s.run_id || '—';
    document.getElementById('raw').textContent = JSON.stringify(s, null, 2);
  } catch (e) { /* keep last good values */ }
}
tick(); setInterval(tick, 1000);
</script>
</body>
</html>
"""


class _Handler(BaseHTTPRequestHandler):
    server_version = "comfybulk-dash/1.0"

    def log_message(self, format: str, *args: Any) -> None:  # silence default access log
        return

    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler API)
        snapshot_fn: SnapshotFn = self.server.snapshot_fn  # type: ignore[attr-defined]
        if self.path == "/" or self.path.startswith("/?"):
            self._send(200, "text/html; charset=utf-8", _HTML_PAGE.encode("utf-8"))
            return
        if self.path == "/api/state":
            data = json.dumps(snapshot_fn(), default=str).encode("utf-8")
            self._send(200, "application/json", data)
            return
        if self.path == "/api/health":
            self._send(200, "application/json", b'{"ok":true}')
            return
        self._send(404, "application/json", b'{"error":"not_found"}')

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)


class DashboardServer:
    """Threaded HTTP dashboard. Default port=0 picks an ephemeral free port.

    Usage:
        state = ProgressState()
        srv = DashboardServer(state=state)
        srv.start()
        ...
        srv.stop()
    """

    def __init__(
        self,
        state: ProgressState | None = None,
        snapshot_fn: SnapshotFn | None = None,
        host: str = "127.0.0.1",
        port: int = 0,
    ) -> None:
        if state is None and snapshot_fn is None:
            raise ValueError("DashboardServer needs either state or snapshot_fn")
        self._snapshot_fn: SnapshotFn = snapshot_fn or state.snapshot  # type: ignore[union-attr]
        self._httpd = ThreadingHTTPServer((host, port), _Handler)
        self._httpd.snapshot_fn = self._snapshot_fn  # type: ignore[attr-defined]
        self._thread: threading.Thread | None = None

    @property
    def host(self) -> str:
        return self._httpd.server_address[0]

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}"

    def start(self) -> None:
        if self._thread is not None:
            return
        t = threading.Thread(target=self._httpd.serve_forever,
                             name="comfybulk-dash", daemon=True)
        t.start()
        self._thread = t

    def stop(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
            self._thread = None

    def __enter__(self) -> "DashboardServer":
        self.start()
        return self

    def __exit__(self, *_exc: Any) -> None:
        self.stop()


def serve_forever(
    state: ProgressState | None = None,
    snapshot_fn: SnapshotFn | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
) -> None:
    srv = DashboardServer(state=state, snapshot_fn=snapshot_fn, host=host, port=port)
    srv.start()
    print(f"[comfybulk-dash] listening on {srv.url}")
    try:
        while True:
            srv._thread.join(timeout=1.0) if srv._thread else None  # noqa: SLF001
    except KeyboardInterrupt:
        srv.stop()
