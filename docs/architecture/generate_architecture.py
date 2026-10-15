"""Generate the role-radar architecture .drawio.

Geometry is COMPUTED, not hand-typed. The lint enforces >=70px horizontal and >=60px vertical
gaps, edge clearance, edge-through-box, label fit and no-overlap; hand-placing ~60 boxes across
four pages against those rules by eye is how the first draft earned an error and 32 warnings.

Palette is exactly src/jobcore/drawio_lint.py's ROLE_PALETTE / CHROME_PALETTE / ZONE_STROKES.
Role is carried by SHAPE as well as colour, because #ffe6cc (queue) and #fff2cc (store) sit only
12 RGB units apart - a reader genuinely cannot tell an event backbone from a database by colour.
Stores use arcSize rather than cylinder3: the lint asks for a distinguishing shape, and cylinder3
renders as a solid black box in some themes.
"""

from __future__ import annotations

import html
import pathlib
import sys

SERVICE, QUEUE, STORE, AI = "#dae8fc", "#ffe6cc", "#fff2cc", "#d5e8d4"
ACTOR, GATEWAY, NOTE, BAND = "#f8cecc", "#e1d5e7", "#fff9c4", "#eef2f7"
ZONE, ZONE2 = "#f5f5f5", "#e0e0e0"

STROKE = {
    SERVICE: "#6c8ebf",
    QUEUE: "#d79b00",
    STORE: "#d6b656",
    AI: "#82b366",
    ACTOR: "#b85450",
    GATEWAY: "#9673a6",
    NOTE: "#d6b656",
    BAND: "#6c8ebf",
    ZONE: "#999999",
    ZONE2: "#999999",
}
ZONE_STROKES = {"provider": "#d79b00", "network": "#6c8ebf", "cluster": "#82b366", "trust": "#b85450"}
EDGE, EDGE_AI = "#607d8b", "#558b2f"  # AI edge is NOT #82b366 - that is 2.4:1 on white


class Page:
    def __init__(self, name, pid, w, h):
        self.name, self.pid, self.w, self.h = name, pid, w, h
        self.cells, self.n = [], 0

    def _id(self, p):
        self.n += 1
        return f"{p}{self.n}"

    def box(self, label, x, y, w, h, fill, *, shape="rounded", fs=12, dashed=False, bold=False):
        st = []
        if shape == "rounded":
            st.append("rounded=1")
        elif shape == "square":
            st.append("rounded=0")
        elif shape == "store":
            st.append("rounded=1")
        elif shape == "queue":
            st.append("shape=parallelogram;perimeter=parallelogramPerimeter;fixedSize=1")
        elif shape == "hexagon":
            st.append("shape=hexagon;perimeter=hexagonPerimeter2")
        elif shape == "note":
            st.append("shape=note;size=14")
        st += [
            f"whiteSpace=wrap;html=1;fillColor={fill}",
            f"strokeColor={STROKE[fill]}",
            "fontColor=#000000",
            f"fontSize={fs}",
        ]
        if shape == "store":
            st.append("arcSize=40")
        if dashed:
            st.append("dashed=1")
        if bold:
            st.append("fontStyle=1")
        cid = self._id("c")
        self.cells.append(
            f'        <mxCell id="{cid}" value="{html.escape(label)}" style="{";".join(st)};" vertex="1" parent="1">\n'
            f'          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>\n'
            f"        </mxCell>"
        )
        return cid

    def zone(self, label, x, y, w, h, kind, *, fill=ZONE):
        cid = self._id("z")
        st = (
            f"rounded=0;whiteSpace=wrap;html=1;fillColor={fill};strokeColor={ZONE_STROKES[kind]};"
            f"dashed=1;strokeWidth=2;verticalAlign=top;align=left;spacingLeft=10;spacingTop=6;"
            f"fontColor=#000000;fontSize=12;fontStyle=1;"
        )
        self.cells.append(
            f'        <mxCell id="{cid}" value="{html.escape(label)}" style="{st}" vertex="1" parent="1">\n'
            f'          <mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/>\n'
            f"        </mxCell>"
        )
        return cid

    def title(self, label, x, y, w, h=40):
        return self.box(label, x, y, w, h, BAND, fs=15, bold=True)

    def row(self, items, x0, y, total_w, h, *, fill, shape="rounded", fs=11, gap=70):
        """n boxes across total_w with an exact gap. Widths are COMPUTED, never guessed."""
        w = (total_w - gap * (len(items) - 1)) // len(items)
        return [self.box(t, x0 + i * (w + gap), y, w, h, fill, shape=shape, fs=fs) for i, t in enumerate(items)]

    def edge(self, src, dst, label="", *, ai=False, exit_=None, entry=None, points=None):
        st = [
            "edgeStyle=orthogonalEdgeStyle",
            "rounded=1",
            "html=1",
            f"strokeColor={EDGE_AI if ai else EDGE}",
            "strokeWidth=2",
            "fontColor=#000000",
            "fontSize=10",
            "labelBackgroundColor=#ffffff",
        ]
        if ai:
            st.append("dashed=1")
        if exit_:
            st.append(f"exitX={exit_[0]};exitY={exit_[1]};exitDx=0;exitDy=0")
        if entry:
            st.append(f"entryX={entry[0]};entryY={entry[1]};entryDx=0;entryDy=0")
        eid = self._id("e")
        geo = '          <mxGeometry relative="1" as="geometry">\n'
        if points:
            geo += '            <Array as="points">\n'
            for px, py in points:
                geo += f'              <mxPoint x="{px}" y="{py}"/>\n'
            geo += "            </Array>\n"
        geo += "          </mxGeometry>\n"
        self.cells.append(
            f'        <mxCell id="{eid}" value="{html.escape(label)}" style="{";".join(st)};" '
            f'edge="1" source="{src}" target="{dst}" parent="1">\n{geo}        </mxCell>'
        )
        return eid

    def legend(self, x, y, entries, *, title="Legend"):
        rw, rh, pad = 260, 26, 34
        self.box(title, x, y, rw, pad, BAND, fs=11, bold=True)
        for i, (fill, shape, text) in enumerate(entries):
            self.box(text, x, y + pad + i * rh, rw, rh, fill, shape=shape, fs=10)

    def render(self):
        return (
            f'  <diagram name="{html.escape(self.name)}" id="{self.pid}">\n'
            f'    <mxGraphModel dx="1422" dy="762" grid="1" gridSize="10" guides="1" tooltips="1" '
            f'connect="1" arrows="1" fold="1" page="1" pageScale="1" pageWidth="{self.w}" '
            f'pageHeight="{self.h}" math="0" shadow="0">\n      <root>\n'
            '        <mxCell id="0"/>\n        <mxCell id="1" parent="0"/>\n'
            + "\n".join(self.cells)
            + "\n      </root>\n    </mxGraphModel>\n  </diagram>"
        )


# ============================== PAGE 0 - THE SPINE ===========================
# Layout-reviewer S19: "Page 1 - the spine. The numbered happy path, <= 8 boxes, nothing else.
# This is the page to memorise and the page to draw on a whiteboard." A diagram that is accurate
# but unmemorable fails its main job. The full architecture is page 2; the numbers reconcile.
p0 = Page("0. The Spine (whiteboard page)", "spine", 2000, 760)
p0.title("role-radar - the whole system in eight steps", 60, 30, 1000)

s1 = p0.box("1  profile.json\nwhose search this is", 60, 180, 200, 100, STORE, shape="store", fs=12)
s2 = p0.box("2  job boards\nATS APIs + CSV export", 330, 180, 200, 100, ACTOR, shape="square", fs=12)
s3 = p0.box("3  ingest\nnormalise to job dicts", 600, 180, 200, 100, SERVICE, fs=12)
s4 = p0.box("4  score + filter\nrelevance - delta - geo", 870, 180, 200, 100, SERVICE, fs=12)
s5 = p0.box("5  liveness\nis the posting still open?", 1140, 180, 200, 100, SERVICE, fs=12)
s6 = p0.box("6  ledger + board\nwhat am I pursuing?", 1410, 180, 200, 100, STORE, shape="store", fs=12)
s7 = p0.box("7  tailor the CV\nthrough the honesty gates", 870, 430, 200, 100, SERVICE, fs=12)
s8 = p0.box("8  PDF out\nCV_<Name>_<Company>.pdf", 1140, 430, 200, 100, STORE, shape="store", fs=12)

p0.edge(s1, s3, "drives every step", exit_=(0.5, 0), entry=(0.5, 0), points=[(160, 120), (700, 120)])
p0.edge(s2, s3, "", exit_=(1, 0.5), entry=(0, 0.5))
p0.edge(s3, s4, "", exit_=(1, 0.5), entry=(0, 0.5))
p0.edge(s4, s5, "", exit_=(1, 0.5), entry=(0, 0.5))
p0.edge(s5, s6, "", exit_=(1, 0.5), entry=(0, 0.5))
p0.edge(s6, s7, "pick one to pursue", exit_=(0.5, 1), entry=(0.5, 0), points=[(1510, 360), (970, 360)])
p0.edge(s7, s8, "", exit_=(1, 0.5), entry=(0, 0.5))

p0.box(
    "Steps 2-6 are DETERMINISTIC - zero LLM calls, so a market scan costs nothing.\n"
    "Judgement (is this role worth my time? does this CV claim only true things?)\n"
    "lives in steps 7-8 and in the skills, where a human reviews it.",
    60,
    430,
    700,
    110,
    NOTE,
    shape="note",
    fs=11,
)
p0.legend(
    1690,
    150,
    [
        (STORE, "store", "state you own"),
        (SERVICE, "rounded", "deterministic step"),
        (ACTOR, "square", "the outside world"),
    ],
    title="Legend",
)
p0.box(
    "Line style carries meaning:\nsolid = the request path\ndashed = AI / model call",
    1690,
    430,
    250,
    100,
    NOTE,
    shape="note",
    fs=10,
)

# ============================== PAGE 1 =======================================
p1 = Page("1. Context & Privacy Boundary", "ctx", 2140, 1340)
p1.title("role-radar - a job-search platform two people share without sharing their job searches", 60, 30, 1580)

a_you, a_col = p1.row(
    ["Job seeker A\n(repo owner)", "Job seeker B\n(collaborator)"], 60, 130, 480, 80, fill=ACTOR, shape="square", fs=12
)

p1.zone("$PROFILE_DIR  -  OUTSIDE the repo, one per user, gitignored", 60, 300, 500, 580, "trust")
d_prof = p1.box(
    "profile.json\nidentity - geo - roles - skills\nfacts - salary - csv - rag - research",
    100,
    360,
    420,
    100,
    STORE,
    shape="store",
    fs=11,
)
d_cv = p1.box("master CV (markdown)", 100, 520, 420, 60, STORE, shape="store", fs=11)
d_ledger = p1.box(
    "pipeline.json  +  PIPELINE_BOARD.md\nstage - outcome - posting_live - channels",
    100,
    640,
    420,
    80,
    STORE,
    shape="store",
    fs=11,
)
p1.box(
    "Nothing in this zone is ever committed.\n`publish_check` refuses a push that tracks it.",
    100,
    780,
    420,
    60,
    NOTE,
    shape="note",
    fs=10,
)

p1.zone("External systems", 60, 940, 500, 340, "provider")
x_boards = p1.box("Job boards\nGreenhouse - Comeet - LinkedIn guest", 100, 1000, 420, 70, ACTOR, shape="square", fs=11)
p1.box("Exa MCP  (Tier-3 discovery, optional)", 100, 1130, 420, 50, ACTOR, shape="square", fs=11)
x_llm = p1.box(
    "LLM providers  -  Ollama | Anthropic | OpenAI-compatible", 100, 1210, 420, 50, ACTOR, shape="square", fs=10
)

p1.zone("role-radar repo  -  SHARED, private on GitHub", 680, 300, 1020, 920, "network")

p1.zone("Interactive layer  -  .claude/  (14 prompt files)", 720, 360, 940, 200, "cluster", fill=ZONE2)
s_orch, _s_disc, _s_auth = p1.row(
    [
        "job-seeking\n(orchestrator)",
        "job-radar\nposition-filter",
        "cv-aligner - interview-prep\narchitect - diagram-generator",
    ],
    760,
    420,
    860,
    100,
    fill=SERVICE,
    fs=11,
)

p1.zone("Deterministic core  -  src/jobcore/  -  ZERO LLM calls", 720, 620, 940, 220, "cluster", fill=ZONE2)
k_prof, k_ing, k_out = p1.row(
    ["profile\n(whose search this is)", "ingest - scoring\nliveness", "gates - check\npdf - pipeline"],
    760,
    680,
    860,
    100,
    fill=SERVICE,
    fs=11,
)
k_rag = p1.box("rag/  -  retrieval over your own notes", 760, 800, 860, 40, AI, fs=11, dashed=True)

p1.zone("Quality gates  -  7 blocking, 3 enforcement points", 720, 920, 940, 220, "trust", fill=ZONE2)
g_all, _g_pp, _g_ci = p1.row(
    ["verify_all.sh\nlocal contract", "pre-push hooks\npre-commit", "ci.yml\nGitHub Actions"],
    760,
    980,
    860,
    90,
    fill=GATEWAY,
    shape="hexagon",
    fs=11,
)

p1.edge(a_you, d_prof, "own profile", exit_=(0.25, 1), entry=(0.25, 0))
p1.edge(a_col, d_prof, "own profile", exit_=(0.75, 1), entry=(0.75, 0))
p1.edge(a_you, s_orch, "invokes skills", exit_=(0.5, 0), entry=(0.5, 0), points=[(162, 100), (880, 100)])
p1.edge(s_orch, k_prof, "calls the core", exit_=(0.5, 1), entry=(0.5, 0))
p1.edge(k_prof, d_prof, "reads (never writes)", exit_=(0, 0.5), entry=(1, 0.5), points=[(620, 730), (620, 410)])
p1.edge(k_ing, x_boards, "polls", exit_=(0.5, 1), entry=(1, 0.5), points=[(1190, 880), (600, 880), (600, 1035)])
p1.edge(
    k_out, d_ledger, "writes the ledger", exit_=(0.5, 0), entry=(1, 0.5), points=[(1470, 590), (580, 590), (580, 680)]
)
p1.edge(
    k_rag,
    x_llm,
    "embeddings (local by default)",
    ai=True,
    exit_=(0, 0.5),
    entry=(1, 0.5),
    points=[(650, 820), (650, 1235)],
)
p1.edge(g_all, k_rag, "gates the core", exit_=(0.5, 0), entry=(0.5, 1))

p1.legend(
    1780,
    300,
    [
        (SERVICE, "rounded", "service / compute"),
        (STORE, "store", "store / state  (arcSize)"),
        (QUEUE, "queue", "queue / stream  (parallelogram)"),
        (AI, "rounded", "AI / retrieval  (dashed)"),
        (ACTOR, "square", "actor / external system"),
        (GATEWAY, "hexagon", "gate  (hexagon)"),
        (NOTE, "note", "annotation"),
    ],
    title="Legend - role is carried by shape AND colour",
)
p1.box(
    "Zone strokes: orange = external  -  blue = the repo\n"
    "green = a layer inside it  -  red = trust boundary (the privacy line)",
    1780,
    620,
    300,
    110,
    NOTE,
    shape="note",
    fs=10,
)

# ============================== PAGE 2 =======================================
p2 = Page("2. Discovery Pipeline", "flow", 1860, 960)
p2.title("Discovery pipeline - every source converges on ONE deterministic scoring path", 60, 30, 1660)

src_gh = p2.box("Greenhouse API\n(JSON)", 60, 150, 190, 60, ACTOR, shape="square", fs=11)
src_cm = p2.box("Comeet API\n(JSON)", 60, 250, 190, 60, ACTOR, shape="square", fs=11)
src_li = p2.box("LinkedIn guest\n(HTML scrape)", 60, 350, 190, 60, ACTOR, shape="square", fs=11)
src_csv = p2.box("Job-board CSV export\ncsv.pattern + sender_filter", 60, 470, 190, 70, ACTOR, shape="square", fs=10)
src_exa = p2.box("Exa MCP\n(deep mode only)", 60, 610, 190, 60, ACTOR, shape="square", fs=11)

ing_ats = p2.box("ats_ingest\nfetch_raw()", 340, 220, 180, 70, SERVICE, fs=11)
ing_csv = p2.box("csv_ingest\n_extract_job()", 340, 480, 180, 70, SERVICE, fs=11)
raw = p2.box("raw job dicts\ntitle - company - location - url - date", 610, 330, 230, 80, QUEUE, shape="queue", fs=10)

prof = p2.box("profile.json\ndrives every step below", 930, 170, 330, 70, STORE, shape="store", fs=11)
core = p2.box(
    "filter_score_positions()\nrelevance -> dedup -> delta -> stale -> geo -> score -> sort",
    930,
    320,
    330,
    100,
    SERVICE,
    fs=11,
)
seen = p2.box("seen-store\n.last_ats_seen.json (accumulates)", 930, 510, 330, 70, STORE, shape="store", fs=10)
live = p2.box("liveness\nOPEN | CLOSED | UNVERIFIED", 1350, 320, 210, 100, SERVICE, fs=11)

ledger = p2.box("pipeline ledger\nstage - outcome - posting_live", 1650, 180, 200, 100, STORE, shape="store", fs=11)
board = p2.box("PIPELINE_BOARD.md\ngenerated, never hand-edited", 1650, 350, 200, 80, STORE, shape="store", fs=10)
short = p2.box("_ats_shortlist.json\n+ ranked markdown", 1650, 510, 200, 80, STORE, shape="store", fs=10)

p2.box(
    "Delta keys are TAGGED: ('id', posting_id) or ('tc', title, company).\n"
    "Untagged 2-tuples let a re-titled req resurface as new.",
    610,
    510,
    230,
    110,
    NOTE,
    shape="note",
    fs=9,
)
p2.box(
    "A blocked probe is UNVERIFIED, never CLOSED.\nposting_live is a market fact - it never closes a pursuit.",
    1350,
    510,
    210,
    120,
    NOTE,
    shape="note",
    fs=9,
)

for s in (src_gh, src_cm, src_li):
    p2.edge(s, ing_ats, "", exit_=(1, 0.5), entry=(0, 0.5))
p2.edge(src_csv, ing_csv, "", exit_=(1, 0.5), entry=(0, 0.5))
p2.edge(src_exa, ing_csv, "", exit_=(1, 0.5), entry=(0, 0.9))
p2.edge(ing_ats, raw, "", exit_=(1, 0.5), entry=(0, 0.2))
p2.edge(ing_csv, raw, "", exit_=(1, 0.5), entry=(0, 0.8))
p2.edge(raw, core, "one shared tail", exit_=(1, 0.5), entry=(0, 0.5))
p2.edge(prof, core, "geo - titles - keywords", exit_=(0.5, 1), entry=(0.5, 0))
p2.edge(core, seen, "reads prev_keys, then advances the delta", exit_=(0.5, 1), entry=(0.5, 0))
p2.edge(core, live, "survivors", exit_=(1, 0.5), entry=(0, 0.5))
p2.edge(live, ledger, "record", exit_=(1, 0.2), entry=(0, 0.5))
p2.edge(live, short, "publish", exit_=(1, 0.8), entry=(0, 0.5))
p2.edge(ledger, board, "render", exit_=(0.5, 1), entry=(0.5, 0))

p2.legend(
    1350,
    700,
    [
        (ACTOR, "square", "external source"),
        (SERVICE, "rounded", "deterministic step (no LLM)"),
        (QUEUE, "queue", "in-flight data"),
        (STORE, "store", "persisted state"),
        (NOTE, "note", "invariant worth keeping"),
    ],
)

# ============================== PAGE 3 =======================================
p3 = Page("3. Gate Architecture", "gates", 1760, 920)
p3.title("Seven blocking gates, three enforcement points - a PASS has to mean something", 60, 30, 1580)

# The gates are a flush COLUMN inside a container: one visual unit, so the enforcement points
# take 3 edges instead of 9. A 9-edge hub is unreadable however correct the XML is.
p3.zone("The gate suite  -  every one of these can FAIL", 60, 140, 420, 420, "trust")
rows = [
    "ruff check  -  lint",
    "ruff format --check  -  formatting",
    "pytest (539)  -  behaviour",
    "publish check  -  no personal data tracked",
    "skill lint  -  prompt prose vs reality",
    "rag goldens  -  retrieval quality",
    "mypy  -  types",
]
g_ids = [p3.box(t, 100, 200 + i * 46, 340, 46, GATEWAY, shape="hexagon", fs=10) for i, t in enumerate(rows)]
anchor = g_ids[3]

e_local = p3.box("verify_all.sh\nthe local contract", 640, 170, 240, 90, SERVICE, fs=12)
e_push = p3.box("pre-commit\npre-push hooks", 640, 330, 240, 90, SERVICE, fs=12)
e_ci = p3.box("ci.yml\nGitHub Actions (Linux)", 640, 490, 240, 90, SERVICE, fs=12)

verdict = p3.box(
    "Every gate reports exactly one of\nPASS  /  FAIL  /  SKIPPED\n\nSKIPPED is NOT a pass - it exits non-zero",
    1020,
    300,
    280,
    140,
    STORE,
    shape="store",
    fs=12,
)
sec = p3.box("gitleaks\nfull-history secret scan", 1020, 510, 280, 90, GATEWAY, shape="hexagon", fs=11)

p3.box(
    "Why all three points, always:\nthe RAG gate once existed in NONE of them\nwhile verify_all.sh claimed to mirror CI.",
    1380,
    160,
    320,
    110,
    NOTE,
    shape="note",
    fs=10,
)
p3.box(
    "Vacuous-pass defences, each from a real escape:\n"
    "- zero goldens -> SKIPPED, exit 1\n- goldens that assert nothing -> FAIL\n"
    "- CI fixture untracked -> a git ls-files test\n- partial facts -> SKIPPED, never 'clean'",
    1380,
    330,
    320,
    150,
    NOTE,
    shape="note",
    fs=10,
)
p3.box(
    "gitleaks ran `git log <root>^..HEAD`, scanned ZERO\nbytes, and reported 'no leaks found'.\n"
    "Now a full-history scan - no range to get wrong.",
    1380,
    540,
    320,
    110,
    NOTE,
    shape="note",
    fs=10,
)

p3.edge(anchor, e_local, "the same commands", exit_=(1, 0.5), entry=(0, 0.5), points=[(560, 361), (560, 215)])
p3.edge(anchor, e_push, "", exit_=(1, 0.5), entry=(0, 0.5))
p3.edge(anchor, e_ci, "", exit_=(1, 0.5), entry=(0, 0.5), points=[(560, 361), (560, 535)])
p3.edge(e_local, verdict, "", exit_=(1, 0.5), entry=(0, 0.2))
p3.edge(e_ci, verdict, "", exit_=(1, 0.5), entry=(0, 0.8))
p3.edge(e_ci, sec, "separate job", exit_=(1, 0.8), entry=(0, 0.5), points=[(950, 563)])

p3.legend(
    1020,
    680,
    [
        (GATEWAY, "hexagon", "a gate (can FAIL)"),
        (SERVICE, "rounded", "enforcement point"),
        (STORE, "store", "verdict contract"),
        (NOTE, "note", "defect this exists to stop"),
    ],
)

# ============================== PAGE 4 =======================================
p4 = Page("4. RAG Lane", "rag", 1740, 880)
p4.title("RAG lane - retrieval over your own notes, gated on measured goldens", 60, 30, 1560)

corpus = p4.box("your notes\nrag.corpus_globs (in $PROFILE_DIR)", 60, 170, 220, 90, STORE, shape="store", fs=11)
chunk = p4.box("chunk\nheading-aware split", 360, 180, 190, 70, SERVICE, fs=11)
mem = p4.box("InMemoryBackend\nlexical - zero deps", 630, 90, 210, 70, SERVICE, fs=11)
chroma = p4.box("ChromaBackend\nembeddings - persistent", 630, 270, 210, 70, AI, fs=11, dashed=True)
store = p4.box("Store\ningest / query", 920, 180, 180, 70, SERVICE, fs=11)
rerank = p4.box("cross-encoder rerank\ntop-1: 5/10 -> 8/10", 1180, 180, 210, 70, AI, fs=11, dashed=True)
hits = p4.box("ranked hits\nsource + score", 1470, 180, 190, 70, QUEUE, shape="queue", fs=11)

goldens = p4.box("rag.goldens\nmust_retrieve - top_source - min_score", 360, 450, 270, 90, STORE, shape="store", fs=10)
gate = p4.box("rag eval  (BLOCKING)\nfixture corpus: 3/3", 760, 450, 250, 90, GATEWAY, shape="hexagon", fs=11)
skills_q = p4.box("semantic skill discovery\n'which skill does X?'", 1180, 450, 210, 90, AI, fs=11, dashed=True)

p4.box(
    "The index is built IN MEMORY from the working tree on every run.\n"
    "No persisted index means no stale index - the gate can never pass\nby reading yesterday's corpus.",
    60,
    630,
    620,
    100,
    NOTE,
    shape="note",
    fs=10,
)
p4.box(
    "A golden setting none of must_retrieve / must_not_retrieve /\ntop_source / min_score asserts NOTHING and now FAILS.\n"
    "Three such goldens once produced 'PASS goldens 3/3'.",
    780,
    630,
    620,
    100,
    NOTE,
    shape="note",
    fs=10,
)

p4.edge(corpus, chunk, "", exit_=(1, 0.5), entry=(0, 0.5))
p4.edge(chunk, mem, "default", exit_=(1, 0.5), entry=(0, 0.5))
p4.edge(chunk, chroma, "--backend chroma", ai=True, exit_=(1, 0.5), entry=(0, 0.5))
p4.edge(mem, store, "", exit_=(1, 0.5), entry=(0, 0.2))
p4.edge(chroma, store, "", ai=True, exit_=(1, 0.5), entry=(0, 0.8))
p4.edge(store, rerank, "over-fetch k=5", exit_=(1, 0.5), entry=(0, 0.5))
p4.edge(rerank, hits, "", ai=True, exit_=(1, 0.5), entry=(0, 0.5))
p4.edge(goldens, gate, "", exit_=(1, 0.5), entry=(0, 0.5))
p4.edge(store, gate, "scored against", exit_=(0.5, 1), entry=(0.5, 0), points=[(1010, 360)])
p4.edge(rerank, skills_q, "", ai=True, exit_=(0.5, 1), entry=(0.5, 0))

p4.legend(
    1470,
    450,
    [
        (STORE, "store", "state / config"),
        (SERVICE, "rounded", "deterministic"),
        (AI, "rounded", "embeddings / model"),
        (QUEUE, "queue", "in-flight"),
        (GATEWAY, "hexagon", "blocking gate"),
    ],
)

pages = [p0, p1, p2, p3, p4]
out = (
    '<mxfile host="app.diagrams.net" modified="2026-08-03T00:00:00.000Z" version="24.0.0">\n'
    + "\n".join(p.render() for p in pages)
    + "\n</mxfile>\n"
)
dest = pathlib.Path(sys.argv[1])
dest.parent.mkdir(parents=True, exist_ok=True)
dest.write_text(out, encoding="utf-8")
print(f"wrote {dest} ({len(out)} bytes, {len(pages)} pages)")
