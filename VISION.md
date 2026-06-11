# VISION — SilentBreak

> Intent anchor for the ship loop. Read before every tick. Changes here are deliberate, rare, and human-approved.

## What the project is
**SilentBreak is a polygraph for data pipelines.** Pipelines report SUCCESS while shipping corrupt
data; SilentBreak cross-examines that claim against the data itself, detects the contradiction,
diagnoses the exact schema mutation, and remediates — but only after a human press-and-holds the
APPROVE stamp. Every write is token-gated, reversible, and recorded as an incident the next run recalls.

## The demo we show judges (≤3 min, script: docs/DEMO_SCRIPT.md)
1. Green SUCCESS tile — the pipeline's own testimony (20s thesis).
2. RUN EXAMINATION: ink needles sweep 9 healthy days, then the red CONTRADICTION stamp slams
   (null_rate 0.002 → 1.000, z=40); the examiner's report names `amount`→`gross_amount`, ~$429K at risk.
3. Centerpiece: the press-and-hold APPROVE stamp (1.2s, single-use token, TTL 300s).
4. Quarantine 10,000 rows → atomic alias flip to last-known-good → repair reindex → verify through the alias.
5. Closer: REVERSE — one atomic call flips it back. A state change, not a video effect.

## Judging criteria we optimize for (rapid-agent.devpost.com/rules — 4 equal weights)
1. **Technological Implementation** — real ADK 2.x `SequentialAgent` + `McpToolset` + Gemini 3.5 Flash;
   Elastic Agent Builder MCP endpoint; runtime tool-schema discovery; tests + CI; smoke suite.
2. **Design** — the polygraph metaphor IS the function; HITL gate costs deliberate physical effort.
3. **Potential Impact** — silent data corruption is universal (Gartner: $12.9M/yr avg cost of poor data);
   detector/actuator/memory are all Elasticsearch, so the loop generalizes to any indexed dataset.
4. **Quality of the Idea** — contradiction detection ("your status and your data disagree"), not
   anomaly detection; remediation that names its trade-offs out loud and proves reversibility.
Stage One (pass/fail): hosted URL ✓, public repo + MIT ✓, description ✓, **demo video (USER records)**.

## Architecture (rendered: docs/img/architecture.png)
Polygraph UI (vanilla JS, SSE) → FastAPI (`app/main.py`) → engine select:
ADK 2.x SequentialAgent (Sentinel → RootCause → Guardian → Scribe, gemini-3.5-flash via Vertex ADC,
location=global) or deterministic orchestrator (same loop, same gate, labeled honestly).
Reads via Elastic MCP (streamable HTTP); writes via elasticsearch-py, token-gated
(quarantine reindex, atomic alias flip, repair rename, incident doc). ES 9.4.
Hosted: Cloud Run `silentbreak`, mock mode (zero external deps during judging), `/api/healthz`.

## Non-negotiables (learned the hard way; do not regress)
- The engine in use is ALWAYS labeled honestly (UI badge, SSE, incident doc).
- No write without a consumed single-use approval token. Approval is never spent twice.
- Every claim in README/Devpost is backed by executable evidence.
- The hosted demo must survive judging (Jun 22 – Jul 6) unattended: mock mode, no expiring deps.
