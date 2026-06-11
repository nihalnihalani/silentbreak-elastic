#!/usr/bin/env python3
"""Render the README architecture diagram to docs/img/architecture.png.

Component names are copied verbatim from the ASCII diagram in README.md so the
rendered image and the terminal-friendly ASCII never drift. Palette matches the
Polygraph UI screenshots: parchment paper, dark ink, oxblood-red accent for the
operator gate, muted green for the deterministic path.

Requires the graphviz `dot` binary (brew install graphviz) and the python
`graphviz` package.
"""
from graphviz import Digraph

# --- Palette lifted from the UI screenshots -------------------------------
PAPER = "#ECE7D7"      # parchment background
INK = "#23201A"        # dark ink text / borders
RED = "#A22A22"        # oxblood — the CONTRADICTION stamp / operator gate
GREEN = "#5E6B3B"      # muted olive — deterministic path
BLUE = "#3A5A6B"       # slate — Elastic reads
NODE_FILL = "#F4F0E3"  # slightly lighter card fill

g = Digraph("silentbreak", format="png")
g.attr(
    bgcolor=PAPER,
    rankdir="TB",
    splines="ortho",
    nodesep="0.45",
    ranksep="0.55",
    fontname="Courier New",
    pad="0.4",
)
g.attr(
    "node",
    shape="box",
    style="filled",
    fillcolor=NODE_FILL,
    color=INK,
    fontcolor=INK,
    fontname="Courier New",
    fontsize="13",
    penwidth="1.6",
    margin="0.18,0.12",
)
g.attr("edge", color=INK, fontname="Courier New", fontsize="11", fontcolor=INK, penwidth="1.4")

# --- Top row: UI / FastAPI / operator -------------------------------------
g.node(
    "ui",
    "Polygraph UI\n(vanilla JS, SSE client,\n4-lamp agent rail)",
)
g.node(
    "api",
    "FastAPI\napp/main.py\ntyped SSE events",
    fillcolor="#EBE3CE",
)
g.node("operator", "operator", shape="box", style="filled,bold", fillcolor=NODE_FILL)

g.edge("ui", "api", label="  POST /api/run  ")
g.edge("operator", "api", label="  press-and-hold stamp  ", color=RED, fontcolor=RED)

# --- Engine select --------------------------------------------------------
g.node(
    "select",
    "engine select: adk if (real + Gemini creds\n[GOOGLE_API_KEY or Vertex ADC]\n+ google-adk importable) else deterministic",
    shape="box",
    style="filled,dashed",
    fillcolor=PAPER,
    fontsize="11",
)
g.edge("api", "select")

# --- Two engines ----------------------------------------------------------
g.node(
    "adk",
    "ADK 2.x SequentialAgent\nSentinel > RootCause >\nGuardian > Scribe\nmodel: gemini-3.5-flash",
)
g.node(
    "det",
    "deterministic orchestrator\n(same loop, same events,\nsame approval gate)",
    fillcolor="#E6E9D6",
    color=GREEN,
    fontcolor=GREEN,
)
g.edge("select", "adk")
g.edge("select", "det")

# --- Reads / Writes fan-out ----------------------------------------------
g.node(
    "reads",
    "Elastic MCP (streamable HTTP)\nprod: Agent Builder MCP endpoint\ndev:  local container :8080\nesql / get_mappings / search",
    color=BLUE,
    fontcolor=BLUE,
)
g.node(
    "writes",
    "elasticsearch-py\n_reindex quarantine\n_reindex repair (painless rename)\nupdate_aliases (atomic)\nincident doc",
    color=RED,
    fontcolor=RED,
)
g.edge("adk", "reads")
g.edge("adk", "writes")
g.edge("det", "reads")
g.edge("det", "writes")
g.edge("reads", "writes", label="  READS                 WRITES (token-gated)  ", style="invis")

# --- Elasticsearch sink ---------------------------------------------------
g.node(
    "es",
    "Elasticsearch 9.4\nsales-YYYY-MM-DD partitions,\nalias revenue_current, quarantine + -repaired indices,\n"
    "silentbreak-{status,baselines,incidents}\n<- agent memory, written by Scribe,\nrecalled by Sentinel on the next run",
    fillcolor="#EBE3CE",
)
g.edge("reads", "es")
g.edge("writes", "es")

# Keep the two engines on the same rank, and reads/writes on the same rank.
with g.subgraph() as s:
    s.attr(rank="same")
    s.node("adk")
    s.node("det")
with g.subgraph() as s:
    s.attr(rank="same")
    s.node("reads")
    s.node("writes")
with g.subgraph() as s:
    s.attr(rank="same")
    s.node("ui")
    s.node("operator")

g.render("docs/img/architecture", cleanup=True)
print("wrote docs/img/architecture.png")
