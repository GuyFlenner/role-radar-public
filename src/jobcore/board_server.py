"""Localhost dashboard for the pipeline ledger. Read-only, loopback-only, zero dependencies.

    uv run python -m src.jobcore.board_server --port 8765

Serves exactly two things — the page, and the view model behind it. There is deliberately **no
write path**: ``record_submission``'s refusal of a second route and ``validate``'s schema check
are the controls that keep a CV from going out twice, and they live in ``pipeline.py``. A UI that
wrote ``pipeline.json`` directly would bypass both, so this one does not write at all. State
changes are offered on the page as copyable CLI commands instead.

Routing is exact-match on purpose. No filesystem path is ever derived from a URL, so the ledger
sitting next to the static file cannot be fetched by traversal.
"""

from __future__ import annotations

import argparse
import json
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote

from . import board_view
from .console import force_utf8_output
from .pipeline import LEDGER_DEFAULT, load

HOST = "127.0.0.1"
DEFAULT_PORT = 8765
STATIC = Path(__file__).parent / "static"
PAGE = STATIC / "board.html"

#: The one prefix route. Everything after it is an opportunity id, never a path — see ``do_GET``.
DETAIL_PREFIX = "/api/position/"


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


class BoardHandler(BaseHTTPRequestHandler):
    """Two GET routes. Every other verb and path is refused."""

    server_version = "jobcore-board"
    ledger_path: Path = LEDGER_DEFAULT

    def _send(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Nothing here is meant to be cached, embedded, or reachable from another origin.
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _host_ok(self) -> bool:
        """DNS-rebinding guard: only the loopback names on OUR port are served."""
        port = int(self.server.server_address[1])  # type: ignore[index]
        host = (self.headers.get("Host") or "").lower()
        return host in {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's contract
        if not self._host_ok():
            self._send(403, b"forbidden host", "text/plain; charset=utf-8")
            return
        route = self.path.split("?", 1)[0]
        if route == "/":
            self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        elif route == "/api/board":
            ledger = load(self.ledger_path)
            payload = board_view.view(ledger, mtime=_mtime(self.ledger_path))
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        elif route.startswith(DETAIL_PREFIX):
            # The suffix is an opportunity **id**, looked up in the ledger — it never becomes a
            # filesystem path. The folder a detail payload reads comes off the matched row, so the
            # module's "no filesystem path is ever derived from a URL" guarantee still holds.
            opp_id = unquote(route[len(DETAIL_PREFIX) :])
            detail = board_view.detail(load(self.ledger_path), opp_id) if opp_id else None
            if detail is None:
                self._send(404, b"not found", "text/plain; charset=utf-8")
            else:
                body = json.dumps(detail, ensure_ascii=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def _refuse(self) -> None:
        self._send(405, b"this board is read-only", "text/plain; charset=utf-8")

    do_POST = do_PUT = do_PATCH = do_DELETE = _refuse  # noqa: N815

    def log_message(self, fmt: str, *args) -> None:
        """Silence per-request stderr chatter; it is noise in tests and in the terminal."""


class BoardServer(ThreadingHTTPServer):
    """Refuses to share a port with an already-running board.

    ``HTTPServer`` sets ``allow_reuse_address``, and on Windows that lets a second socket bind a
    port a live server is still listening on. Both processes then report success while the older
    one keeps answering — so you restart the board, read stale data, and have no way to tell.
    Failing the bind is the honest outcome.
    """

    allow_reuse_address = False


def build_app(ledger_path: Path | str = LEDGER_DEFAULT, port: int = 0) -> BoardServer:
    """A server bound to loopback. ``port=0`` picks an ephemeral port, which is what tests use."""
    handler = type("BoundBoardHandler", (BoardHandler,), {"ledger_path": Path(ledger_path)})
    return BoardServer((HOST, port), handler)


def main(argv: list[str] | None = None) -> int:
    force_utf8_output()
    ap = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        epilog="Full documentation: docs/PIPELINE_BOARD.md",
    )
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"default {DEFAULT_PORT}")
    ap.add_argument("--ledger", default=str(LEDGER_DEFAULT), help="ledger JSON (default: the repo ledger)")
    ap.add_argument("--no-open", action="store_true", help="do not launch a browser")
    args = ap.parse_args(argv)

    srv = build_app(args.ledger, args.port)
    url = f"http://{HOST}:{srv.server_address[1]}"
    print(f"pipeline board: {url}  (read-only, loopback only — Ctrl-C to stop)", file=sys.stderr)
    if not args.no_open:
        webbrowser.open(url)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("", file=sys.stderr)
    finally:
        srv.server_close()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
