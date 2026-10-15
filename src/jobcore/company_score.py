"""Company-worth composite — deterministic, no LLM calls.

A weighted blend of employee-review signal (Glassdoor rating), red-flag count
found during research, and comp-band fit. Answers a DIFFERENT question from
:func:`~src.jobcore.scoring.score` (role-skill fit, "do I match the role"):
this is "is this company worth the pursuit."

Any factor company-researcher couldn't establish is dropped from the weighted
average and its weight redistributed across what IS known — never fabricated
as a default. ``red_flag_count`` is always a countable int (0 if none found),
so it can never itself be the missing factor.
"""

from __future__ import annotations

_WEIGHTS = {"glassdoor": 0.5, "red_flags": 0.3, "comp": 0.2}


def _clamp(value: float, lo: float = 0.0, hi: float = 10.0) -> float:
    return max(lo, min(hi, value))


def _glassdoor_component(rating: float | None) -> float | None:
    """None when no rating was found, else a 0-10 scale from the 0-5 rating."""
    if rating is None:
        return None
    return _clamp(rating / 5 * 10)


def _red_flag_component(red_flag_count: int) -> float:
    """10 at zero flags, -2 per flag, floored at 0. Always known."""
    return _clamp(10 - 2 * red_flag_count)


def _comp_component(comp_delta_pct: float | None) -> float | None:
    """None when no comp data was found, else a 0-10 scale from the band's
    percentage delta to the candidate's target.

    ``comp_delta_pct`` = (company band midpoint - target) / target * 100.
    0 (meets target) -> 10; every 5 points below target costs 1 point, floored
    at 0. Above-target deltas cap at 10 — no bonus for exceeding target.
    """
    if comp_delta_pct is None:
        return None
    return _clamp(10 + comp_delta_pct / 5)


def company_score(
    glassdoor_rating: float | None,
    red_flag_count: int,
    comp_delta_pct: float | None,
) -> dict:
    """Composite "is this company worth the pursuit" score, 1-10.

    Returns ``{"score": int, "confidence": "full" | "partial", "components":
    dict[str, float | None], "missing": list[str]}``. ``missing`` names any
    factor company-researcher couldn't establish, so the caller can show its
    work rather than present a single opaque number.
    """
    components = {
        "glassdoor": _glassdoor_component(glassdoor_rating),
        "red_flags": _red_flag_component(red_flag_count),
        "comp": _comp_component(comp_delta_pct),
    }
    present = {k: v for k, v in components.items() if v is not None}
    missing = sorted(k for k, v in components.items() if v is None)

    weight_total = sum(_WEIGHTS[k] for k in present)
    weighted = sum(v * _WEIGHTS[k] for k, v in present.items()) / weight_total
    score = int(round(_clamp(weighted, 1, 10)))

    return {
        "score": score,
        "confidence": "full" if not missing else "partial",
        "components": components,
        "missing": missing,
    }
