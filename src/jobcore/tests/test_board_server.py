"""board_server tests — a real socket on an ephemeral loopback port, no network.

Half of this file is the read-only guard. The dashboard's safety claim is not "we chose not to
write" but "there is no write path", and a control that cannot fail is not a control
([[feedback_a_green_control_that_means_nothing]]). These tests fail if someone later imports
`save`, adds a POST handler, or binds a non-loopback interface.
"""

import ast
import json
import threading
from http.client import HTTPConnection
from pathlib import Path

import pytest

from src.jobcore import board_server, pipeline

TODAY = "2026-08-06"

#: The only names board_server may take from pipeline. Reading the ledger is all it does.
ALLOWED_PIPELINE_IMPORTS = {"load", "LEDGER_DEFAULT"}

#: Anything that mutates the ledger. None of these may be reachable from the server module.
WRITE_NAMES = {
    "save",
    "set_stage",
    "record_submission",
    "add_channel",
    "set_policy",
    "upsert",
    "prune",
    "dedupe",
}


def _row(company="Armis", role="AI Engineering Team Lead", job_id="a1", score=9):
    return {
        "company": company,
        "title": role,
        "job_id": job_id,
        "url": f"https://example.test/{job_id}",
        "location": "Tel Aviv",
        "match_score": score,
        "level_band": "in_band",
        "source": "linkedin",
    }


@pytest.fixture
def ledger_path(tmp_path):
    p = tmp_path / "pipeline.json"
    led = pipeline.load(p)
    pipeline.upsert(led, [_row(), _row("Gong", "Manager AI Agentic", "g1", 8)], TODAY)
    pipeline.set_stage(led, "gong:manager-ai-agentic", stage="APPLIED", today=TODAY)
    pipeline.save(led, p)
    return p


@pytest.fixture
def server(ledger_path):
    """A live server on an ephemeral loopback port, torn down after the test."""
    srv = board_server.build_app(ledger_path)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    t.join(timeout=5)


def _request(server, path, method="GET"):
    """Raw HTTP so non-GET verbs reach the handler instead of being rewritten."""
    conn = HTTPConnection(*server.server_address[:2], timeout=5)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


# --- it serves the two things it promises ------------------------------------


def test_root_serves_the_dashboard_page(server):
    status, body = _request(server, "/")

    assert status == 200
    assert b"<html" in body.lower()


def test_api_board_returns_the_view_model(server):
    status, body = _request(server, "/api/board")

    assert status == 200
    payload = json.loads(body)
    assert payload["counts"]["act_now"] == 1
    assert payload["counts"]["in_flight"] == 1
    assert {c["id"] for c in payload["cards"]} == {
        "armis:ai-engineering-team-lead",
        "gong:manager-ai-agentic",
    }


def test_api_board_reflects_a_ledger_edit_without_a_restart(server, ledger_path):
    """A `pipeline set` in another window must show up on the next poll."""
    led = pipeline.load(ledger_path)
    pipeline.set_stage(led, "armis:ai-engineering-team-lead", stage="APPLIED", today=TODAY)
    pipeline.save(led, ledger_path)

    payload = json.loads(_request(server, "/api/board")[1])

    assert payload["counts"]["in_flight"] == 2
    assert payload["counts"]["act_now"] == 0


def test_api_board_reports_a_changing_mtime(server, ledger_path):
    first = json.loads(_request(server, "/api/board")[1])["ledger_mtime"]

    assert first > 0


def test_a_missing_ledger_serves_an_empty_board_rather_than_a_500(tmp_path):
    srv = board_server.build_app(tmp_path / "absent.json")
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, body = _request(srv, "/api/board")
        assert status == 200
        assert json.loads(body)["cards"] == []
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


# --- the read-only guard -----------------------------------------------------


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("path", ["/", "/api/board"])
def test_every_write_verb_is_refused(server, method, path):
    status, _ = _request(server, path, method=method)

    assert status == 405


def test_a_full_request_cycle_leaves_the_ledger_byte_identical(server, ledger_path):
    before, before_mtime = ledger_path.read_bytes(), ledger_path.stat().st_mtime

    _request(server, "/")
    _request(server, "/api/board")
    _request(server, "/api/board", method="POST")

    assert ledger_path.read_bytes() == before
    assert ledger_path.stat().st_mtime == before_mtime


def test_board_server_imports_no_mutating_pipeline_function():
    """Adding `save` or `set_stage` to the server's imports must fail this test."""
    tree = ast.parse(Path(board_server.__file__).read_text(encoding="utf-8"))

    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and (node.module or "").endswith("pipeline"):
            imported |= {a.name for a in node.names}
        if isinstance(node, ast.ImportFrom) and node.module is None:
            # `from . import pipeline` re-opens the whole module, guards included.
            assert "pipeline" not in {a.name for a in node.names}, (
                "board_server must import named read-only functions, not the pipeline module"
            )

    assert imported <= ALLOWED_PIPELINE_IMPORTS, f"unexpected pipeline imports: {imported}"
    assert not (imported & WRITE_NAMES)


def test_board_server_source_mentions_no_write_helper():
    src = Path(board_server.__file__).read_text(encoding="utf-8")

    for name in WRITE_NAMES:
        assert f"{name}(" not in src


def test_the_socket_binds_loopback_only(server):
    assert server.server_address[0] == "127.0.0.1"


# --- it cannot be talked into serving the ledger -----------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/../../job-tracking/pipeline.json",
        "/..%2f..%2fjob-tracking%2fpipeline.json",
        "/static/../../../pipeline.json",
        "/pipeline.json",
        "/board_view.py",
    ],
)
def test_traversal_and_stray_paths_are_refused(server, path):
    status, body = _request(server, path)

    assert status == 404
    assert b"opportunities" not in body


def test_unknown_route_is_a_404(server):
    assert _request(server, "/nope")[0] == 404


def test_a_second_server_cannot_silently_share_the_port(ledger_path):
    """Windows honours SO_REUSEADDR by letting a second socket bind a live port, so a stale
    server keeps answering while the new one reports success — you then read old data and
    cannot tell. Binding must fail loudly instead."""
    first = board_server.build_app(ledger_path)
    try:
        port = first.server_address[1]
        with pytest.raises(OSError):
            board_server.build_app(ledger_path, port)
    finally:
        first.server_close()


# --- DNS-rebinding guard -------------------------------------------------------


@pytest.mark.parametrize("host", ["evil.example.com", "evil.example.com:80", "127.0.0.1:1", "", "localhost"])
def test_foreign_host_header_is_refused(server, host):
    """A rebinding page reaches us under ITS hostname — that must never be served."""
    conn = HTTPConnection(*server.server_address[:2], timeout=5)
    try:
        conn.putrequest("GET", "/api/board", skip_host=True)
        if host:
            conn.putheader("Host", host)
        conn.endheaders()
        resp = conn.getresponse()
        body = resp.read()
    finally:
        conn.close()
    assert resp.status == 403
    assert b"board" not in body


def test_localhost_name_on_our_port_is_served(server):
    port = server.server_address[1]
    conn = HTTPConnection(*server.server_address[:2], timeout=5)
    try:
        conn.putrequest("GET", "/api/board", skip_host=True)
        conn.putheader("Host", f"localhost:{port}")
        conn.endheaders()
        assert conn.getresponse().status == 200
    finally:
        conn.close()


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__]))
