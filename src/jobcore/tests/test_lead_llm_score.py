"""Tests for src.jobcore.lead_llm_score.

The one real-network function (complete_structured, imported from jobcore.llm) is
monkeypatched throughout -- no real OpenRouter key or network access required.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.jobcore import lead_llm_score


class TestModelIsFreeTier:
    def test_hardcoded_model_carries_free_suffix(self) -> None:
        # The whole point of this module -- if this ever regresses to a paid model,
        # every other safety property in the docstring stops being true.
        assert lead_llm_score.MODEL.endswith(":free")


class TestBuildPrompt:
    def test_includes_both_texts(self) -> None:
        prompt = lead_llm_score.build_prompt("A recruiter pitch for a backend role", "My CV content here")
        assert "A recruiter pitch for a backend role" in prompt
        assert "My CV content here" in prompt

    def test_truncates_long_inputs(self) -> None:
        prompt = lead_llm_score.build_prompt("x" * 10000, "y" * 10000)
        assert len(prompt) < 20000  # both inputs capped, not passed through raw


class TestScoreLead:
    def test_returns_parsed_assessment(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cv_path = tmp_path / "cv.md"
        cv_path.write_text("# CV\nAI engineering leader.", encoding="utf-8")

        calls = []

        def fake_complete_structured(system, user, schema, **kwargs):
            calls.append((system, user, schema, kwargs))
            return {"score": 8, "explanation": "Strong AI/backend match."}

        monkeypatch.setattr(lead_llm_score, "complete_structured", fake_complete_structured)

        result = lead_llm_score.score_lead("Backend AI team lead role", cv_path, api_key="fake-key")

        assert result == {"score": 8, "explanation": "Strong AI/backend match."}
        _, _, _, kwargs = calls[0]
        assert kwargs["provider"] == "openrouter"
        assert kwargs["model"] == lead_llm_score.MODEL
        assert kwargs["api_key"] == "fake-key"

    def test_rejects_non_free_model(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cv_path = tmp_path / "cv.md"
        cv_path.write_text("CV", encoding="utf-8")
        monkeypatch.setattr(lead_llm_score, "MODEL", "openrouter/some-paid-model")

        with pytest.raises(RuntimeError, match=":free"):
            lead_llm_score.score_lead("lead text", cv_path, api_key="fake-key")

    def test_propagates_provider_errors(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        cv_path = tmp_path / "cv.md"
        cv_path.write_text("CV", encoding="utf-8")

        def raising_complete_structured(*a: object, **k: object) -> dict:
            raise RuntimeError("provider error: 401")

        monkeypatch.setattr(lead_llm_score, "complete_structured", raising_complete_structured)

        with pytest.raises(RuntimeError, match="401"):
            lead_llm_score.score_lead("lead text", cv_path, api_key="fake-key")


class TestMain:
    def test_missing_key_returns_1(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setattr("sys.argv", ["lead_llm_score", "lead text", str(tmp_path / "cv.md")])

        assert lead_llm_score.main() == 1
        assert "OPENROUTER_API_KEY" in capsys.readouterr().err
