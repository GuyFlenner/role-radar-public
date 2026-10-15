"""The per-position detail route — and the boundary it must not cross.

The board already refuses to write. This route adds the opposite risk: *reading* too much. The
position folder it points at holds salary floors, interviewer dossiers and rejection debriefs, so
the route may serve document **names** and a validated profile and nothing else. Several tests here
plant a sentinel string in a real file and assert it never appears in the response — they fail the
moment someone "helpfully" inlines file contents.
"""

import json
import threading
from http.client import HTTPConnection

import pytest

from src.jobcore import board_server, board_view, pipeline

TODAY = "2026-08-07"
SENTINEL = "WALK-AWAY-BELOW-32000-DO-NOT-SERVE"

PROFILE = {
    "schema_version": "1.0",
    "company": "Payoneer",
    "industry": "Financial Technology (FinTech) / Cross-Border Payments",
    "founded": 2005,
    "size": "1,871-2,500 employees",
    "funding": "Public (NASDAQ: PAYO)",
    "what_they_do": "Cross-border payment infrastructure for SMBs in 190+ countries.",
    "differentiator": "Local receiving accounts in multiple currencies.",
    "tech_stack": ["Java", "C#", "React", "MongoDB"],
    "sources": ["https://investor.payoneer.com"],
    "updated": TODAY,
}


def _row(company="Payoneer", role="Engineering Leader Modernization", job_id="p1"):
    return {
        "company": company,
        "title": role,
        "job_id": job_id,
        "url": f"https://example.test/{job_id}",
        "location": "Petah Tikva",
        "match_score": 8,
        "level_band": "in_band",
        "source": "linkedin",
    }


@pytest.fixture(autouse=True)
def positions_root(tmp_path, monkeypatch):
    """Point the confinement root at the temp tree.

    `_documents` confines folders to THIS repo's `positions/`, so tests must move the root rather
    than build a directory that merely happens to be named `positions` — that name-only check was
    the Medium finding from the security review, and the decoy test below proves it is gone.
    """
    root = tmp_path / "positions"
    root.mkdir(exist_ok=True)
    monkeypatch.setattr(board_view, "POSITIONS_ROOT", root)
    return root


@pytest.fixture
def folder(tmp_path):
    """A realistic position folder: a profile, some research, and a private debrief."""
    d = tmp_path / "positions" / "payoneer"
    d.mkdir(parents=True)
    (d / "profile.json").write_text(json.dumps(PROFILE), encoding="utf-8")
    (d / "COMPANY_RESEARCH.md").write_text(f"# Payoneer\n\nSalary note: {SENTINEL}\n", encoding="utf-8")
    (d / "INTERVIEW_PREP.md").write_text(f"# Prep\n\n{SENTINEL}\n", encoding="utf-8")
    return d


@pytest.fixture
def ledger_path(tmp_path, folder):
    p = tmp_path / "pipeline.json"
    led = pipeline.load(p)
    pipeline.upsert(led, [_row(), _row(role="Backend Group Lead", job_id="p2")], TODAY)
    opp = pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")
    opp["position_folder"] = str(folder)
    pipeline.set_stage(led, opp["id"], "APPLIED", today=TODAY)
    pipeline.add_event(led, opp["id"], "HR_SCREEN", note="Liat, 30m", today=TODAY)
    pipeline.add_risk(led, opp["id"], text="legacy stack per Glassdoor", source="glassdoor", today=TODAY)
    pipeline.save(led, p)
    return p


@pytest.fixture
def server(ledger_path):
    srv = board_server.build_app(ledger_path)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv
    srv.shutdown()
    srv.server_close()
    t.join(timeout=5)


def _request(server, path, method="GET"):
    conn = HTTPConnection(*server.server_address[:2], timeout=5)
    try:
        conn.request(method, path)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


DETAIL = "/api/position/payoneer:engineering-leader-modernization"


# --- what it serves ----------------------------------------------------------


def test_detail_route_returns_the_card(server):
    status, body = _request(server, DETAIL)

    assert status == 200
    payload = json.loads(body)
    assert payload["card"]["company"] == "Payoneer"
    assert payload["card"]["id"] == "payoneer:engineering-leader-modernization"


def test_detail_route_returns_the_validated_profile(server):
    payload = json.loads(_request(server, DETAIL)[1])

    assert payload["profile"]["industry"].startswith("Financial Technology")
    assert payload["profile"]["tech_stack"] == ["Java", "C#", "React", "MongoDB"]
    assert payload["profile_error"] is None


def test_detail_route_returns_events_and_risks(server):
    payload = json.loads(_request(server, DETAIL)[1])

    assert [e["event"] for e in payload["events"]] == ["APPLIED", "HR_SCREEN"]
    assert payload["risks"][0]["text"] == "legacy stack per Glassdoor"
    assert payload["applied_date"] == TODAY


def test_detail_route_lists_document_names(server):
    payload = json.loads(_request(server, DETAIL)[1])

    names = {d["name"] for d in payload["documents"]}
    assert "COMPANY_RESEARCH.md" in names
    assert "INTERVIEW_PREP.md" in names


# --- the boundary ------------------------------------------------------------


def test_detail_never_includes_document_bodies(server):
    """Plant a private string in two files; it must not appear anywhere in the response."""
    _, body = _request(server, DETAIL)

    assert SENTINEL.encode() not in body


def test_documents_carry_no_content_or_path_field(server):
    payload = json.loads(_request(server, DETAIL)[1])

    for doc in payload["documents"]:
        assert set(doc) <= {"name", "kind"}, "a document entry must not carry a body or a filesystem path"


def test_position_folder_outside_positions_yields_no_documents(tmp_path, folder):
    """A folder pointer that escapes the positions tree serves nothing — no traversal, no leak."""
    outside = tmp_path / "secrets"
    outside.mkdir()
    (outside / "id_rsa").write_text(SENTINEL, encoding="utf-8")

    p = tmp_path / "pipeline.json"
    led = pipeline.load(p)
    pipeline.upsert(led, [_row()], TODAY)
    pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")["position_folder"] = str(outside)
    pipeline.save(led, p)

    srv = board_server.build_app(p)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, body = _request(srv, DETAIL)
        assert status == 200
        assert json.loads(body)["documents"] == []
        assert SENTINEL.encode() not in body
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_a_decoy_positions_directory_elsewhere_is_refused(tmp_path):
    """Regression, Phase 5 security review (Medium).

    The confinement check used to ask whether any ancestor was *named* `positions`, which any
    directory of that name anywhere on the machine satisfied. It must be rooted at the real one.
    """
    decoy = tmp_path / "evilroot" / "positions" / "not-the-real-one"
    decoy.mkdir(parents=True)
    (decoy / "STOLEN.md").write_text(SENTINEL, encoding="utf-8")

    assert board_view._documents(str(decoy)) == []


def test_the_positions_root_itself_lists_nothing(tmp_path, positions_root):
    """A pointer at the root rather than a position folder is a mistake, not a directory listing."""
    (positions_root / "loose.md").write_text(SENTINEL, encoding="utf-8")

    assert board_view._documents(str(positions_root)) == []


def test_a_traversal_shaped_folder_pointer_is_refused(tmp_path):
    p = tmp_path / "pipeline.json"
    led = pipeline.load(p)
    pipeline.upsert(led, [_row()], TODAY)
    pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")["position_folder"] = "positions/../../.."
    pipeline.save(led, p)

    srv = board_server.build_app(p)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        payload = json.loads(_request(srv, DETAIL)[1])
        assert payload["documents"] == []
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


def test_an_invalid_profile_serves_null_and_an_error(tmp_path, folder):
    """Never the raw dict — a profile that fails validation is withheld entirely."""
    (folder / "profile.json").write_text(
        json.dumps({**PROFILE, "what_they_do": f"pays ₪60K — {SENTINEL}"}), encoding="utf-8"
    )

    p = tmp_path / "pipeline.json"
    led = pipeline.load(p)
    pipeline.upsert(led, [_row()], TODAY)
    pipeline._find(led, opp_id="payoneer:engineering-leader-modernization")["position_folder"] = str(folder)
    pipeline.save(led, p)

    srv = board_server.build_app(p)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        status, body = _request(srv, DETAIL)
        assert status == 200
        payload = json.loads(body)
        assert payload["profile"] is None
        assert payload["profile_error"]
        assert SENTINEL.encode() not in body
    finally:
        srv.shutdown()
        srv.server_close()
        t.join(timeout=5)


# --- routing hygiene ---------------------------------------------------------


def test_unknown_id_is_a_404(server):
    status, _ = _request(server, "/api/position/nope:not-here")

    assert status == 404


def test_the_bare_detail_prefix_is_a_404(server):
    status, _ = _request(server, "/api/position/")

    assert status == 404


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_write_verbs_on_the_detail_route_are_refused(server, method):
    status, body = _request(server, DETAIL, method=method)

    assert status == 405
    assert b"read-only" in body


def test_the_detail_route_does_not_write_the_ledger(server, ledger_path):
    before = ledger_path.read_bytes()
    _request(server, DETAIL)

    assert ledger_path.read_bytes() == before


def test_a_row_with_no_folder_serves_an_empty_document_list(server):
    payload = json.loads(_request(server, "/api/position/payoneer:backend-group-lead")[1])

    assert payload["documents"] == []
    assert payload["profile"] is None
