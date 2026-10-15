"""Comeet (Spark Hire Recruit) public Careers API provider.

Endpoint (per-company public token — the seeker-usable path, not the
partner-gated B2B "Job Board API"):
    GET https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}

Returns a JSON list of positions. Field shapes vary across Comeet versions, so
every access is defensive.
"""

from __future__ import annotations

from .base import http_get_json, iso_date, make_raw, strip_html

POSITIONS_URL = "https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}"


def _location(pos: dict) -> str:
    loc = pos.get("location")
    if isinstance(loc, dict):
        return loc.get("name") or ", ".join(filter(None, [loc.get("city"), loc.get("country")]))
    if isinstance(loc, str):
        return loc
    return ""


def _snippet(pos: dict) -> str:
    details = pos.get("details")
    if isinstance(details, list):
        chunks = [d.get("value", "") for d in details if isinstance(d, dict)]
        return strip_html(" ".join(chunks))
    return strip_html(pos.get("description", "") or "")


def parse(company: str, payload) -> list[dict]:
    """Pure: Comeet positions payload (a list) → raw job dicts."""
    if not isinstance(payload, list):
        return []
    out: list[dict] = []
    for p in payload:
        if not isinstance(p, dict):
            continue
        title = p.get("name") or p.get("position_name") or ""
        if not title:
            continue
        url = p.get("url_comeet_hosted_page") or p.get("url_active_page") or p.get("url") or ""
        posted = iso_date(p.get("time_updated") or p.get("updated_at") or p.get("time_created"))
        out.append(
            make_raw(
                company=company,
                title=title,
                url=url,
                source=f"comeet:{company}",
                location=_location(p),
                domain=p.get("department", "") or "",
                snippet=_snippet(p),
                posted_date=posted,
                job_id=str(p.get("uid") or p.get("id") or ""),
            )
        )
    return out


def fetch(target: dict, get_json=http_get_json) -> list[dict]:
    """Fetch one Comeet board. `target` needs {company, uid, token}."""
    uid, token = target.get("uid"), target.get("token")
    if not uid or not token:
        return []
    payload = get_json(POSITIONS_URL.format(uid=uid, token=token))
    return parse(target.get("company", uid), payload)
