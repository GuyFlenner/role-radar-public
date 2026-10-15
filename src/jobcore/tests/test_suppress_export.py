"""Suppression-feed export tests — the one-directional fix for the forked ledger.

Context: a second scanner in another repo kept re-proposing roles this ledger had
already closed, because outcomes live only here. The feed lets that scanner filter
without keeping a second durable ledger that can drift.
"""

import json

from src.jobcore import pipeline


def _ledger():
    return {
        "schema_version": 1,
        "company_policy": {"jfrog": {"policy": "do_not_pursue", "reason": "auto-rejected", "set": "2026-07-30"}},
        "opportunities": [
            {
                "id": "fetcherr:llm-team-lead",
                "company_slug": "fetcherr",
                "company": "Fetcherr",
                "role": "LLM Team Lead",
                "job_id": "BA.96C",
                "url": "https://www.comeet.com/jobs/fetcherr/68.006/llm-team-lead/BA.96C",
                "stage": "CLOSED",
                "outcome": "THEY_REJECTED",
                "last_touched": "2026-07-30",
            },
            {
                "id": "grip:sw-eng-tl",
                "company_slug": "grip",
                "company": "Grip Security",
                "role": "Software Engineering Team Lead",
                "job_id": "77.001",
                "url": "https://grip.example/jobs/77.001",
                "stage": "IN_PROCESS",
                "outcome": None,
                "last_touched": "2026-08-02",
            },
            {
                "id": "monday:backend-tl",
                "company_slug": "monday",
                "company": "monday.com",
                "role": "Backend Team Lead",
                "job_id": "",
                "url": "",
                "stage": "NEW",
                "outcome": None,
                "last_touched": "2026-08-01",
            },
        ],
    }


def test_closed_rows_are_exported_with_match_keys():
    feed = pipeline.suppress_list(_ledger())
    assert len(feed["closed"]) == 1
    row = feed["closed"][0]
    assert row["job_id"] == "BA.96C"
    assert row["role_key"] == "fetcherr|llm team lead"
    assert row["outcome"] == "THEY_REJECTED"
    assert row["company"] == "Fetcherr"


def test_do_not_pursue_companies_are_exported():
    assert pipeline.suppress_list(_ledger())["do_not_pursue"] == ["jfrog"]


def test_in_flight_is_separate_so_a_live_pursuit_is_never_dropped():
    """Marking an active interview is right; suppressing it would hide it entirely."""
    feed = pipeline.suppress_list(_ledger())
    assert [r["role_key"] for r in feed["in_flight"]] == ["grip|software engineering team lead"]
    assert all("grip" not in r["role_key"] for r in feed["closed"])


def test_new_rows_are_not_exported_at_all():
    feed = pipeline.suppress_list(_ledger())
    everything = json.dumps(feed)
    assert "monday" not in everything


def test_role_key_normalises_punctuation_so_retitling_still_matches():
    """ "R&D Team Leader" and "rd team leader" must land on the same key."""
    assert pipeline._norm_role("R&D Team Leader") == pipeline._norm_role("rd  team   leader")
    assert pipeline._norm_role("Backend TL — Platform") == "backend tl platform"


def test_hashed_mode_removes_plaintext_company_and_role():
    feed = pipeline.suppress_list(_ledger(), hashed=True)
    blob = json.dumps(feed)
    for secret in ("Fetcherr", "fetcherr", "LLM Team Lead", "Grip", "jfrog"):
        assert secret not in blob, f"{secret!r} leaked into the hashed feed"
    assert feed["hashed"] is True
    assert len(feed["do_not_pursue"][0]) == 16


def test_hashed_mode_is_deterministic_so_a_consumer_can_match():
    a = pipeline.suppress_list(_ledger(), hashed=True)
    b = pipeline.suppress_list(_ledger(), hashed=True)
    assert a["closed"][0]["role_key"] == b["closed"][0]["role_key"]
    # …and the consumer reproduces it from its own candidate, which is how filtering works
    assert a["closed"][0]["role_key"] == pipeline._digest("fetcherr|llm team lead")


def test_hashed_mode_leaves_empty_keys_empty():
    """An empty job_id must not hash into a value that collides with other blanks."""
    led = _ledger()
    led["opportunities"][0]["job_id"] = ""
    feed = pipeline.suppress_list(led, hashed=True)
    assert feed["closed"][0]["job_id"] == ""


def test_feed_carries_schema_and_provenance():
    feed = pipeline.suppress_list(_ledger())
    assert feed["schema"] == "suppress/1"
    assert "pipeline.json" in feed["source"]


def test_cli_writes_the_feed_to_a_file(tmp_path):
    led = tmp_path / "ledger.json"
    led.write_text(json.dumps(_ledger()), encoding="utf-8")
    out = tmp_path / "sub" / "suppress.json"
    rc = pipeline.main(["export-suppress", "--ledger", str(led), "--out", str(out)])
    assert rc == 0
    feed = json.loads(out.read_text(encoding="utf-8"))
    assert feed["closed"][0]["job_id"] == "BA.96C"


def test_cli_hashed_flag_is_honoured(tmp_path):
    led = tmp_path / "ledger.json"
    led.write_text(json.dumps(_ledger()), encoding="utf-8")
    out = tmp_path / "suppress.json"
    pipeline.main(["export-suppress", "--ledger", str(led), "--out", str(out), "--hashed"])
    assert "Fetcherr" not in out.read_text(encoding="utf-8")
