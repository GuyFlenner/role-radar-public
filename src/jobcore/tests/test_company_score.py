"""Golden-input tests for the company-worth composite (Glassdoor + red flags +
comp-band fit). A different question from scoring.score (role-skill fit):
"is this company worth the pursuit."
"""

from src.jobcore.company_score import company_score


def test_full_confidence_when_all_three_present():
    result = company_score(glassdoor_rating=4.0, red_flag_count=0, comp_delta_pct=0)
    assert result["confidence"] == "full"
    assert result["missing"] == []
    assert result["score"] == 9


def test_missing_glassdoor_gives_partial_confidence_and_reweights():
    result = company_score(glassdoor_rating=None, red_flag_count=1, comp_delta_pct=-10)
    assert result["confidence"] == "partial"
    assert result["missing"] == ["glassdoor"]
    assert result["score"] == 8


def test_heavy_red_flags_floors_component_at_zero():
    result = company_score(glassdoor_rating=3.0, red_flag_count=6, comp_delta_pct=None)
    assert result["components"]["red_flags"] == 0
    assert result["missing"] == ["comp"]
    assert result["score"] == 4


def test_comp_far_below_target_floors_at_zero():
    result = company_score(glassdoor_rating=4.0, red_flag_count=0, comp_delta_pct=-100)
    assert result["components"]["comp"] == 0


def test_comp_far_above_target_caps_at_ten():
    result = company_score(glassdoor_rating=4.0, red_flag_count=0, comp_delta_pct=50)
    assert result["components"]["comp"] == 10


def test_missing_comp_and_glassdoor_still_scores_from_red_flags_alone():
    result = company_score(glassdoor_rating=None, red_flag_count=0, comp_delta_pct=None)
    assert sorted(result["missing"]) == ["comp", "glassdoor"]
    assert result["confidence"] == "partial"
    assert result["score"] == 10
