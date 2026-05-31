# 🔇 SilentBreak — the failures your dashboards swear never happened

> **Google Cloud Rapid Agent Hackathon** · Partner track: **Elastic** · Deadline: **June 11, 2026**
> Status: 🏆 *Best-bet pick across all idea sets (highest floor, un-fakeable action).*

An autonomous **data-reliability agent** that catches pipelines which report **`SUCCESS` while silently shipping corrupt data**, traces the root cause, **quarantines the bad partition, and resyncs — all natively inside Elasticsearch.**

---

## 🎯 The Problem
A data pipeline run is marked **SUCCESS** (Airflow/dbt exit 0), every uptime monitor is green — but the output table is silently wrong. An upstream column rename (`amount` → `gross_amount`) nulls 12,400 revenue rows; a unit flips cents→dollars; a join fans out row counts 3×. **Nobody notices** until finance closes the books days later, and six figures have already been misreported.

- **Gartner:** poor data quality costs the average organization **~$12.9M/year**.
- The dangerous class is **silent**: `status = SUCCESS` **AND** `data = CORRUPT` — the one thing freshness/uptime monitors *don't* catch.

## ⚡ What It Does (the consequential action)
SilentBreak runs a closed loop entirely inside Elasticsearch:
1. **Detect** the contradiction (green status, red data) via Elasticsearch aggregations vs. a rolling baseline.
2. **Diagnose** the exact schema mutation that caused it via a native mapping diff.
3. **Act** — **quarantine** the corrupt partition into an isolation index and **flip the `revenue_current` alias** off the poisoned index so every downstream consumer instantly stops reading bad data; then **trigger a clean connector resync.**

The action is a **real, reversible state change** (an alias pointer swap) — un-fakeable on camera, with zero external grants or mocks.

## 🧠 Why the Elastic MCP is Load-Bearing
The **detector and the actuator live in the same Elasticsearch cluster** — remove Elastic and there is no product:
- **Detector** = ES|QL `STATS` aggregation (`row_count`, `null_rate`, `distinct_count`, `avg`) over the just-landed partition.
- **Diagnosis** = native `get_mappings` diff between today's and yesterday's index.
- **Action** = `reindex` (quarantine) + `update_aliases` (the alias flip) + connector resync.

> Unlike "search" submissions where Elastic is a lookup, here Elastic **is** the detection, the diagnosis, **and** the action.

---

## 🏗️ Architecture
**Stack:** Gemini 3 (reasoning) · Google Cloud Agent Builder / ADK (orchestration + session state) · **Elastic MCP server** (load-bearing engine) · Cloud Run (hosted URL) · a thin React dashboard.

Four agents, a sequential-with-branch ADK workflow, triggered by a pipeline-`completed` webhook:

| Agent | Role | Key Elastic MCP calls |
|-------|------|------------------------|
| **Sentinel** (detector) | Computes the new run's data fingerprint; z-scores it vs. the 30-day baseline index | `esql_query` / `search` with aggregations; `search` on `silentbreak-baselines` |
| **RootCause** (diagnoser) | Fires only on a contradiction; names the exact mutation | `get_mappings` (today vs. yesterday) + `esql_query` correlation |
| **Guardian** (actuator) | The consequential action | `reindex`/`bulk` → `silentbreak-quarantine-*`; `update_aliases` (flip); connector **sync** API |
| **Scribe** (reporter) | Persists the incident timeline + post-incident summary | `index` into `silentbreak-incidents` |

**Gemini 3's job:** decide which checks matter for this table, interpret the aggregation deltas + mapping diff, and write the one-sentence root cause ("upstream renamed `amount`→`gross_amount` at 02:14; loader kept mapping `amount`, nulling 12,400 revenue rows ≈ $533k misreported").

```
pipeline.completed ──▶ Sentinel ──(contradiction?)──▶ RootCause ──▶ Guardian ──▶ Scribe
                          │ no                                         │
                          └────────── log green & exit                 └── alias flipped, resync fired
```

---

## 🎬 3-Minute Demo Script (build to this)
- **0:00–0:10 — The lie.** A finance dashboard: revenue chart + big green `Pipeline run #4471 — SUCCESS ✓`.
- **0:10–0:40 — The trap, on real data.** Every monitor green; show a one-line upstream git diff: `amount` → `gross_amount`. "No alert fired. Why would it? The pipeline succeeded."
- **0:40–1:15 — Sentinel catches the contradiction.** Webhook fires live; the ES|QL aggregation streams `null_revenue = 12,400` vs. baseline `~30`. Red overlay on the green tile: **`CONTRADICTION: status=SUCCESS, data=CORRUPT — null-revenue 0.2%→41%, z=37`**.
- **1:15–1:50 — RootCause names it.** Side-by-side `get_mappings` diff highlights `- amount / + gross_amount`; Gemini's one-line verdict types on screen with the **$533k** impact.
- **1:50–2:40 — The visible action.** Guardian runs the MCP calls live: `reindex` → `silentbreak-quarantine-2026-06-01` (count ticks up), `update_aliases` flips `revenue_current` off the poisoned index; the finance tile reverts to **last-known-good in real time**; resync corrects 12,400 → 0 on screen.
- **2:40–3:00 — Prove it's not canned.** Flip the alias **back** on camera (reversible). Close on the green dashboard now telling the truth.

> **Differentiator to lead with (devil's-advocate note):** "data observability" is a crowded theme and the alias flip is low-drama. So **open on a live downstream consumer returning a visibly WRONG answer** (a dashboard tile or a RAG answer), make the action the climax, and **flip back** to prove it's a real reversible state change, not a video. Anchor to a *specific 2026 schema-drift* so it reads as current.

---

## 🚀 Getting Started
### Prerequisites
- **Google Cloud** project with billing; **Vertex AI / Gemini 3** + **Agent Builder (Gemini Enterprise Agent Platform)** enabled; **Cloud Run**.
- **Elastic Cloud** deployment (trial is fine) + the **Elastic MCP server** (`mcp-server-elasticsearch` or the Elastic Agent Builder MCP server).
- Node 20+ and/or Python 3.11+, `gcloud` CLI.

### Setup
```bash
git clone <your-fork-url> silentbreak-elastic && cd silentbreak-elastic
cp .env.example .env   # fill in the values below

# 1) Seed the demo data plane
#    - a 30-day baseline index `silentbreak-baselines` from a real dbt/Airflow demo pipeline
#    - daily partitioned indices `sales-YYYY-MM-DD`
python scripts/seed_baseline.py

# 2) Register the Elastic MCP + GitHub(optional) MCP with Agent Builder, deploy the 4-agent graph
python scripts/deploy_agents.py

# 3) Run locally / deploy the webhook + dashboard
npm install && npm run dev          # dashboard
gcloud run deploy silentbreak --source .   # hosted URL for judging
```

### `.env` (see `.env.example`)
```
GOOGLE_CLOUD_PROJECT=
GEMINI_API_KEY=                 # or Vertex auth
ELASTIC_CLOUD_ID=
ELASTIC_API_KEY=
ELASTIC_MCP_URL=
```

### Proposed project structure
```
silentbreak-elastic/
├── agents/            # Sentinel, RootCause, Guardian, Scribe (ADK definitions + prompts)
├── mcp/               # Elastic MCP client config + tool wrappers
├── scripts/           # seed_baseline.py, deploy_agents.py, inject_drift.py
├── dashboard/         # React: green tile, contradiction overlay, quarantine counter
├── webhook/           # Cloud Run entrypoint (pipeline.completed)
├── .env.example
├── LICENSE
└── README.md
```

---

## 🗓️ Build Plan (today → June 11)
| Day | Goal |
|-----|------|
| 1 | Elastic Cloud + MCP up; seed `silentbreak-baselines` (30 days) + daily partitions; one ES\|QL aggregation returning real numbers |
| 2 | Sentinel: z-score contradiction detection; `inject_drift.py` to rename a column live |
| 3 | RootCause: `get_mappings` diff + Gemini root-cause sentence |
| 4 | Guardian: `reindex` quarantine + `update_aliases` flip + resync — the real action |
| 5 | Dashboard: green tile → red overlay → revert-on-flip; wire the webhook |
| 6 | Scribe + incident index; Agent Builder graph end-to-end on Cloud Run |
| 7 | Polish the "wrong downstream answer → correct after flip → flip back" arc |
| 8 | Rehearse the 3-min demo; deterministic, repeatable run |
| 9 | Hosted URL live; README + LICENSE; full dry run |
| 10 | Record ≤3-min video; submit on Devpost **before 2:00pm PDT June 11** |

## ⚠️ Biggest Risk → Mitigation
**Risk:** "data observability" is the most crowded Elastic sub-theme, and a pointer-swap is low-drama → could score a clean 56 and still lose to a flashier 50.
**Mitigation:** reframe as *"autonomous data-integrity remediation that reverses itself."* Lead the demo with the un-fakeable reversible action (not detection), open on a visibly wrong downstream result, and pick a timely, specific 2026 schema-drift so it reads as current-events, not generic.

## ✅ Submission Checklist
- [ ] Functional agent on a **live hosted URL** (judge can test)
- [ ] **Elastic MCP is load-bearing** — stated in README; removing it breaks detection + action
- [ ] Visible consequential action (alias flip) on camera + reversible
- [ ] Gemini 3 + Agent Builder genuinely used
- [ ] Public repo + **LICENSE visible at top** (✓ this repo)
- [ ] **≤3-min** demo video on YouTube/Vimeo, public
- [ ] Devpost form: description + **Elastic track** selected

## 🧰 Tech Stack
Gemini 3 · Google Cloud Agent Builder / ADK · Elastic MCP (Elasticsearch + ES|QL) · Cloud Run · React.

## 📄 License
MIT — see [LICENSE](./LICENSE).
