"""The diagram gate, and proof it refuses.

`drawio_lint.py` was 900 lines with 54 tests and wired into NOTHING — no verify_all.sh step, no
CI step, no pre-push hook. A diagram could ship with arrows through boxes and text invisible on a
dark canvas while every gate said green. Same "dormant checker" shape as `check_parity.py`: the
capability existed, the enforcement did not.
"""

from __future__ import annotations

from pathlib import Path

from ..diagram_gate import main, run, tracked_diagrams

BROKEN = """<mxfile host="app.diagrams.net">
  <diagram name="Broken" id="b1">
    <mxGraphModel dx="800" dy="600" pageWidth="800" pageHeight="600">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="c1" value="A" style="rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontColor=#000000;" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="200" height="100" as="geometry"/>
        </mxCell>
        <mxCell id="c2" value="B overlapping A" style="rounded=1;fillColor=#dae8fc;strokeColor=#6c8ebf;fontColor=#000000;" vertex="1" parent="1">
          <mxGeometry x="120" y="80" width="200" height="100" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>
"""


class TestTheRealRepoDiagramIsClean:
    def test_shipped_diagrams_have_no_errors(self) -> None:
        status, messages, code = run()
        assert code == 0, "\n".join(m for m in messages if " ERROR " in m)
        assert status in ("PASS", "SKIPPED")

    def test_the_architecture_diagram_is_tracked(self) -> None:
        """A diagram nobody committed is a diagram the gate never sees."""
        names = [p.name for p in tracked_diagrams()]
        assert "role_radar_architecture.drawio" in names, names

    def test_status_is_pass_not_skipped(self) -> None:
        """Once a diagram exists, SKIPPED would mean the gate stopped looking."""
        status, _messages, _code = run()
        assert status == "PASS", "the repo has a tracked diagram, so the gate must actually run"

    def test_cli_exits_zero(self) -> None:
        assert main([]) == 0


class TestItActuallyRefuses:
    def test_a_diagram_with_overlapping_boxes_fails(self, tmp_path: Path) -> None:
        """The gate must be capable of failing, or it proves nothing."""
        from ..drawio_lint import lint_file

        bad = tmp_path / "bad.drawio"
        bad.write_text(BROKEN, encoding="utf-8")
        errors, _warns = lint_file(bad)
        assert errors, "a 80x60px box overlap produced no error"
        assert any("overlap" in e for e in errors), errors


class TestZeroDiagramsIsSkippedNotPass:
    """Reporting PASS over zero files is the `goldens 0/0` defect in another costume."""

    def test_empty_root_reports_skipped(self, tmp_path: Path) -> None:
        status, messages, code = run(root=tmp_path)
        assert status == "SKIPPED"
        assert "NOT checked" in messages[0]

    def test_skipped_exits_zero_so_a_diagramless_repo_is_not_permanently_red(self, tmp_path: Path) -> None:
        """Deliberate: a red gate on a repo with no diagrams gets deleted, not fixed.

        The honesty comes from printing SKIPPED rather than PASS — the summary line cannot be
        mistaken for verification that did not happen.
        """
        _status, _messages, code = run(root=tmp_path)
        assert code == 0

    def test_skipped_never_prints_the_word_pass(self, tmp_path: Path, capsys) -> None:
        main(["--root", str(tmp_path)])
        out = capsys.readouterr().out
        assert "SKIPPED" in out
        assert "PASS" not in out
