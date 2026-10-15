"""Headhunter email-lead queue — `job-tracking/_inbox/email_leads.json`.

Deterministic, zero-LLM-call module (same bar as `pipeline.py`/`batch_guard.py`). Owns the
append-only queue the Gmail-poll Action writes to and the `email-lead-triage` skill reads from.
See `docs/superpowers/specs/2026-08-20-headhunter-email-intake-design.md` (Data Shape) and
`docs/superpowers/plans/2026-08-20-headhunter-email-intake-plan.md` (Task 1).

Entries are never deleted, including declines — the same append-only, never-overwrite-human-state
philosophy `pipeline.py` already enforces for the main ledger.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

from src.jobcore.gates import find_private_scope_leak
from src.jobcore.scoring import is_relevant as _is_relevant
from src.jobcore.scoring import score as _score

DEFAULT_PATH = Path("job-tracking/_inbox/email_leads.json")

#: Legal status transitions. Terminal states (APPROVED/DECLINED/NEEDS_HUMAN/NOISE_AUTO_FILTERED)
#: have none — matches the spec's "entries are never deleted, including declines" rule: once
#: decided, an entry's outcome is a historical fact, not a field to keep editing.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "PENDING_TRIAGE": {"NEEDS_RESEARCH", "APPROVED", "DECLINED", "NOISE_AUTO_FILTERED"},
    "NEEDS_RESEARCH": {"APPROVED", "DECLINED", "NEEDS_HUMAN"},
}

STATUSES = frozenset(
    {
        "PENDING_TRIAGE",
        "NEEDS_RESEARCH",
        "APPROVED",
        "DECLINED",
        "NEEDS_HUMAN",
        "NOISE_AUTO_FILTERED",
    }
)

# Real recruiter mail comes from a person's own address (firstname@example.com);
# automated system/service mail comes from a generic role-account local-part. Confirmed
# 2026-08-23 against a dedicated account's first live poll: every genuine forward
# (from a person's own real address) survives this, every Google/SaaS system notification
# (forwarding-noreply@, no-reply@mail.example.com, hello@saas.example.com, hello@news.example.com)
# matches it. Deliberately sender-shaped, not domain-specific — a new SaaS signed up on
# the dedicated mailbox later will still send from a "hello@"/"no-reply@" local-part.
_SYSTEM_SENDER_LOCALPART = re.compile(
    r"^(no-?reply|noreply-?\w*|forwarding-noreply|mailer-daemon|hello|support|"
    r"notifications?|alerts?|security|do-?not-?reply|welcome)@",
    re.I,
)
# Subject-line phrasing for known system notifications, matched regardless of sender —
# a second, independent signal so this doesn't rely on the sender heuristic alone.
_SYSTEM_SUBJECT = re.compile(
    r"gmail forwarding confirmation|"
    r"security alert|"
    r"you shared some google account data|"
    r"new sign-?in\b|"
    r"^welcome to \S",
    re.I,
)


_SUBJECT_PREFIX = re.compile(r"^\s*(fwd?|re)\s*:\s*", re.I)


def _is_system_noise(sender: str, subject: str) -> bool:
    """True for an automated service/system notification, never a real recruiter email.

    Deliberately narrow: only fires on well-known automated-sender shapes or system-
    notification phrasing. An ambiguous or personal-looking message (even a one-word
    subject like "gcp" from a person's own address) is left as ``PENDING_TRIAGE`` for a human
    to see — this must never be the mechanism that silently drops a real lead.
    """
    return bool(_SYSTEM_SENDER_LOCALPART.search(sender)) or bool(_SYSTEM_SUBJECT.search(subject))


def load(path: Path | str = DEFAULT_PATH) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8"))


def save(entries: list[dict], path: Path | str = DEFAULT_PATH) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(entries, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def new_entry(
    *,
    gmail_message_id: str,
    received_date: str,
    sender: str,
    subject: str,
    raw_text: str,
    company: str | None = None,
    role_title: str | None = None,
    now: datetime | None = None,
) -> dict | None:
    """Build one queue entry, or ``None`` if ``raw_text`` trips the private-scope gate.

    The gate's *return value* (the matched substrings) must never be logged, printed, or
    written anywhere — same "implementation trap" the Telegram spec's review caught. This
    function only branches on ``bool(...)``; callers must do the same and never print the
    list this function discards.
    """
    if find_private_scope_leak(raw_text):
        return None

    now = now or datetime.now(UTC)
    # role_title is almost never populated at capture time (no structured JD to parse
    # yet) — falling back to the subject line (stripped of Fwd:/Re:/Fw: prefixes) gives
    # is_relevant()/score() an actual title to read instead of "", which otherwise
    # guarantees is_relevant() == False for every single captured email regardless of
    # content (title is the only field it consults). Confirmed 2026-08-23: with an
    # empty title, a real 7/10-keyword-score lead still showed "Relevant: False".
    title_for_scoring = role_title or _SUBJECT_PREFIX.sub("", subject).strip()
    job = {"title": title_for_scoring, "domain": "", "snippet": raw_text[:2000]}

    is_noise = _is_system_noise(sender, subject)
    entry = {
        "id": f"gmail:{gmail_message_id}",
        "gmail_message_id": gmail_message_id,
        "received_date": received_date,
        "captured_date": now.isoformat(),
        "sender": sender,
        "subject": subject,
        "raw_text": raw_text,
        "company": company,
        "role_title": role_title,
        "keyword_score": _score(job),
        "is_relevant": _is_relevant(job),
        "status": "NOISE_AUTO_FILTERED" if is_noise else "PENDING_TRIAGE",
        "triage": {
            "reviewed_date": now.isoformat() if is_noise else None,
            "resolved_company": None,
            "llm_score": None,
            "llm_explanation": None,
            "decision": "auto_filtered_noise" if is_noise else None,
            "decision_note": (
                "system/service notification (sender or subject pattern), not a "
                "recruiter email — auto-classified at capture, never shown for triage"
                if is_noise
                else None
            ),
        },
        "position_folder": None,
        "reply_draft_path": None,
    }
    return entry


def append(entries: list[dict], entry: dict) -> list[dict]:
    """Append ``entry``, de-duplicated by ``gmail_message_id`` (a re-polled/redelivered
    message is a no-op, not a second row) — the queue's own version of the Gmail
    ``historyId`` de-dup the poller already does at the API-query level; this is the
    second, cheap backstop at the write boundary.
    """
    existing_ids = {e["gmail_message_id"] for e in entries}
    if entry["gmail_message_id"] in existing_ids:
        return entries
    return [*entries, entry]


def set_status(entry: dict, status: str, *, now: datetime | None = None) -> dict:
    """Return a copy of ``entry`` with its status transitioned, validated against the
    documented state machine. Illegal transitions raise ``ValueError`` — the same "state
    truth has an enforced home" discipline as ``pipeline.py``'s ``set_stage``.
    """
    if status not in STATUSES:
        raise ValueError(f"unknown status: {status!r}")
    current = entry["status"]
    allowed = _ALLOWED_TRANSITIONS.get(current, set())
    if status not in allowed:
        raise ValueError(f"illegal transition {current!r} -> {status!r}")
    updated = dict(entry)
    updated["status"] = status
    triage = dict(updated["triage"])
    triage["reviewed_date"] = (now or datetime.now(UTC)).isoformat()
    updated["triage"] = triage
    return updated


def set_triage_result(
    entry: dict,
    *,
    resolved_company: str | None = None,
    llm_score: int | None = None,
    llm_explanation: str | None = None,
    decision: str | None = None,
    decision_note: str | None = None,
) -> dict:
    """Return a copy of ``entry`` with its ``triage`` block updated. Does not itself change
    ``status`` — call :func:`set_status` separately so the transition is always validated.
    """
    updated = dict(entry)
    triage = dict(updated["triage"])
    if resolved_company is not None:
        triage["resolved_company"] = resolved_company
        updated["company"] = resolved_company
    if llm_score is not None:
        triage["llm_score"] = llm_score
    if llm_explanation is not None:
        triage["llm_explanation"] = llm_explanation
    if decision is not None:
        triage["decision"] = decision
    if decision_note is not None:
        triage["decision_note"] = decision_note
    updated["triage"] = triage
    return updated
