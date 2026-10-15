"""Tests for the master-CV drift detector in jobcore.cv_sync."""

from __future__ import annotations

import json

from src.jobcore.cv_sync import find_drift, find_tailored_cvs, main, read_watchlist

MASTER_CV = """# Jane Smith

**Backend Team Leader | Python**

## PROFESSIONAL SUMMARY

Hands-on backend leader building Python services on AWS with Docker and Kafka.
"""


def _make_position(tmp_path, slug, cv_text):
    folder = tmp_path / "positions" / slug
    folder.mkdir(parents=True)
    (folder / "CV_TAILORED.md").write_text(cv_text, encoding="utf-8")
    return folder


class TestReadWatchlist:
    def test_skips_comments_and_blanks(self, tmp_path):
        path = tmp_path / "watchlist.txt"
        path.write_text("# a comment\n\nRAG\n  tool calling  \n\n# another\nGenAI\n", encoding="utf-8")
        assert read_watchlist(path) == ["RAG", "tool calling", "GenAI"]

    def test_missing_file_is_empty(self, tmp_path):
        assert read_watchlist(tmp_path / "nope.txt") == []


class TestFindTailoredCvs:
    def test_only_one_level_deep(self, tmp_path):
        _make_position(tmp_path, "acme", MASTER_CV)
        nested = tmp_path / "positions" / "_friends" / "or-rosh" / "role"
        nested.mkdir(parents=True)
        (nested / "CV_TAILORED.md").write_text(MASTER_CV, encoding="utf-8")

        found = find_tailored_cvs(tmp_path / "positions")

        assert [p.parent.name for p in found] == ["acme"]


class TestFindDrift:
    def test_term_missing_everywhere_is_not_drift(self):
        drift = find_drift(MASTER_CV, {"a": "python", "b": "python"}, ["RAG"], min_occurrences=2)
        assert drift == {}

    def test_term_present_in_master_is_never_drift(self):
        tailored = {"a": "we use Kafka here", "b": "Kafka again"}
        drift = find_drift(MASTER_CV, tailored, ["Kafka"], min_occurrences=2)
        assert "Kafka" not in drift

    def test_term_below_min_occurrences_is_not_drift(self):
        tailored = {"a": "we use RAG here", "b": "no ai terms here"}
        drift = find_drift(MASTER_CV, tailored, ["RAG"], min_occurrences=2)
        assert drift == {}

    def test_term_recurring_across_corpus_is_drift(self):
        tailored = {
            "a": "we use RAG pipelines here",
            "b": "another RAG-based system",
            "c": "no ai terms here",
        }
        drift = find_drift(MASTER_CV, tailored, ["RAG"], min_occurrences=2)
        assert drift == {"RAG": ["a", "b"]}

    def test_min_occurrences_is_configurable(self):
        tailored = {"a": "RAG here", "b": "RAG there", "c": "RAG everywhere"}
        assert find_drift(MASTER_CV, tailored, ["RAG"], min_occurrences=3) == {"RAG": ["a", "b", "c"]}
        assert find_drift(MASTER_CV, tailored, ["RAG"], min_occurrences=4) == {}


class TestMain:
    def _write_watchlist(self, tmp_path, terms):
        path = tmp_path / "watchlist.txt"
        path.write_text("\n".join(terms) + "\n", encoding="utf-8")
        return path

    def test_no_drift_exits_zero(self, tmp_path):
        master = tmp_path / "cv.md"
        master.write_text(MASTER_CV, encoding="utf-8")
        _make_position(tmp_path, "a", "python and aws")
        _make_position(tmp_path, "b", "python and docker")
        watchlist = self._write_watchlist(tmp_path, ["RAG"])

        rc = main(
            [
                "--master-cv",
                str(master),
                "--positions-root",
                str(tmp_path / "positions"),
                "--watchlist",
                str(watchlist),
            ]
        )
        assert rc == 0

    def test_drift_exits_one(self, tmp_path):
        master = tmp_path / "cv.md"
        master.write_text(MASTER_CV, encoding="utf-8")
        _make_position(tmp_path, "a", "we build RAG pipelines")
        _make_position(tmp_path, "b", "another RAG system")
        watchlist = self._write_watchlist(tmp_path, ["RAG"])

        rc = main(
            [
                "--master-cv",
                str(master),
                "--positions-root",
                str(tmp_path / "positions"),
                "--watchlist",
                str(watchlist),
            ]
        )
        assert rc == 1

    def test_json_mode_is_parseable(self, tmp_path, capsys):
        master = tmp_path / "cv.md"
        master.write_text(MASTER_CV, encoding="utf-8")
        _make_position(tmp_path, "a", "we build RAG pipelines")
        _make_position(tmp_path, "b", "another RAG system")
        watchlist = self._write_watchlist(tmp_path, ["RAG"])

        main(
            [
                "--master-cv",
                str(master),
                "--positions-root",
                str(tmp_path / "positions"),
                "--watchlist",
                str(watchlist),
                "--json",
            ]
        )
        payload = json.loads(capsys.readouterr().out)
        assert payload["drift"] == {"RAG": ["a", "b"]}
        assert payload["checked_positions"] == 2

    def test_missing_master_cv_returns_two(self, tmp_path):
        rc = main(["--master-cv", str(tmp_path / "nope.md")])
        assert rc == 2

    def test_missing_watchlist_returns_two(self, tmp_path):
        master = tmp_path / "cv.md"
        master.write_text(MASTER_CV, encoding="utf-8")
        rc = main(["--master-cv", str(master), "--watchlist", str(tmp_path / "nope.txt")])
        assert rc == 2

    def test_default_paths_never_raise(self):
        """Smoke test against whatever the working tree holds. A clean clone has no master CV, so
        the honest answer is exit 2 ("nothing to check") — never an exception, and never 0, which
        would read as "no drift". With real files present it is 0 (clean) or 1 (drift)."""
        rc = main([])
        assert rc in (0, 1, 2)
