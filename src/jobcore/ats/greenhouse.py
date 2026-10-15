"""Greenhouse public Job Board API provider.

Endpoint (no auth — Greenhouse docs: "Job Board data is publicly available"):
    GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true

`{token}` is the board slug in boards.greenhouse.io/<token>. `content=true`
includes the HTML job description so we can build a snippet the scorer reads.
"""

from __future__ import annotations

from .base import http_get_json, iso_date, make_raw, strip_html

BOARD_URL = "https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true"


def parse(company: str, payload: dict | None) -> list[dict]:
    """Pure: Greenhouse jobs payload → raw job dicts. Defensive to missing fields."""
    if not payload:
        return []
    out: list[dict] = []
    for j in payload.get("jobs", []) or []:
        title = j.get("title") or ""
        if not title:
            continue
        location = (j.get("location") or {}).get("name", "") if isinstance(j.get("location"), dict) else ""
        posted = iso_date(j.get("updated_at") or j.get("first_published"))
        out.append(
            make_raw(
                company=company,
                title=title,
                url=j.get("absolute_url", ""),
                source=f"greenhouse:{company}",
                location=location,
                snippet=strip_html(j.get("content", "")),
                posted_date=posted,
                job_id=str(j.get("id") or ""),
            )
        )
    return out


def fetch(target: dict, get_json=http_get_json) -> list[dict]:
    """Fetch one Greenhouse board. `target` needs {company, token}."""
    token = target.get("token")
    if not token:
        return []
    payload = get_json(BOARD_URL.format(token=token))
    return parse(target.get("company", token), payload)
