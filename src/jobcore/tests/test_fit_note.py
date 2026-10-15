from src.jobcore import fit_note
from src.jobcore.fit_note import build_prompt, format_assessment


def test_build_prompt_includes_both_docs():
    p = build_prompt("JD: needs Kafka", "CV: 15 yrs Kafka")
    assert "JOB DESCRIPTION" in p and "needs Kafka" in p
    assert "TAILORED CV" in p and "15 yrs Kafka" in p


def test_build_prompt_truncates_long_inputs():
    p = build_prompt("x" * 10000, "y" * 10000)
    # JD capped at 4000, CV at 6000 → well under the raw 20000
    assert len(p) < 11000


def test_llm_assessment_returns_validated_dict(monkeypatch):
    monkeypatch.setattr(
        fit_note,
        "complete_structured_with_fallback",
        lambda *a, **k: (
            {"score": 8, "strongest_match": "Kafka depth", "gap": "no Node.js"},
            {"provider": "ollama", "model": None, "hop": 1},
        ),
    )
    result = fit_note.llm_assessment("jd", "cv")
    assert result["score"] == 8
    assert result["strongest_match"] == "Kafka depth"
    assert result["_answered_by"] == "hop 1: ollama"


def test_format_assessment_renders_all_fields():
    text = format_assessment(
        {"score": 7, "strongest_match": "AI platform work", "gap": "TypeScript depth", "_answered_by": "hop 1: ollama"}
    )
    assert "7/10" in text
    assert "AI platform work" in text
    assert "TypeScript depth" in text
    assert "ollama" in text
