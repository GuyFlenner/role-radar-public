#!/usr/bin/env python3
"""Deterministic UX/geometry/contrast lint for draw.io (.drawio) diagrams.

Catches the presentation defects a text/LLM reviewer structurally cannot see,
because they are properties of *coordinates and colours*, not of the XML's meaning.

ERRORS (block the "done" gate)
  - box <-> box overlap (excluding container backgrounds and full-width band labels)
  - edge path passing THROUGH a box it does not connect -- checked for edges WITH
    explicit waypoints AND for waypoint-less edges via their candidate auto-routes
  - text invisible on the canvas: a value-bearing cell with no solid fill, so it
    renders directly on the canvas and disappears in draw.io's dark theme
  - text unreadable on its own fill: WCAG contrast ratio < MIN_CONTRAST
  - DETACHED edge endpoint -- dragging an arrow end in the draw.io GUI drops
    source=/target= and leaves a floating mxPoint, so the line looks connected
    but models nothing. The most common way a hand-edited diagram loses meaning.
  - an edge whose strokeColor is invisible on the light OR the dark canvas
  - content outside the page on ANY side (including negative coordinates)
  - a fill that is a NEAR-DUPLICATE of a semantic-palette colour -- drift, not choice
    (an audit of this repo found seven barely-distinguishable pale blues), which makes
    colour stop being a code the reader can decode

WARNINGS (advisory)
  - straight-line route crosses a box but an L-route is clear (add waypoints to be sure)
  - sibling boxes closer than --min-gap / stacked boxes closer than --min-vgap
  - a label too large for its own box (clips, or spills onto the row below)
  - duplicate consecutive waypoints (editor drag artefact)
  - an edge grazing a box it does not connect (reads as touching it)
  - an edge label estimated to land on top of a box
  - edge label with no labelBackgroundColor (may sit unreadable on a line/box)
  - high-degree hub node (>= --hub-degree edges) -- edge-label pile-up risk
  - a fill outside the semantic palette (may be deliberate, but needs a legend entry)
  - more than MAX_DISTINCT_FILLS colours on a page (colour stops being holdable as a code)
  - >= LEGEND_REQUIRED_AT semantic role-colours used with no legend cell present
  - excessive edge-edge crossings (arrow spaghetti; reported as a page-level count, since a
    few crossings are unavoidable in any real architecture)

Documented blind spot: edge *labels* use relative geometry with no absolute box,
so label-on-label collisions are not machine-checkable -- the hub-degree WARN is
the proxy, and a render/eyeball is still required.

Run this after ANY hand-edit in the draw.io GUI, not just after generating a file.

CLI:  uv run python -m src.jobcore.drawio_lint <file.drawio>
          [--min-gap 20] [--min-vgap 25] [--hub-degree 4]
"""

from __future__ import annotations

import argparse
import re
import sys
import xml.etree.ElementTree as ET

from .console import force_utf8_output

# Container backgrounds legitimately enclose children; skip them in overlap checks.
CONTAINER_MIN_W = 500
CONTAINER_MIN_H = 120

# Full-width, very short cells are section dividers / band labels ("--- VPC ---").
# Arrows legitimately cross them and they legitimately span other content.
BAND_MIN_W = 600
BAND_MAX_H = 40

# WCAG AA for normal text is 4.5:1. Diagram labels are small and dense -- hold the line.
MIN_CONTRAST = 4.5

# draw.io renders on a white canvas in the default theme and near-black in dark
# theme. Anything you colour explicitly must clear BOTH.
LIGHT_CANVAS = "#ffffff"
DARK_CANVAS = "#121212"
# WCAG non-text contrast (lines, borders, icons) is 3:1; text is 4.5:1.
MIN_LINE_CONTRAST = 3.0

# --- semantic palette -------------------------------------------------------
# The SINGLE source of truth for "what colour means what" in this repo's diagrams.
# `.claude/skills/diagram-generator/SKILL.md` documents these; this gate enforces them,
# so the two can't drift.
#
# Why this exists: contrast checks prove a box is *readable*; they say nothing about whether
# it is *decodable*. A store painted service-blue passes every other rule in this file and
# still lies to the reader. An audit of the 21 diagrams in this repo found a core of 10
# colours used 500+ times plus a tail of ~20 one-offs including SEVEN nearly identical pale
# blues (#dae8fc, #e8f0fb, #c9d9e8, #eef3fb, #eaf2ff, #f4f7fb, #eef6ff) — invisible drift that
# makes colour meaningless.
#
# ROLE colours carry meaning and must stay far apart from each other. CHROME colours are
# decoration (titles, notes, panel backgrounds) and are deliberately excluded from
# near-duplicate matching: title-band #eef2f7 sits only 23 RGB units from service-blue
# #dae8fc, so letting chrome compete for "nearest role" made the drift message point at the
# wrong colour. That proximity is also *why* the drift happened — an author reaching for
# "a pale blue" had two legitimate targets.
ROLE_PALETTE = {
    "#dae8fc": "service / compute (producer or consumer)",
    "#ffe6cc": "queue / stream / event backbone (ingestion)",
    "#fff2cc": "store / state / database",
    "#d5e8d4": "AI / LLM / ML",
    "#f8cecc": "actor / external system / client",
    "#e1d5e7": "gateway / integration / tool",
}
CHROME_PALETTE = {
    "#fff9c4": "note / annotation",
    "#eef2f7": "title / band / legend",
    "#f5f5f5": "zone / cloud / container area (see ZONE convention)",
    "#e0e0e0": "zone, nested one level deeper",
    "#ffffff": "neutral panel (white)",
}

# ZONE convention — how to draw nested infrastructure areas (provider → network → cluster).
#
# Do NOT invent a new pale tint per zone. That is what produced #eef7ee / #f4f7fb / #fff7e6 in
# the ExampleCorp GCP page: three one-off greens/blues/creams that mean nothing to a reader and
# are each a near-duplicate of something else. Any set of nested greys is necessarily close
# together, so fill cannot carry this distinction.
#
# Instead: every zone uses the SAME neutral fill and is distinguished by STROKE colour plus a
# dashed border and an explicit label. Stroke is free — it competes with nothing, and a dashed
# outline already reads as "boundary, not component".
ZONE_STROKES = {
    "provider": "#d79b00",  # outermost: cloud provider / public edge
    "network": "#6c8ebf",  # VPC / subnet / private network
    "cluster": "#82b366",  # K8s cluster / ECS cluster / compute pool
    "trust": "#b85450",  # security or trust boundary
}
SEMANTIC_PALETTE = {**ROLE_PALETTE, **CHROME_PALETTE}
# A fill this close (Euclidean RGB) to a ROLE colour but not equal to it is drift, not choice —
# you meant the palette entry. Set empirically at 20 from the audited drift family (#e8f0fb 16,
# #e6ebf2 16, #eaf2ff 19, #d0e0d0 10). It CANNOT be raised much: see KNOWN_CLOSE_ROLE_PAIRS.
NEAR_DUPLICATE_DIST = 20.0

# ⚠️ Known defects in the convention itself, pinned so they cannot silently get worse.
# `#ffe6cc` (queue/ingestion) sits only 12 RGB units from `#fff2cc` (store/database) — two of
# the most semantically opposite roles in an architecture diagram are nearly the same colour,
# so a reader cannot tell an event backbone from a database by colour alone. `#dae8fc` (service)
# and `#e1d5e7` (gateway) are 29 apart, which is better but still not comfortable.
#
# The durable fix is NOT a bigger threshold — it is to stop encoding role in colour ALONE.
# Give stores a distinguishing shape (cylinder / `arcSize`) and queues a distinguishing shape
# too, so the role survives both a near-duplicate palette and a colour-blind reader. Colour
# should be a redundant cue, never the only one. (Both diagrams are also read by people at an
# accessibility company — worth getting right on principle.)
KNOWN_CLOSE_ROLE_PAIRS = {
    frozenset({"#ffe6cc", "#fff2cc"}): 12,
    frozenset({"#dae8fc", "#e1d5e7"}): 29,
}
# More distinct fills than this on one page and colour stops being a code the reader can hold.
# It MUST be >= the palette size, or a diagram that legitimately uses the whole documented
# palette (a fully-legended architecture page needs all six role colours plus note, title, zone
# and white) gets flagged for conforming. Exceeding the palette size means off-palette colours
# are in play, which the near-duplicate / off-palette rules already name individually.
MAX_DISTINCT_FILLS = 11
# At or above this many *semantic* role fills, the diagram needs a legend to be self-explanatory.
LEGEND_REQUIRED_AT = 3

# draw.io's default text colour when a style sets no fontColor.
DEFAULT_FONT = "#000000"
# A cell with no fillColor renders on the canvas, which is light in the default
# theme and near-black in dark theme -- so there is NO safe font colour for it.
NO_FILL = {"none", "default", ""}


def _has_xywh(geo):
    """A vertex is only positionable if it carries all four geometry values.

    Partial geometry is legal in mxGraph (relative/auto-sized cells) and must be
    skipped, not crashed on -- a lint that dies on valid input is worse than one
    that misses a case.
    """
    return all(geo.get(k) is not None for k in ("x", "y", "width", "height"))


def _style_map(style):
    out = {}
    for part in (style or "").split(";"):
        if "=" in part:
            k, _, v = part.partition("=")
            out[k.strip()] = v.strip()
        elif part.strip():
            out[part.strip()] = True
    return out


def _hex_to_rgb(c):
    c = (c or "").strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if not re.fullmatch(r"[0-9a-fA-F]{6}", c or ""):
        return None
    return tuple(int(c[i : i + 2], 16) for i in (0, 2, 4))


def _luminance(rgb):
    def chan(v):
        v = v / 255
        return v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4

    r, g, b = (chan(v) for v in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(fg, bg):
    """WCAG contrast ratio between two hex colours, or None if either is unparseable."""
    a, b = _hex_to_rgb(fg), _hex_to_rgb(bg)
    if a is None or b is None:
        return None
    la, lb = _luminance(a), _luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def load_pages(path):
    """Yield (page_name, page_w, page_h, boxes, edges) per <diagram> page.

    Each draw.io page has its OWN coordinate system, so cross-page cells must
    never be compared -- that would report bogus overlaps on multi-page files.

    boxes: {id: (x, y, w, h, label, style, parent)}
    edges: [(id, source, target, waypoints, label, style, floating_endpoints)]

    Labels are capped at 300 chars (not the ~34 used for display truncation in error
    messages) -- a tighter cap here silently defeated check_label_fits's swimlane-title
    overflow math: a title clipped to 34 chars at STORAGE time can never be measured as
    "too long for its box," no matter how long the real title actually is. Escaped to a
    real diagram, 2026-08-03. Truncate for display only, at the point each check builds
    its message string, never at storage time.
    """
    root = ET.parse(path).getroot()
    diagrams = root.findall(".//diagram")
    if not diagrams:  # bare <mxGraphModel> with no <diagram> wrapper
        model = root.find(".//mxGraphModel")
        diagrams = [model] if model is not None else []
    for i, dia in enumerate(diagrams):
        name = dia.get("name", f"page{i + 1}") if dia.tag == "diagram" else f"page{i + 1}"
        model = dia.find(".//mxGraphModel") if dia.tag == "diagram" else dia
        if model is None:
            continue
        page_w = float(model.get("pageWidth", "0"))
        page_h = float(model.get("pageHeight", "0"))

        # A child cell's geometry is RELATIVE TO ITS PARENT group. Treating every
        # x/y as absolute reported ~150 phantom overlaps on a legitimately grouped
        # diagram -- resolve the parent chain to absolute coordinates first.
        _pmap = {c: par for par in model.iter() for c in par}

        def _rid(cell):
            w = _pmap.get(cell)
            if w is not None and w.tag in ("object", "UserObject"):
                return w.get("id") or cell.get("id")
            return cell.get("id")

        raw = {}
        for cell in model.iter("mxCell"):
            geo = cell.find("mxGeometry")
            if cell.get("vertex") == "1" and geo is not None and _has_xywh(geo):
                raw[_rid(cell)] = (cell.get("parent"), float(geo.get("x")), float(geo.get("y")))

        def origin(cid, _seen=None):
            """Absolute (x, y) offset of a cell's parent chain."""
            _seen = _seen or set()
            parent = raw.get(cid, (None, 0.0, 0.0))[0]
            if parent is None or parent not in raw or parent in _seen:
                return 0.0, 0.0
            _seen.add(parent)
            px, py = raw[parent][1], raw[parent][2]
            gx, gy = origin(parent, _seen)
            return px + gx, py + gy

        # draw.io wraps a cell in <object label=... tooltip=...> when it carries
        # metadata. The label then lives on the OBJECT, not the mxCell -- reading
        # only mxCell/@value made every such cell look unlabelled, silently
        # skipping the contrast and label-fit checks on whole files.
        parent_of = {c: par for par in model.iter() for c in par}

        def cell_label(cell):
            v = cell.get("value")
            if v:
                return v
            wrapper = parent_of.get(cell)
            if wrapper is not None and wrapper.tag in ("object", "UserObject"):
                return wrapper.get("label") or ""
            return ""

        def cell_id(cell):
            wrapper = parent_of.get(cell)
            if wrapper is not None and wrapper.tag in ("object", "UserObject"):
                return wrapper.get("id") or cell.get("id")
            return cell.get("id")

        boxes, edges = {}, []
        for cell in model.iter("mxCell"):
            cid = cell_id(cell)
            geo = cell.find("mxGeometry")
            style = cell.get("style") or ""
            label = cell_label(cell).replace("&#10;", " ").strip()
            if cell.get("vertex") == "1" and geo is not None and _has_xywh(geo):
                ox, oy = origin(cid)
                boxes[cid] = (
                    float(geo.get("x")) + ox,
                    float(geo.get("y")) + oy,
                    float(geo.get("width")),
                    float(geo.get("height")),
                    label[:300],
                    style,
                    cell.get("parent"),
                )
            if cell.get("edge") == "1":
                # Only <Array as="points"> children are waypoints. A bare mxPoint with
                # as="sourcePoint"/"targetPoint" is a DETACHED endpoint, not a bend --
                # treating those as waypoints silently corrupted the routing check.
                pts, floating = [], set()
                if geo is not None:
                    for arr in geo.findall("Array"):
                        if arr.get("as") == "points":
                            pts += [
                                (float(p.get("x")), float(p.get("y")))
                                for p in arr.findall("mxPoint")
                                if p.get("x") and p.get("y")
                            ]
                    for p in geo.findall("mxPoint"):
                        if p.get("as") in ("sourcePoint", "targetPoint"):
                            floating.add(p.get("as"))
                edges.append((cid, cell.get("source"), cell.get("target"), pts, label[:300], style, floating))
        yield name, page_w, page_h, boxes, edges


def containers(boxes):
    """Cells that legitimately enclose others: real mxGraph groups (exact -- anything
    that is another vertex's parent) plus large visual backdrops (heuristic)."""
    out = {b[6] for b in boxes.values() if len(b) > 6 and b[6] in boxes}
    # A box that FULLY encloses another is a backdrop/zone, not a collision --
    # geometric containment is an exact signal, unlike the size heuristic below.
    for cid, (x, y, w, h, *_r) in boxes.items():
        for oid, (ox, oy, ow, oh, *_r2) in boxes.items():
            if oid == cid or (ow >= w and oh >= h):
                continue
            if ox >= x - 1 and oy >= y - 1 and ox + ow <= x + w + 1 and oy + oh <= y + h + 1:
                out.add(cid)
                break
    for cid, (x, y, w, h, _lbl, _st, *_r) in boxes.items():
        if w < CONTAINER_MIN_W or h < CONTAINER_MIN_H:
            continue
        inside = sum(
            1
            for oid, (ox, oy, ow, oh, _l, _s, *_r2) in boxes.items()
            if oid != cid and ox >= x and oy >= y and ox + ow <= x + w and oy + oh <= y + h
        )
        if inside >= 2:
            out.add(cid)
    return out


def band_labels(boxes):
    """Full-width, very short separator labels. Arrows may cross them by design."""
    return {cid for cid, (_x, _y, w, h, _l, _s, *_r) in boxes.items() if w >= BAND_MIN_W and h <= BAND_MAX_H}


def overlap(a, b):
    ox = min(a[0] + a[2], b[0] + b[2]) - max(a[0], b[0])
    oy = min(a[1] + a[3], b[1] + b[3]) - max(a[1], b[1])
    return ox, oy


def seg_through_box(p1, p2, box, pad=4):
    """True if segment p1->p2 passes through the box rect.

    Handles DIAGONAL segments too (the previous horizontal/vertical-only version
    silently passed every sloped edge -- see test_diagonal_segment_is_checked).
    Uses the Liang-Barsky slab clip against the padded rectangle.
    """
    (x1, y1), (x2, y2) = p1, p2
    bx, by, bw, bh = box[:4]
    xmin, ymin = bx + pad, by + pad
    xmax, ymax = bx + bw - pad, by + bh - pad
    if xmin >= xmax or ymin >= ymax:
        return False
    dx, dy = x2 - x1, y2 - y1
    t0, t1 = 0.0, 1.0
    for p, q in ((-dx, x1 - xmin), (dx, xmax - x1), (-dy, y1 - ymin), (dy, ymax - y1)):
        if abs(p) < 1e-9:
            if q < 0:  # parallel and outside this slab
                return False
            continue
        t = q / p
        if p < 0:
            if t > t1:
                return False
            t0 = max(t0, t)
        else:
            if t < t0:
                return False
            t1 = min(t1, t)
    return t0 <= t1


def anchor_points(box, style_map, kind):
    """Resolve an edge's exit/entry point on a box, honouring exitX/entryX if set."""
    x, y, w, h = box[:4]
    fx, fy = style_map.get(f"{kind}X"), style_map.get(f"{kind}Y")
    try:
        if fx is not None and fy is not None:
            return (x + float(fx) * w, y + float(fy) * h)
    except (TypeError, ValueError):
        pass
    return (x + w / 2, y + h / 2)


def route_candidates(src, dst, style_map):
    """Plausible paths draw.io may draw for a waypoint-less edge.

    Returns (straight, l_route_h_first, l_route_v_first) as point lists. draw.io's
    orthogonalEdgeStyle picks an elbow; which elbow depends on relative geometry,
    so a lint that only modelled one of them would miss real collisions.
    """
    a = anchor_points(src, style_map, "exit")
    b = anchor_points(dst, style_map, "entry")
    straight = [a, b]
    l_h = [a, (b[0], a[1]), b]  # horizontal then vertical
    l_v = [a, (a[0], b[1]), b]  # vertical then horizontal
    return straight, l_h, l_v


def _path_hits(path, boxes, skip):
    hits = set()
    for j in range(len(path) - 1):
        for bid, box in boxes.items():
            if bid in skip:
                continue
            if seg_through_box(path[j], path[j + 1], box):
                hits.add(bid)
    return hits


def check_contrast(boxes, edges):
    """Text must be readable on whatever sits behind it -- in BOTH draw.io themes."""
    errors, warns = [], []
    for cid, (_x, _y, _w, _h, label, style, *_r) in boxes.items():
        if not label:
            continue
        sm = _style_map(style)
        fill = sm.get("fillColor")
        font = sm.get("fontColor", DEFAULT_FONT)
        if fill is None or str(fill).lower() in NO_FILL:
            errors.append(
                f"invisible-on-canvas {cid}({label[:30]!r}): text with no fillColor renders "
                f"directly on the canvas -> disappears in draw.io dark theme. "
                f"Give it a solid fillColor."
            )
            continue
        ratio = contrast_ratio(font, fill)
        if ratio is not None and ratio < MIN_CONTRAST:
            errors.append(
                f"low-contrast {cid}({label[:30]!r}): fontColor={font} on fillColor={fill} "
                f"= {ratio:.1f}:1, below {MIN_CONTRAST}:1"
            )
    for eid, _s, _t, _pts, label, style, _fl in edges:
        if label and "labelBackgroundColor" not in _style_map(style):
            warns.append(
                f"edge label {eid}({label[:30]!r}) has no labelBackgroundColor - may sit unreadable on a line/box"
            )
    return errors, warns


def check_edge_visibility(edges):
    """An arrow is only useful if you can SEE it -- in both draw.io themes.

    The contrast check originally covered text only, so 18 near-black arrows
    (#444444) shipped in a "clean" diagram and vanished on a dark canvas. Same
    defect class as invisible text, one object type over. Lines are non-text
    content, so the bar is WCAG 3:1 rather than 4.5:1.
    """
    errors = []
    for eid, _s, _t, _pts, label, style, _fl in edges:
        stroke = _style_map(style).get("strokeColor")
        if stroke is None or str(stroke).lower() in ("none", "default"):
            continue  # inherits the theme's default, which draw.io keeps visible
        light = contrast_ratio(stroke, LIGHT_CANVAS)
        dark = contrast_ratio(stroke, DARK_CANVAS)
        if light is None or dark is None:
            continue
        if light < MIN_LINE_CONTRAST or dark < MIN_LINE_CONTRAST:
            worst = "dark" if dark < light else "light"
            errors.append(
                f"invisible edge {eid}({label[:26]!r}): strokeColor={stroke} is "
                f"{min(light, dark):.1f}:1 on a {worst} canvas (need {MIN_LINE_CONTRAST}:1). "
                f"Pick a mid-tone that clears 3:1 on BOTH themes."
            )
    return errors


def check_edge_clearance(boxes, edges, ignore, clearance=10):
    """An arrow that grazes a box it does not connect reads as touching it.

    Non-intersection is not enough -- a line 2px from a border looks attached.
    """
    warns = []
    for eid, s, t, pts, label, style, _fl in edges:
        if s not in boxes or t not in boxes:
            continue
        path = [anchor_points(boxes[s], _style_map(style), "exit")]
        path += pts
        path.append(anchor_points(boxes[t], _style_map(style), "entry"))
        skip = {s, t} | ignore
        near = set()
        for j in range(len(path) - 1):
            for bid, box in boxes.items():
                if bid in skip:
                    continue
                if seg_through_box(path[j], path[j + 1], box, pad=-clearance) and not seg_through_box(
                    path[j], path[j + 1], box
                ):
                    near.add(bid)
        for bid in sorted(near):
            warns.append(f"edge {eid}({label[:26]!r}) passes within {clearance}px of {bid}({boxes[bid][4]!r})")
    return warns


def check_edge_label_collision(boxes, edges, ignore):
    """Approximate where an edge label lands and flag it if it sits on a box.

    Edge labels use relative geometry, so this is an estimate, not proof -- but
    an estimate beats the previous position of "not machine-checkable, eyeball it".
    """
    warns = []
    for eid, s, t, pts, label, style, _fl in edges:
        if not label or s not in boxes or t not in boxes:
            continue
        sm = _style_map(style)
        path = [anchor_points(boxes[s], sm, "exit")] + pts + [anchor_points(boxes[t], sm, "entry")]
        best, blen = None, -1.0
        for j in range(len(path) - 1):
            (x1, y1), (x2, y2) = path[j], path[j + 1]
            ln = abs(x2 - x1) + abs(y2 - y1)
            if ln > blen:
                blen, best = ln, ((x1 + x2) / 2, (y1 + y2) / 2)
        if best is None:
            continue
        try:
            fs = float(sm.get("fontSize", 10))
        except (TypeError, ValueError):
            fs = 10.0
        lw, lh = len(label) * fs * 0.55, fs * 1.4
        lbox = (best[0] - lw / 2, best[1] - lh / 2, lw, lh)
        for bid, box in boxes.items():
            if bid in ({s, t} | ignore):
                continue
            ox, oy = overlap(lbox, box)
            if ox > 0 and oy > 0:
                warns.append(
                    f"edge label {eid}({label[:26]!r}) likely lands on {bid}({box[4]!r}) - "
                    f"move the label or reroute so its longest segment is in clear space"
                )
                break
    return warns


def check_detached_edges(boxes, edges):
    """An edge endpoint dragged in the draw.io GUI silently DETACHES from its node.

    draw.io drops the source/target attribute and writes a floating
    mxPoint as="sourcePoint"/"targetPoint" instead. The line still *looks*
    connected, but it is bound to nothing: move the node and it stays behind, and
    the modelled relationship is gone. This is the single most common way a
    hand-edited diagram silently loses meaning.
    """
    errors = []
    for eid, s, t, _pts, label, _style, floating in edges:
        missing = []
        if not s or s not in boxes:
            missing.append(f"source{'(floating point)' if 'sourcePoint' in floating else ''}")
        if not t or t not in boxes:
            missing.append(f"target{'(floating point)' if 'targetPoint' in floating else ''}")
        if missing:
            errors.append(
                f"detached edge {eid}({label[:30]!r}): {' and '.join(missing)} not attached to a node. "
                f"Re-attach in the editor or set source=/target= in the XML."
            )
    return errors


def check_in_page(boxes, page_w, page_h):
    """Content must sit inside the page on ALL four sides.

    Only the right/bottom edges were checked before, so a node dragged off to
    negative coordinates (x=-200) was silently accepted.
    """
    errors = []
    for cid, (x, y, w, h, label, _s, *_r) in boxes.items():
        if x < 0 or y < 0:
            errors.append(f"off-page {cid}({label[:30]!r}): origin ({x:.0f},{y:.0f}) is left of / above the page")
    if boxes:
        maxx = max(b[0] + b[2] for b in boxes.values())
        maxy = max(b[1] + b[3] for b in boxes.values())
        if page_w and maxx > page_w:
            errors.append(f"content x={maxx:.0f} exceeds pageWidth={page_w:.0f}")
        if page_h and maxy > page_h:
            errors.append(f"content y={maxy:.0f} exceeds pageHeight={page_h:.0f}")
    return errors


def check_label_fits(boxes):
    """A box too small for its own text clips it or spills it over neighbours.

    Conservative estimate: draw.io wraps at the box width, so approximate the
    characters that fit per line from the font size and count wrapped lines.

    A `swimlane` container's title is a special case, handled separately below:
    it renders in the `startSize` header strip ONLY (never the container's full
    body height) and does NOT wrap -- draw.io renders it as one line. Reusing the
    wrapped-line-height math against the full container height gave a long title
    effectively unlimited room to "wrap into," so it never fired -- escaped to a
    real diagram (grip deployment-topology page, 2026-08-03): a title 3x its
    header's usable width read as clean because the zone box itself was 600px tall.
    """
    warns = []
    for cid, (_x, _y, w, h, label, style, *_r) in boxes.items():
        if not label:
            continue
        sm = _style_map(style)
        try:
            fs = float(sm.get("fontSize", 12))
        except (TypeError, ValueError):
            fs = 12.0
        is_bold = sm.get("fontStyle", "0") in ("1", "5", "7")  # draw.io ORs bold=1 into fontStyle

        if sm.get("swimlane") or "swimlane" in style:
            try:
                start_size = float(sm.get("startSize", 23))
            except (TypeError, ValueError):
                start_size = 23.0
            title = label.split("\n", 1)[0]
            char_w = fs * (0.68 if is_bold else 0.58)
            needed_w = len(title) * char_w + 16
            if needed_w > w:
                warns.append(
                    f"swimlane title overflow {cid}({title[:40]!r}): ~{needed_w:.0f}px needed on one "
                    f"line (titles don't wrap) but the header is {w:.0f}px wide (startSize={start_size:.0f}px "
                    f"tall) -- shorten the title or move detail into a child note"
                )
            continue

        cpl = max(1, int((w - 12) / (fs * 0.55)))
        lines = 0
        for para in label.split("\n"):
            lines += max(1, -(-len(para) // cpl))
        needed = lines * fs * 1.35 + 10
        if needed > h * 1.2:
            warns.append(
                f"label overflow {cid}({label[:30]!r}): ~{lines} wrapped line(s) need ~{needed:.0f}px "
                f"but the box is {h:.0f}px tall"
            )
    return warns


def _is_flush_stack(ba, bb):
    """True if two boxes are ADJACENT rows of one table: same column, exactly zero gap."""
    if not (abs(ba[0] - bb[0]) < 1 and abs(ba[2] - bb[2]) < 1):
        return False
    gap = (bb[1] - (ba[1] + ba[3])) if ba[1] < bb[1] else (ba[1] - (bb[1] + bb[3]))
    return abs(gap) < 1


def flush_runs(boxes, ids):
    """Group ids into contiguous flush stacks — legends and ER tables.

    A legend and an ER entity are deliberately flush: same x, same width, zero gap between
    consecutive rows. The ER convention this lint exists to support specifies exactly that
    ("Height=26 per column, each subsequent column offset by 26"), so warning about it fired on
    every correctly-drawn ER diagram and every legend — 24 of the 32 warnings on the first run of
    the role-radar architecture diagram. The gate was drowning its own single real finding in
    legend rows, and a gate that cries wolf gets switched off.

    Adjacency alone is not enough: rows 1 and 3 of a legend are 26px apart with row 2 between
    them, so a pairwise adjacent-only test still warns about them. Hence *runs*.

    The rule stays narrow on purpose. "Same column" alone would be far too lenient — a real
    stack of components frequently shares x and width (this diagram's own $PROFILE_DIR boxes do,
    at 20px apart, and they SHOULD be flagged). Only a chain in which EVERY consecutive gap is
    zero is a table.
    """
    runs: list[set] = []
    by_col: dict[tuple[int, int], list] = {}
    for cid in ids:
        x, _y, w, _h, *_ = boxes[cid]
        by_col.setdefault((round(x), round(w)), []).append(cid)
    for column in by_col.values():
        if len(column) < 2:
            continue
        column.sort(key=lambda c: boxes[c][1])
        run = [column[0]]
        for cid in column[1:]:
            if _is_flush_stack(boxes[run[-1]], boxes[cid]):
                run.append(cid)
            else:
                if len(run) > 1:
                    runs.append(set(run))
                run = [cid]
        if len(run) > 1:
            runs.append(set(run))
    return runs


def check_vertical_gaps(boxes, ids, min_vgap):
    """Stacked boxes need breathing room; only horizontal gaps were checked before."""
    warns = []
    runs = flush_runs(boxes, ids)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            ba, bb = boxes[a], boxes[b]
            if min(ba[0] + ba[2], bb[0] + bb[2]) - max(ba[0], bb[0]) <= 10:
                continue  # not vertically stacked
            if any(a in r and b in r for r in runs):
                continue  # two rows of the same legend / ER table
            gap = (bb[1] - (ba[1] + ba[3])) if ba[1] < bb[1] else (ba[1] - (bb[1] + bb[3]))
            if 0 <= gap < min_vgap:
                warns.append(f"tight vertical gap {gap:.0f}px: {a}({ba[4]!r}) / {b}({bb[4]!r})")
    return warns


def _rgb_dist(a, b):
    ra, rb = _hex_to_rgb(a), _hex_to_rgb(b)
    if ra is None or rb is None:
        return None
    return sum((x - y) ** 2 for x, y in zip(ra, rb, strict=False)) ** 0.5


def check_palette(boxes):
    """Enforce the semantic palette: colour must be a *code the reader can decode*.

    Every other rule in this file proves a box is legible. None of them prove it is
    meaningful. A database painted service-blue is perfectly readable and still misinforms.

    ERROR on a near-duplicate of a palette colour — that is drift, not a choice (you wrote
    ``#e8f0fb`` and meant ``#dae8fc``). WARN on a genuinely novel fill, which may be
    deliberate but needs a legend entry to be decodable.
    """
    errors, warns = [], []
    fills = {}
    for bid, box in boxes.items():
        label, style = box[4], box[5]
        sm = _style_map(style)
        fill = (sm.get("fillColor") or "").strip().lower()
        if not fill or fill in NO_FILL:
            continue  # container/outline-only cells are handled by the contrast rules
        fills.setdefault(fill, []).append((bid, label))

    for fill, users in sorted(fills.items()):
        if fill in SEMANTIC_PALETTE:
            continue
        # Match against ROLE colours only — chrome must not compete for "nearest".
        nearest, best = None, None
        for p in ROLE_PALETTE:
            d = _rgb_dist(fill, p)
            if d is not None and (best is None or d < best):
                nearest, best = p, d
        who = ", ".join(f"{b}({lbl!r})" for b, lbl in users[:3])
        if best is not None and best < NEAR_DUPLICATE_DIST:
            errors.append(
                f"off-palette fill {fill} is a near-duplicate of {nearest} "
                f"({ROLE_PALETTE[nearest]}, distance {best:.0f}) - used by {who}. "
                f"Use the palette colour; near-identical fills make colour undecodable."
            )
        else:
            warns.append(
                f"fill {fill} is not in the semantic palette - used by {who}. "
                f"Either use a palette colour or add a legend entry saying what it means."
            )

    if len(fills) > MAX_DISTINCT_FILLS:
        warns.append(
            f"{len(fills)} distinct fills on this page (limit {MAX_DISTINCT_FILLS}) - "
            f"more colours than a reader can hold as a code"
        )

    semantic = {f for f in fills if f in ROLE_PALETTE}
    if len(semantic) >= LEGEND_REQUIRED_AT:
        has_legend = any("legend" in (b[4] or "").lower() for b in boxes.values())
        if not has_legend:
            warns.append(
                f"{len(semantic)} semantic role-colours used but no legend cell found - "
                f"add a legend so the colour coding is self-explanatory"
            )
    return errors, warns


def _seg_cross(p1, p2, p3, p4):
    """True when segment p1-p2 properly crosses p3-p4 (shared endpoints don't count)."""

    def orient(a, b, c):
        v = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
        return 0 if abs(v) < 1e-9 else (1 if v > 0 else -1)

    if len({tuple(p) for p in (p1, p2, p3, p4)}) < 4:
        return False
    d1, d2 = orient(p3, p4, p1), orient(p3, p4, p2)
    d3, d4 = orient(p1, p2, p3), orient(p1, p2, p4)
    return d1 * d2 < 0 and d3 * d4 < 0


def check_edge_crossings(boxes, edges, max_crossings=6):
    """Arrows crossing arrows. Individually fine; in bulk it is spaghetti nobody can trace.

    Reported as a page-level count rather than per-pair: a few crossings are unavoidable in
    any real architecture, so naming each one would be noise. The signal is the total.
    """
    paths = []
    for eid, s, t, pts, label, style, _fl in edges:
        if s not in boxes or t not in boxes:
            continue
        sm = _style_map(style)
        path = [anchor_points(boxes[s], sm, "exit")] + list(pts) + [anchor_points(boxes[t], sm, "entry")]
        paths.append((eid, label, path))

    pairs = []
    for i, (ea, la, pa) in enumerate(paths):
        for eb, lb, pb in paths[i + 1 :]:
            hit = any(
                _seg_cross(pa[m], pa[m + 1], pb[n], pb[n + 1]) for m in range(len(pa) - 1) for n in range(len(pb) - 1)
            )
            if hit:
                pairs.append(f"{ea}({la!r})x{eb}({lb!r})")
    if len(pairs) > max_crossings:
        shown = ", ".join(pairs[:4])
        return [
            f"{len(pairs)} edge-edge crossings (limit {max_crossings}) - reads as spaghetti; "
            f"reroute or re-layer. e.g. {shown}"
        ]
    return []


def check_waypoints(edges):
    """Duplicate consecutive waypoints are editor drag artefacts -- noise that can
    also confuse the router into drawing a zero-length segment."""
    warns = []
    for eid, _s, _t, pts, label, _style, _fl in edges:
        dupes = sum(1 for i in range(len(pts) - 1) if pts[i] == pts[i + 1])
        if dupes:
            warns.append(f"degenerate waypoints on edge {eid}({label[:30]!r}): {dupes} duplicate consecutive point(s)")
    return warns


def lint_page(page_w, page_h, boxes, edges, args):
    cont = containers(boxes)
    bands = band_labels(boxes)
    ignore = cont | bands
    ids = [i for i in boxes if i not in ignore]
    errors, warns = [], []

    # 1. box-box overlap
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            ox, oy = overlap(boxes[a], boxes[b])
            if ox > 0 and oy > 0:
                errors.append(f"overlap {a}({boxes[a][4]!r}) x {b}({boxes[b][4]!r}) = {ox:.0f}x{oy:.0f}px")

    # 2. tight gaps (same visual row)
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            ba, bb = boxes[a], boxes[b]
            if min(ba[1] + ba[3], bb[1] + bb[3]) - max(ba[1], bb[1]) <= 10:
                continue  # not the same row
            gap = (bb[0] - (ba[0] + ba[2])) if ba[0] < bb[0] else (ba[0] - (bb[0] + bb[2]))
            if 0 <= gap < args.min_gap:
                warns.append(f"tight gap {gap:.0f}px: {a}({ba[4]!r}) -> {b}({bb[4]!r})")

    # 3. edge path through a box it does not connect.
    #    Waypoint-less edges are the common case and were previously SKIPPED entirely.
    for eid, s, t, pts, elabel, estyle, _fl in edges:
        skip = {s, t} | ignore
        if pts:
            path = []
            if s in boxes:
                path.append(anchor_points(boxes[s], _style_map(estyle), "exit"))
            path += pts
            if t in boxes:
                path.append(anchor_points(boxes[t], _style_map(estyle), "entry"))
            for bid in sorted(_path_hits(path, boxes, skip)):
                errors.append(f"edge {eid}({elabel!r}) crosses box {bid}({boxes[bid][4]!r})")
            continue
        if s not in boxes or t not in boxes:
            continue
        straight, l_h, l_v = route_candidates(boxes[s], boxes[t], _style_map(estyle))
        h_straight = _path_hits(straight, boxes, skip)
        h_lh = _path_hits(l_h, boxes, skip)
        h_lv = _path_hits(l_v, boxes, skip)
        if h_straight and h_lh and h_lv:
            blocked = sorted(h_straight | h_lh | h_lv)
            names = ", ".join(f"{b}({boxes[b][4]!r})" for b in blocked)
            errors.append(
                f"edge {eid}({elabel!r}) has NO clear auto-route {s}->{t}: every candidate path "
                f"crosses {names}. Add explicit waypoints to route around."
            )
        elif h_straight or h_lh or h_lv:
            blocked = sorted(h_straight | h_lh | h_lv)
            names = ", ".join(f"{b}({boxes[b][4]!r})" for b in blocked)
            warns.append(f"edge {eid}({elabel!r}) {s}->{t}: some auto-routes cross {names} - pin waypoints to be safe")

    # 4. content inside the page on all four sides
    errors += check_in_page(boxes, page_w, page_h)

    # 5. contrast / dark-theme safety
    c_err, c_warn = check_contrast(boxes, edges)
    errors += c_err
    warns += c_warn

    # 6. detached edge endpoints (GUI drag artefact -- relationship silently lost)
    errors += check_detached_edges(boxes, edges)

    # 6b. arrows you cannot see, arrows that graze boxes, labels that land on boxes
    errors += check_edge_visibility(edges)
    warns += check_edge_clearance(boxes, edges, ignore)
    warns += check_edge_label_collision(boxes, edges, ignore)

    # 6c. semantic palette — colour must be decodable, not merely legible
    p_err, p_warn = check_palette(boxes)
    errors += p_err
    warns += p_warn

    # 6d. arrow spaghetti
    warns += check_edge_crossings(boxes, edges)

    # 7. text that does not fit its box, cramped stacking, degenerate waypoints
    warns += check_label_fits(boxes)
    warns += check_vertical_gaps(boxes, ids, args.min_vgap)
    warns += check_waypoints(edges)

    # 8. high-degree hubs (label pile-up proxy)
    deg = {}
    for _e, s, t, _p, _l, _st, _fl in edges:
        for n in (s, t):
            if n in boxes:
                deg[n] = deg.get(n, 0) + 1
    for n, d in sorted(deg.items()):
        if d >= args.hub_degree:
            warns.append(f"hub {n}({boxes[n][4]!r}) has {d} edges - verify labels don't pile up (needs eyeball/render)")

    return errors, warns


class _Args:
    def __init__(self, min_gap=20.0, hub_degree=4, min_vgap=25.0):
        self.min_gap = min_gap
        self.hub_degree = hub_degree
        self.min_vgap = min_vgap


def lint_file(path, min_gap=20.0, hub_degree=4, min_vgap=25.0):
    """Programmatic entry point. Returns (errors, warns) as flat lists of strings."""
    args = _Args(min_gap, hub_degree, min_vgap)
    errors, warns = [], []
    for name, page_w, page_h, boxes, edges in load_pages(path):
        e, w = lint_page(page_w, page_h, boxes, edges, args)
        errors += [f"[{name}] {m}" for m in e]
        warns += [f"[{name}] {m}" for m in w]
    return errors, warns


def main(argv=None):
    force_utf8_output()
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--min-gap", type=float, default=20)
    ap.add_argument("--min-vgap", type=float, default=25)
    ap.add_argument("--hub-degree", type=int, default=4)
    args = ap.parse_args(argv)

    total_err = total_warn = 0
    pages = list(load_pages(args.file))
    for name, page_w, page_h, boxes, edges in pages:
        errors, warns = lint_page(page_w, page_h, boxes, edges, args)
        if len(pages) > 1:
            print(f"--- page: {name} ---")
        for e in errors:
            print(f"  ERROR  {e}")
        for w in warns:
            print(f"  WARN   {w}")
        if not errors and not warns:
            print("  clean - no geometric/contrast UX issues detected")
        total_err += len(errors)
        total_warn += len(warns)
    print(
        f"\n{total_err} error(s), {total_warn} warning(s) across {len(pages)} page(s). "
        f"(edge-label positions are NOT machine-checkable - eyeball hubs.)"
    )
    return 1 if total_err else 0


if __name__ == "__main__":
    sys.exit(main())
