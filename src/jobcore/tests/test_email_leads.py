"""Tests for src.jobcore.email_leads."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.jobcore import email_leads


def _entry(**overrides) -> dict:
    base = email_leads.new_entry(
        gmail_message_id="m1",
        received_date="2026-08-20T09:14:00+03:00",
        sender="recruiter@example.com",
        subject="Backend Team Lead role",
        raw_text="We have a Backend Team Lead opening, Python/AWS, remote-friendly.",
        role_title="Backend Team Lead",
    )
    assert base is not None
    base.update(overrides)
    return base


class TestNewEntry:
    def test_builds_expected_shape(self) -> None:
        e = _entry()
        assert e["id"] == "gmail:m1"
        assert e["status"] == "PENDING_TRIAGE"
        assert e["company"] is None
        assert e["triage"] == {
            "reviewed_date": None,
            "resolved_company": None,
            "llm_score": None,
            "llm_explanation": None,
            "decision": None,
            "decision_note": None,
        }
        assert isinstance(e["keyword_score"], int)
        assert isinstance(e["is_relevant"], bool)

    def test_private_scope_leak_returns_none_not_the_entry(self, tmp_path) -> None:
        """A lead whose raw text trips the candidate's own privacy firewall is dropped whole."""
        import json

        from src.jobcore import profile as profile_mod
        from src.jobcore import scoring

        raw = profile_mod._skeleton()
        raw["privacy"] = {"never_mention": ["severance"], "private_dates": []}
        path = tmp_path / "profile.json"
        path.write_text(json.dumps(raw), encoding="utf-8")
        scoring.set_default_profile(profile_mod.load(path))  # the autouse fixture resets it
        result = email_leads.new_entry(
            gmail_message_id="m2",
            received_date="2026-08-20T09:14:00+03:00",
            sender="x@example.com",
            subject="subj",
            raw_text="details about the severance terms and next steps",
            role_title="Some Role",
        )
        assert result is None

    def test_company_passthrough(self) -> None:
        e = email_leads.new_entry(
            gmail_message_id="m3",
            received_date="2026-08-20T09:14:00+03:00",
            sender="x@example.com",
            subject="subj",
            raw_text="A named-company lead.",
            company="Acme Corp",
            role_title="Backend Lead",
        )
        assert e is not None
        assert e["company"] == "Acme Corp"


class TestSystemNoiseFiltering:
    def test_google_system_sender_auto_filtered(self) -> None:
        e = email_leads.new_entry(
            gmail_message_id="n1",
            received_date="2026-08-23T09:00:00+03:00",
            sender="Google <no-reply@mail.example.com>",
            subject="Security alert",
            raw_text="A new sign-in on your Google Account.",
        )
        assert e is not None
        assert e["status"] == "NOISE_AUTO_FILTERED"
        assert e["triage"]["decision"] == "auto_filtered_noise"
        assert e["triage"]["reviewed_date"] is not None

    def test_saas_welcome_sender_auto_filtered(self) -> None:
        e = email_leads.new_entry(
            gmail_message_id="n2",
            received_date="2026-08-23T09:00:00+03:00",
            sender="Exa Team <hello@saas.example.com>",
            subject="Welcome to the service, Jane!",
            raw_text="Thanks for signing up.",
        )
        assert e is not None
        assert e["status"] == "NOISE_AUTO_FILTERED"

    def test_gmail_forwarding_confirmation_auto_filtered(self) -> None:
        e = email_leads.new_entry(
            gmail_message_id="n3",
            received_date="2026-08-23T09:00:00+03:00",
            sender="Gmail Team <forwarding-noreply@mail.example.com>",
            subject="(Gmail Forwarding Confirmation - Receive Mail from owner@example.com)",
            raw_text="You requested a change to your forwarding settings.",
        )
        assert e is not None
        assert e["status"] == "NOISE_AUTO_FILTERED"

    def test_real_forwarded_lead_not_filtered(self) -> None:
        e = email_leads.new_entry(
            gmail_message_id="n4",
            received_date="2026-08-23T09:00:00+03:00",
            sender="Jane Smith <forwarder@example.com>",
            subject="Fwd: מקווה שזו הזדמנות:",
            raw_text="Backend Team Lead, Python/AWS, Tel Aviv.",
        )
        assert e is not None
        assert e["status"] == "PENDING_TRIAGE"

    def test_ambiguous_personal_subject_not_filtered(self) -> None:
        # A one-word subject from a person's own address is ambiguous, not classifiable
        # noise — must stay PENDING_TRIAGE so a human sees it, never silently dropped.
        e = email_leads.new_entry(
            gmail_message_id="n5",
            received_date="2026-08-23T09:00:00+03:00",
            sender="Jane Smith <forwarder@example.com>",
            subject="gcp",
            raw_text="reminder to check gcp billing",
        )
        assert e is not None
        assert e["status"] == "PENDING_TRIAGE"

    def test_pending_to_noise_auto_filtered_allowed_manually(self) -> None:
        # A human re-reading a PENDING_TRIAGE entry the classifier missed can still
        # mark it noise by hand — the classifier is a convenience, not the only path.
        e = email_leads.set_status(_entry(), "NOISE_AUTO_FILTERED")
        assert e["status"] == "NOISE_AUTO_FILTERED"

    def test_noise_status_is_terminal(self) -> None:
        e = email_leads.new_entry(
            gmail_message_id="n6",
            received_date="2026-08-23T09:00:00+03:00",
            sender="Google <no-reply@mail.example.com>",
            subject="Security alert",
            raw_text="A new sign-in on your Google Account.",
        )
        assert e is not None
        with pytest.raises(ValueError):
            email_leads.set_status(e, "PENDING_TRIAGE")


class TestRoleTitleFallbackForScoring:
    def test_empty_title_no_longer_guarantees_irrelevant(self) -> None:
        # Regression: before the subject-fallback fix, is_relevant() consulted only the
        # (always-empty at capture time) title field, so it returned False for every
        # single captured email regardless of content. A subject that itself carries a
        # real title should now be read.
        e = email_leads.new_entry(
            gmail_message_id="n7",
            received_date="2026-08-23T09:00:00+03:00",
            sender="recruiter@example.com",
            subject="Fwd: Backend Team Lead - AI Platform",
            raw_text="Backend Team Lead opening, Python/AWS, Tel Aviv, remote-friendly.",
        )
        assert e is not None
        assert e["is_relevant"] is True
        # role_title itself (the stored field) is untouched — only the internal
        # scoring read uses the subject fallback.
        assert e["role_title"] is None


class TestAppend:
    def test_appends_new_entry(self) -> None:
        entries = email_leads.append([], _entry())
        assert len(entries) == 1

    def test_dedupes_by_gmail_message_id(self) -> None:
        e1 = _entry()
        entries = email_leads.append([], e1)
        entries = email_leads.append(entries, _entry(subject="a redelivered copy"))
        assert len(entries) == 1
        assert entries[0]["subject"] == "Backend Team Lead role"  # first write wins

    def test_distinct_ids_both_kept(self) -> None:
        e1 = _entry()
        e2 = email_leads.new_entry(
            gmail_message_id="m9",
            received_date="2026-08-20T09:14:00+03:00",
            sender="x@example.com",
            subject="subj",
            raw_text="another lead",
        )
        assert e2 is not None
        entries = email_leads.append(email_leads.append([], e1), e2)
        assert len(entries) == 2


class TestSetStatus:
    def test_pending_to_needs_research_allowed(self) -> None:
        e = email_leads.set_status(_entry(), "NEEDS_RESEARCH")
        assert e["status"] == "NEEDS_RESEARCH"
        assert e["triage"]["reviewed_date"] is not None

    def test_pending_to_approved_allowed_direct(self) -> None:
        e = email_leads.set_status(_entry(), "APPROVED")
        assert e["status"] == "APPROVED"

    def test_needs_research_to_needs_human_allowed(self) -> None:
        e = email_leads.set_status(_entry(), "NEEDS_RESEARCH")
        e = email_leads.set_status(e, "NEEDS_HUMAN")
        assert e["status"] == "NEEDS_HUMAN"

    def test_illegal_transition_raises(self) -> None:
        e = email_leads.set_status(_entry(), "APPROVED")
        with pytest.raises(ValueError):
            email_leads.set_status(e, "DECLINED")

    def test_unknown_status_raises(self) -> None:
        with pytest.raises(ValueError):
            email_leads.set_status(_entry(), "BOGUS")

    def test_does_not_mutate_input(self) -> None:
        original = _entry()
        email_leads.set_status(original, "APPROVED")
        assert original["status"] == "PENDING_TRIAGE"


class TestSetTriageResult:
    def test_updates_only_given_fields(self) -> None:
        e = email_leads.set_triage_result(_entry(), llm_score=8, llm_explanation="strong fit")
        assert e["triage"]["llm_score"] == 8
        assert e["triage"]["llm_explanation"] == "strong fit"
        assert e["triage"]["decision"] is None

    def test_resolved_company_also_sets_top_level_company(self) -> None:
        e = email_leads.set_triage_result(_entry(), resolved_company="Acme Corp")
        assert e["company"] == "Acme Corp"
        assert e["triage"]["resolved_company"] == "Acme Corp"

    def test_does_not_mutate_input(self) -> None:
        original = _entry()
        email_leads.set_triage_result(original, llm_score=9)
        assert original["triage"]["llm_score"] is None


class TestLoadSave:
    def test_load_missing_file_returns_empty(self, tmp_path: Path) -> None:
        assert email_leads.load(tmp_path / "nope.json") == []

    def test_save_then_load_round_trips(self, tmp_path: Path) -> None:
        path = tmp_path / "_inbox" / "email_leads.json"
        entries = [_entry()]
        email_leads.save(entries, path)
        loaded = email_leads.load(path)
        assert loaded == entries

    def test_save_creates_parent_dir(self, tmp_path: Path) -> None:
        path = tmp_path / "a" / "b" / "email_leads.json"
        email_leads.save([], path)
        assert path.exists()
