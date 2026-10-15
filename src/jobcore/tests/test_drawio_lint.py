"""Regression tests for the draw.io UX lint.

Every test here except the "clean" ones encodes a defect that ESCAPED the previous
version of the gate and reached a real prep diagram (ExampleCorp customer portal,
2026-07-27): black-on-canvas titles that vanish in dark theme, and arrows drawn
straight through component boxes. The old lint reported "0 errors" on that file
because it never checked colour at all and skipped every waypoint-less edge.
"""

from __future__ import annotations

import textwrap

import pytest

from src.jobcore.drawio_lint import check_vertical_gaps, contrast_ratio, lint_file, seg_through_box

EXEMPLAR_DIAGRAM = "docs/examples/exemplar_architecture.drawio"


def _write(tmp_path, cells, page="1200x900"):
    w, h = page.split("x")
    xml = textwrap.dedent(f"""\
        <mxfile host="test">
          <diagram name="p1">
            <mxGraphModel pageWidth="{w}" pageHeight="{h}">
              <root>
                <mxCell id="0"/><mxCell id="1" parent="0"/>
                {cells}
              </root>
            </mxGraphModel>
          </diagram>
        </mxfile>
        """)
    p = tmp_path / "d.drawio"
    p.write_text(xml, encoding="utf-8")
    return str(p)


def _box(cid, x, y, w=100, h=50, label="n", style="fillColor=#ffffff;fontColor=#000000;"):
    return (
        f'<mxCell id="{cid}" value="{label}" style="{style}" vertex="1" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>'
    )


def _edge(eid, s, t, label="", style="", points=None):
    pts = ""
    if points:
        inner = "".join(f'<mxPoint x="{x}" y="{y}" as="point"/>' for x, y in points)
        pts = f'<Array as="points">{inner}</Array>'
    return (
        f'<mxCell id="{eid}" value="{label}" style="{style}" edge="1" parent="1" '
        f'source="{s}" target="{t}"><mxGeometry relative="1" as="geometry">{pts}</mxGeometry></mxCell>'
    )


def _detached_edge(eid, target, at=(100, 100), label=""):
    """What draw.io writes when you drag an edge endpoint off its node in the GUI."""
    return (
        f'<mxCell id="{eid}" value="{label}" style="edgeStyle=orthogonalEdgeStyle;" edge="1" '
        f'parent="1" target="{target}"><mxGeometry relative="1" as="geometry">'
        f'<mxPoint x="{at[0]}" y="{at[1]}" as="sourcePoint"/></mxGeometry></mxCell>'
    )


# --- contrast / dark-theme safety (was NOT checked at all) -------------------


def test_text_with_no_fill_is_flagged_as_invisible_on_canvas(tmp_path):
    """A bare text cell renders on the canvas -> black-on-black in dark theme."""
    f = _write(tmp_path, _box("t1", 40, 10, 400, 40, "Title", style="text;fontColor=#000000;"))
    errors, _ = lint_file(f)
    assert any("invisible-on-canvas" in e and "t1" in e for e in errors), errors


def test_fillcolor_none_is_also_flagged(tmp_path):
    f = _write(tmp_path, _box("t1", 40, 10, 400, 40, "Title", style="fillColor=none;fontColor=#000000;"))
    errors, _ = lint_file(f)
    assert any("invisible-on-canvas" in e for e in errors), errors


def test_low_contrast_text_on_its_own_fill_is_flagged(tmp_path):
    """Explicit fill is not enough -- dark text on a dark fill is still unreadable."""
    f = _write(tmp_path, _box("b1", 40, 10, 200, 50, "Hi", style="fillColor=#222222;fontColor=#000000;"))
    errors, _ = lint_file(f)
    assert any("low-contrast" in e for e in errors), errors


def test_readable_text_passes(tmp_path):
    f = _write(tmp_path, _box("b1", 40, 10, 200, 50, "Hi", style="fillColor=#dae8fc;fontColor=#000000;"))
    errors, _ = lint_file(f)
    assert not [e for e in errors if "contrast" in e or "invisible" in e], errors


def test_contrast_ratio_matches_wcag_reference():
    assert contrast_ratio("#000000", "#ffffff") == pytest.approx(21.0, abs=0.05)
    assert contrast_ratio("#ffffff", "#ffffff") == pytest.approx(1.0, abs=0.01)
    assert contrast_ratio("#000000", "not-a-colour") is None


def test_unlabelled_cell_is_not_flagged(tmp_path):
    """Spacers/backgrounds carry no text, so they cannot have a contrast problem."""
    f = _write(tmp_path, _box("bg", 0, 0, 300, 200, "", style="fillColor=none;"))
    errors, _ = lint_file(f)
    assert not [e for e in errors if "invisible" in e or "contrast" in e], errors


# --- edge routing (waypoint-less edges were skipped entirely) ----------------


def test_waypointless_edge_straight_through_a_box_is_flagged(tmp_path):
    """A -> C with B exactly between them. The old lint did `if not pts: continue`."""
    cells = (
        _box("a", 0, 100, 100, 50) + _box("b", 200, 100, 100, 50) + _box("c", 400, 100, 100, 50) + _edge("e1", "a", "c")
    )
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert any("NO clear auto-route" in e and "e1" in e for e in errors), errors


def test_edge_with_clear_route_is_not_flagged(tmp_path):
    cells = _box("a", 0, 100, 100, 50) + _box("c", 400, 100, 100, 50) + _edge("e1", "a", "c")
    f = _write(tmp_path, cells)
    errors, warns = lint_file(f)
    assert not [e for e in errors if "auto-route" in e], errors
    assert not [w for w in warns if "auto-routes cross" in w], warns


def test_edge_routed_around_an_obstacle_with_waypoints_passes(tmp_path):
    """The documented fix for the above: pin waypoints through a clear gutter."""
    cells = (
        _box("a", 0, 100, 100, 50)
        + _box("b", 200, 100, 100, 50)
        + _box("c", 400, 100, 100, 50)
        + _edge("e1", "a", "c", points=[(250, 20), (450, 20)])
    )
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert not [e for e in errors if "e1" in e], errors


def test_diagonal_segment_is_checked(tmp_path):
    """The old seg_through_box returned False for anything not axis-aligned."""
    assert seg_through_box((0, 0), (200, 200), (80, 80, 60, 60)) is True
    cells = (
        _box("a", 0, 0, 60, 40)
        + _box("b", 200, 200, 80, 60)
        + _box("c", 500, 500, 60, 40)
        + _edge("e1", "a", "c", points=[(240, 230)])
    )
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert any("crosses box b" in e for e in errors), errors


# --- structural exclusions ---------------------------------------------------


def test_full_width_band_label_is_not_an_overlap_or_obstacle(tmp_path):
    """Section dividers span the page by design; arrows legitimately cross them."""
    cells = (
        _box("L1", 40, 100, 900, 20, "-- section --", style="fillColor=#eeeeee;fontColor=#000000;")
        + _box("a", 100, 40, 100, 40)
        + _box("c", 100, 200, 100, 40)
        + _edge("e1", "a", "c")
    )
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert not [e for e in errors if "overlap" in e or "auto-route" in e], errors


def test_box_overlap_still_detected(tmp_path):
    cells = _box("a", 0, 0, 200, 100) + _box("b", 100, 50, 200, 100)
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert any("overlap" in e for e in errors), errors


def test_content_beyond_page_still_detected(tmp_path):
    f = _write(tmp_path, _box("a", 1150, 10, 400, 50), page="1200x900")
    errors, _ = lint_file(f)
    assert any("exceeds pageWidth" in e for e in errors), errors


# --- GUI-edit damage (all found on a hand-fixed diagram, 2026-07-28) ---------


def test_detached_edge_endpoint_is_flagged(tmp_path):
    """Dragging an endpoint in the draw.io GUI silently unbinds the edge.

    draw.io drops source= and writes a floating mxPoint as="sourcePoint". The line
    still looks connected but models nothing. Six edges on the portal diagram were
    broken this way and the lint said nothing, because it did
    `if s not in boxes: continue`.
    """
    cells = _box("a", 0, 0) + _box("b", 400, 0) + _detached_edge("e1", "b", label="route AI query")
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert any("detached edge e1" in e and "source" in e for e in errors), errors


def test_attached_edge_is_not_flagged_as_detached(tmp_path):
    cells = _box("a", 0, 0) + _box("b", 400, 0) + _edge("e1", "a", "b")
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert not [e for e in errors if "detached" in e], errors


def test_negative_coordinates_are_flagged(tmp_path):
    """Only the right/bottom page edges were checked, so a node dragged to
    x=-200 (which happened to Identity Platform) was silently accepted."""
    f = _write(tmp_path, _box("p6", -200, 180, 240, 50, "Identity Platform"))
    errors, _ = lint_file(f)
    assert any("off-page p6" in e for e in errors), errors


def test_negative_y_is_flagged(tmp_path):
    f = _write(tmp_path, _box("t1", 40, -30, 400, 70, "Title"))
    errors, _ = lint_file(f)
    assert any("off-page t1" in e for e in errors), errors


def test_label_too_big_for_its_box_is_flagged(tmp_path):
    """A title that does not fit clips or spills over the row beneath it."""
    long = "ExampleCorp Customer Portal - PROPOSED component/flow on their ACTUAL stack " * 3
    f = _write(tmp_path, _box("t1", 40, 10, 300, 20, long, style="fillColor=#eef2f7;fontColor=#000000;fontSize=12;"))
    _, warns = lint_file(f)
    assert any("label overflow" in w for w in warns), warns


def test_label_that_fits_is_not_flagged(tmp_path):
    f = _write(tmp_path, _box("b", 40, 10, 400, 60, "Short", style="fillColor=#ffffff;fontColor=#000000;fontSize=12;"))
    _, warns = lint_file(f)
    assert not [w for w in warns if "label overflow" in w], warns


def test_swimlane_title_overflow_is_flagged(tmp_path):
    """A swimlane title only occupies the startSize header strip and never wraps --
    the old check measured wrapped-line height against the container's FULL body
    height, so a tall zone box gave a long title effectively unlimited room and the
    check never fired. Escaped to a real diagram, 2026-08-03 (grip deployment-topology
    page): 'CONSUMER FLEET -- EKS Deployments (horizontally scaled, KEDA on
    consumer-group lag)' in a 340px-wide, 30px-tall header on a 600px-tall zone."""
    long_title = "CONSUMER FLEET -- EKS Deployments (horizontally scaled, KEDA on consumer-group lag)"
    cells = _box(
        "z1",
        40,
        100,
        340,
        600,
        long_title,
        style="swimlane;fontStyle=1;align=center;verticalAlign=top;startSize=30;container=1;"
        "collapsible=0;fillColor=#f5f5f5;strokeColor=#82b366;fontColor=#000000;fontSize=13;",
    )
    f = _write(tmp_path, cells)
    _, warns = lint_file(f)
    assert any("swimlane title overflow" in w and "z1" in w for w in warns), warns


def test_swimlane_short_title_is_not_flagged(tmp_path):
    cells = _box(
        "z1",
        40,
        100,
        340,
        600,
        "STORAGE",
        style="swimlane;fontStyle=1;align=center;verticalAlign=top;startSize=30;container=1;"
        "collapsible=0;fillColor=#f5f5f5;strokeColor=#666666;fontColor=#000000;fontSize=13;",
    )
    f = _write(tmp_path, cells)
    _, warns = lint_file(f)
    assert not [w for w in warns if "swimlane title overflow" in w], warns


def test_tight_vertical_gap_between_stacked_boxes_is_flagged(tmp_path):
    """Only horizontal gaps were checked; stacked rows 10px apart passed."""
    cells = _box("a", 100, 0, 200, 50) + _box("b", 100, 60, 200, 50)
    f = _write(tmp_path, cells)
    _, warns = lint_file(f)
    assert any("tight vertical gap" in w for w in warns), warns


def test_roomy_vertical_gap_passes(tmp_path):
    cells = _box("a", 100, 0, 200, 50) + _box("b", 100, 150, 200, 50)
    f = _write(tmp_path, cells)
    _, warns = lint_file(f)
    assert not [w for w in warns if "tight vertical gap" in w], warns


def test_duplicate_consecutive_waypoints_are_flagged(tmp_path):
    """Editor drag artefact -- q3 ended up with three identical points."""
    cells = _box("a", 0, 0) + _box("b", 600, 0) + _edge("e1", "a", "b", points=[(290, 230), (290, 230)])
    f = _write(tmp_path, cells)
    _, warns = lint_file(f)
    assert any("degenerate waypoints" in w for w in warns), warns


def test_endpoint_markers_are_not_treated_as_waypoints(tmp_path):
    """as="sourcePoint" is an endpoint, not a bend. Counting it as a waypoint
    corrupted the routing check's idea of the path."""
    cells = _box("a", 0, 0) + _box("b", 400, 0) + _detached_edge("e1", "b", at=(9999, 9999))
    f = _write(tmp_path, cells)
    _, warns = lint_file(f)
    assert not [w for w in warns if "degenerate" in w], warns


# --- grouped cells: false-positive guards (lint bug, 2026-07-28) -------------


def _group(gid, x, y, w, h, children):
    """An mxGraph group: children carry coordinates RELATIVE to the group origin."""
    kids = "".join(
        f'<mxCell id="{cid}" value="{cid}" style="fillColor=#ffffff;fontColor=#000000;" vertex="1" '
        f'parent="{gid}"><mxGeometry x="{cx}" y="{cy}" width="{cw}" height="{ch}" as="geometry"/></mxCell>'
        for cid, cx, cy, cw, ch in children
    )
    return (
        f'<mxCell id="{gid}" value="" style="group" vertex="1" connectable="0" parent="1">'
        f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>' + kids
    )


def test_child_coordinates_are_resolved_relative_to_their_group(tmp_path):
    """Children of two separate groups both start at x=10 in their own frame.

    Read as absolute they collide; resolved against the parent origin they are
    500px apart. Treating them as absolute produced ~150 phantom overlaps on
    whiteboard_skeleton.drawio -- a gate that cries wolf is worse than no gate.
    """
    cells = _group("gA", 0, 0, 200, 100, [("a1", 10, 10, 80, 40)]) + _group(
        "gB", 500, 0, 200, 100, [("b1", 10, 10, 80, 40)]
    )
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert not [e for e in errors if "overlap" in e], errors


def test_a_group_is_not_reported_as_overlapping_its_own_children(tmp_path):
    """A group box encloses its children by definition -- that is not an overlap."""
    cells = _group("gA", 0, 0, 300, 200, [("a1", 10, 10, 80, 40), ("a2", 120, 10, 80, 40)])
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert not [e for e in errors if "overlap" in e], errors


def test_partial_geometry_does_not_crash_the_lint(tmp_path):
    """mxGraph allows a vertex with incomplete geometry (relative / auto-sized).

    A real file had a cell with x but no y and the lint died with a TypeError.
    A gate that crashes on valid input is worse than one that misses a case.
    """
    cells = (
        '<mxCell id="odd" value="partial" style="fillColor=#ffffff;fontColor=#000000;" vertex="1" '
        'parent="1"><mxGeometry x="10" as="geometry"/></mxCell>' + _box("ok", 200, 200)
    )
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)  # must not raise
    assert isinstance(errors, list)


def test_real_overlap_inside_a_group_is_still_detected(tmp_path):
    """The fix must not mask genuine collisions between siblings in a group."""
    cells = _group("gA", 0, 0, 300, 200, [("a1", 10, 10, 100, 60), ("a2", 50, 30, 100, 60)])
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert any("overlap" in e for e in errors), errors


# --- edge visibility & clutter (found by eye, 2026-07-28) --------------------


def test_near_black_edge_is_flagged_as_invisible_on_dark(tmp_path):
    """The contrast check covered TEXT only, so 18 near-black arrows shipped in a
    diagram the lint called clean and vanished on a dark canvas."""
    cells = _box("a", 0, 0) + _box("b", 400, 0) + _edge("e1", "a", "b", style="strokeColor=#444444;")
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert any("invisible edge e1" in e for e in errors), errors


def test_near_white_edge_is_flagged_as_invisible_on_light(tmp_path):
    cells = _box("a", 0, 0) + _box("b", 400, 0) + _edge("e1", "a", "b", style="strokeColor=#f2f2f2;")
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert any("invisible edge e1" in e for e in errors), errors


def test_dual_theme_safe_edge_passes(tmp_path):
    """A mid-tone clears 3:1 against white AND near-black."""
    cells = _box("a", 0, 0) + _box("b", 400, 0) + _edge("e1", "a", "b", style="strokeColor=#2E74B5;")
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert not [e for e in errors if "invisible edge" in e], errors


def test_edge_grazing_a_box_is_flagged(tmp_path):
    """Non-intersection is not enough -- a line 2px off a border reads as touching."""
    cells = (
        _box("a", 0, 100, 100, 50)
        + _box("b", 200, 133, 100, 50)  # top edge 8px under the a->c line at y=125
        + _box("c", 400, 100, 100, 50)
        + _edge("e1", "a", "c", style="strokeColor=#2E74B5;")
    )
    f = _write(tmp_path, cells)
    _, warns = lint_file(f)
    assert any("passes within" in w for w in warns), warns


def test_edge_label_landing_on_a_box_is_flagged(tmp_path):
    """Closes part of the documented 'labels are not machine-checkable' gap."""
    cells = (
        _box("a", 0, 100, 100, 50)
        + _box("b", 240, 90, 120, 70, label="in the way")
        + _box("c", 500, 100, 100, 50)
        + _edge("e1", "a", "c", label="a fairly long edge label", style="strokeColor=#2E74B5;", points=[(300, 125)])
    )
    f = _write(tmp_path, cells)
    _, warns = lint_file(f)
    assert any("likely lands on" in w for w in warns), warns


def test_object_wrapped_label_is_read(tmp_path):
    """draw.io moves the label onto <object> when a cell carries tooltip/link.

    Reading only mxCell/@value made those cells look unlabelled, silently skipping
    the contrast and label-fit checks on whole files.
    """
    cells = (
        '<object id="o1" label="Title" tooltip="detail here"><mxCell style="text;fontColor=#000000;" '
        'vertex="1" parent="1"><mxGeometry x="40" y="10" width="300" height="40" as="geometry"/></mxCell></object>'
    )
    f = _write(tmp_path, cells)
    errors, _ = lint_file(f)
    assert any("invisible-on-canvas" in e and "o1" in e for e in errors), errors


# --- the golden guardrail ----------------------------------------------------


def test_near_duplicate_of_a_palette_colour_is_an_error(tmp_path):
    """#e8f0fb is 16 RGB units from service-blue #dae8fc — that is drift, not a choice."""
    p = _write(tmp_path, _box("a", 100, 100, label="Svc", style="fillColor=#e8f0fb;fontColor=#000000;"))
    errors, _ = lint_file(p)
    assert any("near-duplicate" in e and "#dae8fc" in e for e in errors), errors  # nearest ROLE colour


def test_palette_colour_passes_clean(tmp_path):
    p = _write(tmp_path, _box("a", 100, 100, label="Svc", style="fillColor=#dae8fc;fontColor=#000000;"))
    errors, _ = lint_file(p)
    assert not any("palette" in e or "near-duplicate" in e for e in errors), errors


def test_genuinely_novel_fill_warns_but_does_not_error(tmp_path):
    """A far-from-palette colour may be deliberate — ask for a legend, don't block."""
    p = _write(tmp_path, _box("a", 100, 100, label="Odd", style="fillColor=#3b0a45;fontColor=#ffffff;"))
    errors, warns = lint_file(p)
    assert not any("near-duplicate" in e for e in errors), errors
    assert any("not in the semantic palette" in w for w in warns), warns


def test_the_pale_blue_drift_family_from_the_audit_is_all_flagged(tmp_path):
    """Regression for the exact drift the 2026-08-01 audit surfaced.

    The requirement is that every one is FLAGGED, not that every one is an ERROR: the closest
    (<20 RGB units) are unambiguous drift and error; the more distant are warned as off-palette.
    Both surface it to the author, which is the point.
    """
    drift = ["#e8f0fb", "#eef3fb", "#eaf2ff", "#f4f7fb", "#eef6ff", "#e6ebf2"]
    cells = "".join(
        _box(
            f"b{i}", 60 + (i % 3) * 300, 100 + (i // 3) * 300, label=f"n{i}", style=f"fillColor={c};fontColor=#000000;"
        )
        for i, c in enumerate(drift)
    )
    errors, warns = lint_file(_write(tmp_path, cells, page="1200x900"))
    found = errors + warns
    for c in drift:
        assert any(c in f for f in found), f"{c} was not flagged at all: {found}"
    # The nearest ones must be hard errors, not merely advisory.
    for c in ("#e8f0fb", "#eaf2ff", "#e6ebf2"):
        assert any(c in e and "near-duplicate" in e for e in errors), f"{c} should ERROR: {errors}"


def test_missing_legend_warns_when_several_role_colours_used(tmp_path):
    roles = ["#dae8fc", "#fff2cc", "#ffe6cc", "#d5e8d4"]
    cells = "".join(
        _box(f"r{i}", 60 + i * 220, 100, label=f"n{i}", style=f"fillColor={c};fontColor=#000000;")
        for i, c in enumerate(roles)
    )
    _, warns = lint_file(_write(tmp_path, cells, page="1400x900"))
    assert any("no legend" in w for w in warns), warns


def test_legend_cell_satisfies_the_legend_requirement(tmp_path):
    roles = ["#dae8fc", "#fff2cc", "#ffe6cc", "#d5e8d4"]
    cells = "".join(
        _box(f"r{i}", 60 + i * 220, 100, label=f"n{i}", style=f"fillColor={c};fontColor=#000000;")
        for i, c in enumerate(roles)
    )
    cells += _box("lg", 60, 400, w=300, label="Legend", style="fillColor=#eef2f7;fontColor=#000000;")
    _, warns = lint_file(_write(tmp_path, cells, page="1400x900"))
    assert not any("no legend" in w for w in warns), warns


def test_note_and_neutral_fills_do_not_trigger_the_legend_rule(tmp_path):
    """Notes/titles/panels are not role colours — they carry no semantics to explain."""
    cells = (
        _box("n1", 60, 100, label="note", style="fillColor=#fff9c4;fontColor=#000000;")
        + _box("t1", 300, 100, label="title", style="fillColor=#eef2f7;fontColor=#000000;")
        + _box("p1", 540, 100, label="panel", style="fillColor=#f5f5f5;fontColor=#000000;")
    )
    _, warns = lint_file(_write(tmp_path, cells, page="1200x900"))
    assert not any("no legend" in w for w in warns), warns


def test_palette_sprawl_warns(tmp_path):
    from src.jobcore.drawio_lint import SEMANTIC_PALETTE

    # The limit equals the palette size, so sprawl needs at least one colour beyond it.
    fills = [*SEMANTIC_PALETTE, "#3b0a45"]
    cells = "".join(
        _box(
            f"s{i}", 60 + (i % 6) * 200, 100 + (i // 6) * 200, label=f"n{i}", style=f"fillColor={c};fontColor=#000000;"
        )
        for i, c in enumerate(fills)
    )
    _, warns = lint_file(_write(tmp_path, cells, page="1400x900"))
    assert any("distinct fills" in w for w in warns), warns


def test_role_palette_collisions_are_pinned_and_cannot_get_worse():
    """The convention must be self-consistent, or "nearest role colour" is meaningless.

    This check surfaced two real defects in the documented palette: queue/ingestion #ffe6cc is
    only 12 RGB units from store/database #fff2cc, and service #dae8fc is 29 from gateway
    #e1d5e7. Rather than pretend the palette is clean, the known pairs are pinned here so a
    NEW collision fails the build. The durable fix is shape + colour, never colour alone.
    """
    from src.jobcore.drawio_lint import (
        KNOWN_CLOSE_ROLE_PAIRS,
        NEAR_DUPLICATE_DIST,
        ROLE_PALETTE,
        _rgb_dist,
    )

    roles = list(ROLE_PALETTE)
    for i, a in enumerate(roles):
        for b in roles[i + 1 :]:
            d = _rgb_dist(a, b)
            if frozenset({a, b}) in KNOWN_CLOSE_ROLE_PAIRS:
                continue  # documented defect, differentiated by shape
            assert d >= NEAR_DUPLICATE_DIST, (
                f"NEW role-colour collision: {a} ({ROLE_PALETTE[a]}) and {b} ({ROLE_PALETTE[b]}) "
                f"are only {d:.0f} apart, below the {NEAR_DUPLICATE_DIST} drift threshold. "
                f"Either separate them or pin the pair in KNOWN_CLOSE_ROLE_PAIRS with a reason."
            )


def test_pinned_close_pairs_still_match_their_recorded_distance():
    """If someone re-tints a pinned colour, the pin must stop silently covering for it."""
    from src.jobcore.drawio_lint import KNOWN_CLOSE_ROLE_PAIRS, _rgb_dist

    for pair, recorded in KNOWN_CLOSE_ROLE_PAIRS.items():
        a, b = sorted(pair)
        assert round(_rgb_dist(a, b)) == recorded, (
            f"{a}/{b} distance is now {_rgb_dist(a, b):.0f}, pinned as {recorded} - update the pin"
        )


def test_sprawl_limit_is_at_least_the_palette_size():
    """A diagram that uses the whole documented palette must not be flagged for conforming."""
    from src.jobcore.drawio_lint import MAX_DISTINCT_FILLS, SEMANTIC_PALETTE

    assert MAX_DISTINCT_FILLS >= len(SEMANTIC_PALETTE), (
        f"limit {MAX_DISTINCT_FILLS} is below the {len(SEMANTIC_PALETTE)}-colour palette, so a "
        f"fully-legended conforming diagram would warn"
    )


def test_documented_edge_stroke_colours_clear_both_canvases():
    """The AI edge stroke the skill used to prescribe (#82b366) is 2.4:1 on white — invisible.

    A border on a filled box is judged against the fill; a line on the canvas is not. The skill
    now prescribes #558b2f for AI edges and #607d8b for neutral ones; pin that here so the
    guidance and the gate can never disagree again.
    """
    from src.jobcore.drawio_lint import DARK_CANVAS, LIGHT_CANVAS, MIN_LINE_CONTRAST

    # EDGE strokes only. Box borders (#d79b00, #9673a6, #6c8ebf...) sit on their own fill and
    # are deliberately excluded — holding them to canvas contrast would be wrong.
    for stroke in ("#607d8b", "#558b2f", "#b85450"):
        for canvas in (LIGHT_CANVAS, DARK_CANVAS):
            r = contrast_ratio(stroke, canvas)
            assert r >= MIN_LINE_CONTRAST, f"{stroke} is {r:.2f}:1 on {canvas} (need {MIN_LINE_CONTRAST})"


def test_exemplar_diagram_stays_clean():
    """The shipped exemplar must lint clean — it is what a new user copies.

    Replaces two tests that asserted the original author's own prep diagrams; those are personal
    artifacts and deliberately did not travel to this repo.
    """
    import pathlib

    if not pathlib.Path(EXEMPLAR_DIAGRAM).exists():
        pytest.skip("no exemplar diagram shipped yet")
    errors, _ = lint_file(EXEMPLAR_DIAGRAM)
    assert errors == [], "; ".join(errors)


class TestFlushStacksAreNotCrowding:
    """A legend and an ER table are deliberately flush stacks — the lint must not warn on them.

    On the first run of the role-radar architecture diagram, 24 of 32 warnings were legend rows.
    The gate was drowning its own real findings (one edge-crossing error) in noise about a shape
    it was designed to support: the ER convention in the diagram-generator skill specifies
    "Height=26 per column, each subsequent column offset by 26", i.e. exactly zero gap.

    The exemption must stay narrow, so the tests below pin both directions.
    """

    @staticmethod
    def _b(x, y, w, h, label="x"):
        return (x, y, w, h, label, "", None)

    def test_a_flush_legend_stack_is_exempt(self) -> None:
        boxes = {
            "a": self._b(100, 100, 250, 26, "Legend"),
            "b": self._b(100, 126, 250, 26, "service"),
            "c": self._b(100, 152, 250, 26, "store"),
        }
        assert check_vertical_gaps(boxes, list(boxes), 60) == []

    def test_real_crowding_still_warns(self) -> None:
        """Two components 20px apart is the defect the check exists for."""
        boxes = {
            "a": self._b(100, 100, 200, 60, "Service A"),
            "b": self._b(100, 180, 200, 60, "Service B"),
        }
        warns = check_vertical_gaps(boxes, list(boxes), 60)
        assert warns and "20px" in warns[0]

    def test_same_column_but_nonzero_gap_still_warns(self) -> None:
        """The exemption is for FLUSH stacks only — a 6px gap is drift, not a table."""
        boxes = {
            "a": self._b(100, 100, 200, 40),
            "b": self._b(100, 146, 200, 40),
        }
        assert check_vertical_gaps(boxes, list(boxes), 60) != []

    def test_flush_but_mismatched_width_still_warns(self) -> None:
        """Differing widths mean these are not rows of one table."""
        boxes = {
            "a": self._b(100, 100, 200, 40),
            "b": self._b(100, 140, 260, 40),
        }
        assert check_vertical_gaps(boxes, list(boxes), 60) != []

    def test_flush_but_offset_x_still_warns(self) -> None:
        boxes = {
            "a": self._b(100, 100, 200, 40),
            "b": self._b(140, 140, 200, 40),
        }
        assert check_vertical_gaps(boxes, list(boxes), 60) != []

    def test_overlapping_boxes_are_not_exempted_as_a_stack(self) -> None:
        """Guard the guard: a genuine overlap must not be read as a tidy table."""
        boxes = {
            "a": self._b(100, 100, 200, 60),
            "b": self._b(100, 130, 200, 60),
        }
        # 30px overlap -> negative gap; the vgap check ignores negatives (overlap check owns it),
        # but the exemption must not be what silences it.
        from ..drawio_lint import _is_flush_stack

        assert not _is_flush_stack(boxes["a"], boxes["b"])

    def test_non_adjacent_rows_of_the_same_legend_are_exempt(self) -> None:
        """Rows 1 and 3 are 26px apart with row 2 between them — the case a pairwise test misses."""
        boxes = {
            "a": self._b(100, 100, 250, 26, "Legend"),
            "b": self._b(100, 126, 250, 26, "service"),
            "c": self._b(100, 152, 250, 26, "store"),
        }
        assert check_vertical_gaps(boxes, list(boxes), 60) == []

    def test_a_component_column_at_20px_is_still_flagged(self) -> None:
        """Same x and width, but NOT flush — this diagram's own $PROFILE_DIR boxes look like this.

        This is the case that makes "same column" alone an unacceptable rule.
        """
        boxes = {
            "a": self._b(100, 460, 400, 90, "profile.json"),
            "b": self._b(100, 570, 400, 50, "master CV"),
        }
        warns = check_vertical_gaps(boxes, list(boxes), 60)
        assert warns and "20px" in warns[0], warns

    def test_a_flush_run_ending_does_not_exempt_the_next_component(self) -> None:
        """Legend rows are exempt; a component 20px below the legend is not."""
        boxes = {
            "l1": self._b(100, 100, 250, 26, "Legend"),
            "l2": self._b(100, 126, 250, 26, "service"),
            "far": self._b(100, 172, 250, 40, "a real component"),
        }
        warns = check_vertical_gaps(boxes, list(boxes), 60)
        assert any("far" in w for w in warns), warns
