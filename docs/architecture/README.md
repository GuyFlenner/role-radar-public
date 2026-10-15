# Architecture

`role_radar_architecture.drawio` — five pages, opens in
[app.diagrams.net](https://app.diagrams.net) or the VS Code draw.io extension.

| Page | What it answers | Read it when |
|---|---|---|
| **0. The Spine** | The whole system in 8 numbered steps | You are about to present or whiteboard this. Memorise this page and nothing else. |
| **1. Context & Privacy Boundary** | Who uses it, and why two people can share the repo without sharing their job searches | Onboarding someone, or asking "can my collaborator see my CV?" |
| **2. Discovery Pipeline** | How a role travels from a job board to your ledger | Debugging why a role did or didn't appear |
| **3. Gate Architecture** | The 8 blocking gates × 3 enforcement points | A gate failed, or you are adding one |
| **4. RAG Lane** | Retrieval over your own notes, and what gates it | Working on `src/jobcore/rag/` |

Page 0 exists because a diagram that is accurate but unmemorable fails its main job. It is
capped at 8 boxes — roughly what working memory holds — and its numbers reconcile with page 2.

## Which file is authoritative

**`role_radar_architecture.drawio` is the artifact of record.** The gate lints it, CI runs the
gate, and it is what a reader opens.

`generate_architecture.py` is how it was built: it computes every coordinate so the lint's
spacing, clearance and no-overlap rules cannot be violated by hand-placing ~70 boxes. Use it for
a full re-layout — regenerating is far quicker than hand-fixing a crowded page.

**Be aware of the tradeoff:** if you hand-edit the `.drawio` in the GUI, the generator becomes
stale, and the two disagree. That is exactly the two-implementations-of-one-thing drift this repo
keeps finding, so it is stated rather than pretended away. Hand-editing is fine — the gate still
protects the artifact — but either re-apply your change in the generator or accept that it is now
a bootstrap script rather than the source.

## After ANY edit

```bash
uv run python -m src.jobcore.diagram_gate        # every tracked .drawio (BLOCKING in CI)
```

Dragging things in the draw.io GUI introduces defects the editor never warns about — the worst
being a **detached edge**: drag an arrow's end off a node and draw.io drops `source=`/`target=`
and writes a floating point. The line still looks connected but is bound to nothing. The gate
catches it; your eye will not.

To regenerate instead:

```bash
uv run python docs/architecture/generate_architecture.py docs/architecture/role_radar_architecture.drawio
uv run python -m src.jobcore.diagram_gate
```

## Conventions the gate enforces

Role is carried by **shape as well as colour**, because `#ffe6cc` (queue) and `#fff2cc` (store)
sit only 12 RGB units apart — a reader genuinely cannot tell an event backbone from a database by
colour, and neither can a colour-blind one. Nested zones share one neutral fill and differ by
**stroke**, not by inventing a pale tint per layer. Every text-bearing cell has a light fill,
because black text on the canvas background is invisible in dark theme. Edge strokes clear 3:1 on
both canvases. Full rules and rationale: `src/jobcore/drawio_lint.py`.
