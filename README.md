# SilentBreak

**The polygraph for data pipelines that say SUCCESS while shipping corrupt data.**

A human-overseen remediation agent built for the Google Cloud Rapid Agent Hackathon (Elastic partner track). It detects the contradiction between a pipeline's green status and its red data, diagnoses the exact schema mutation, presents the evidence, and only after a human presses-and-holds the APPROVE stamp does it quarantine the poisoned partition and flip the `revenue_current` alias to the last known good index. One atomic call reverses everything.

```
$ python scripts/run_demo.py        # zero dependencies, 10 seconds, the whole story
```

## The thesis

The most expensive data failures are silent. The orchestrator exits 0, the status doc says `SUCCESS`, every uptime monitor is green, and the revenue table is wrong. Our demo villain is realistic: an SDK migration commit renames `amount` to `gross_amount` upstream, the loader keeps mapping `amount`, and 100% of revenue rows land null while run #4471 reports SUCCESS. Nobody notices until finance closes the books.

SilentBreak is built on one idea: **status and data are two witnesses, and an agent should cross-examine them.** When the pipeline says SUCCESS and the data says corrupt, that contradiction is the alarm. And because the detector and the actuator live in the same Elasticsearch cluster, the agent does not just alert. It acts: a reversible alias flip that instantly stops every downstream consumer from reading poison.

## What actually happens (the loop)

1. **Sentinel** reads the pipeline's own status doc (the green lie), then fingerprints the landed partition with ES|QL stats (`null_rate`, `avg_amount`, `distinct_sku`) and z-scores each metric against a rolling baseline. First breach at z >= 5 raises a CONTRADICTION (the demo run breaches at z = 40).
2. **RootCause** diffs the index mappings (today vs yesterday), names the mutation (`- amount` / `+ gross_amount`), and estimates the dollar exposure (nulled rows x baseline average order value, always labeled "estimated").
3. **The operator gate.** The run pauses. The UI shows the evidence and the exact action plan. Nothing mutates Elasticsearch until a human press-and-holds the APPROVE stamp, which mints a single-use approval token (TTL 300s). REJECT means zero writes, provably.
4. **Guardian** consumes the token (no token, no writes), reindexes the corrupt rows into a quarantine index, and flips the alias to yesterday's index in one atomic `update_aliases` call.
5. **Verify** re-runs the downstream revenue query through the alias and shows it healthy again (null_rate back to 0.002).
6. **Scribe** persists a full incident document, including which engine ran, who wrote the report, and the approval token id.
7. **Reverse** (the proof it is real): one button flips the alias back. State change, not theater.

## The load-bearing map

Every stage names the exact capability that powers it. Remove Elastic and there is no detector, no diagnosis, and no actuator. Remove the Google stack and there is no reasoning engine.

| Loop stage | Elastic capability | Google capability |
|---|---|---|
| Detect | ES&#124;QL `STATS COUNT(*), COUNT_DISTINCT(sku)` plus `exists` query, via the **official Elastic MCP server** (`esql`, `search` tools) | Gemini 3.5 Flash reasons over MCP tool results (ADK engine); transparent z-score math (deterministic engine) |
| Diagnose | MCP `get_mappings` diff, today vs yesterday | Gemini writes the diagnosis prose (ADK); template prose (deterministic), `report_author` recorded honestly |
| Approve | nothing happens here, by design | ApprovalRegistry token gate shared by **both** engines (chosen deliberately over ADK's confirmation flow) |
| Act | `_reindex` quarantine plus **one atomic `update_aliases`** (elasticsearch-py) | Guardian is a single FunctionTool gated on the token |
| Verify | ES&#124;QL through the alias via MCP | same event stream either way |
| Record | incident doc into `silentbreak-incidents` | ADK `SequentialAgent` orchestrates Sentinel, RootCause, Guardian, Scribe |

**The honest read/write split:** in real mode all *reads* (ES|QL stats, mappings, status doc, verification query) go through the official Elastic MCP server (`docker.elastic.co/mcp/elasticsearch`, streamable HTTP). The MCP server exposes no write tools, so *writes* (seed, quarantine reindex, alias flip, incident doc) go through elasticsearch-py directly. MCP tool schemas are discovered at runtime from `tools/list` and locked by the smoke test: `esql{query}`, `get_mappings{index}`, `search{fields,index,query_body}`, `list_indices{index_pattern}`.

## Architecture

```
                       POST /api/run                 press-and-hold stamp
  Polygraph UI  ---------------------->  FastAPI  <--------------------  operator
  (vanilla JS,  <----------------------  app/main.py
   SSE client)     typed SSE events          |
                                             |  engine select: adk if (real + GOOGLE_API_KEY
                                             |  + google-adk importable) else deterministic
                              +--------------+--------------+
                              |                             |
                    ADK 2.x SequentialAgent        deterministic orchestrator
                    Sentinel>RootCause>             (same loop, same events,
                    Guardian>Scribe                  same approval gate)
                    model: gemini-3.5-flash                 |
                              |                             |
                              +-----------+-----------------+
                                          |
                         +----------------+----------------+
                         | READS                           | WRITES (token-gated)
                         v                                 v
              Elastic MCP server (HTTP :8080)      elasticsearch-py
              esql / get_mappings / search          _reindex quarantine
                         |                          update_aliases (atomic)
                         v                          incident doc
                  Elasticsearch 9.4  ...  sales-YYYY-MM-DD partitions,
                  alias revenue_current, silentbreak-{status,baselines,incidents}
```

## Quickstart A: mock, 10 seconds, zero setup

Python 3.11+ standard library only. No packages, no docker, no keys.

```
python scripts/run_demo.py
```

To see the full Polygraph UI against the same in-memory Elastic:

```
make install     # fastapi + uvicorn (and the real-mode deps)
make web         # http://localhost:8000  ->  press RUN EXAMINATION
```

This is also exactly what the hosted Cloud Run URL serves: fully interactive, deterministic engine, in-memory Elastic, real approval gate.

## Quickstart B: local real mode (Elasticsearch + official Elastic MCP server)

```
make install
make up          # docker compose: Elasticsearch 9.4.2 + Elastic MCP server (http :8080)
make seed        # 14 days of healthy e-commerce partitions + baselines + SUCCESS status docs
make inject      # today's silently corrupted partition; alias -> poison; status doc: SUCCESS
SILENTBREAK_MODE=real make web    # http://localhost:8000
```

Press RUN EXAMINATION. Without a `GOOGLE_API_KEY` the run uses the deterministic engine (labeled "DETERMINISTIC FALLBACK (no Gemini key)" in the UI and in the SSE stream) but every read still goes through the MCP server and every write is real. With `GOOGLE_API_KEY` in `.env`, the ADK/Gemini engine drives the same loop through the same gate.

Note for repeated takes: a remediated run really moves the corrupt rows into quarantine, so the poison is consumed. Run `make inject` again between takes. `make reset` wipes the world.

End-to-end assertion suite (isolated `sales-smoke-` prefix, restores your demo alias afterwards):

```
make smoke       # prints SMOKE PASS on a green stack
```

## Quickstart C: cloud

**Hosted URL (what judges click):** the Cloud Run image defaults to mock mode so it needs zero secrets and is fully interactive.

```
make deploy-cloud-run
# = gcloud run deploy silentbreak --source . --region us-central1 --allow-unauthenticated
```

**Real mode on cloud** is configuration only: set `SILENTBREAK_MODE=real`, `ES_URL` plus `ELASTIC_API_KEY` (or `ELASTIC_CLOUD_ID`), and point `MCP_URL` at a reachable MCP server. Elastic Agent Builder (GA in Elastic 9.4) exposes an MCP endpoint at `{KIBANA_URL}/api/agent_builder/mcp`; pass its key via `MCP_AUTH_HEADER="ApiKey ..."`. The standalone Elastic MCP server logs that it is superseded by the Agent Builder endpoint, which is exactly why both are supported through the same two variables. Copy `.env.example` to `.env` to configure everything.

## The HITL gate: a stamp, not a checkbox

Approval is the centerpiece, so it costs deliberate physical effort: press and hold the circular stamp for 1.2 seconds while a radial progress ring fills, then release to slam a rotated "QUARANTINE APPROVED" impression onto the paper. Under the ink it is strict: the hold mints a **single-use token** (TTL 300s) through an `ApprovalRegistry`; the Guardian consumes it before any write; reused, expired, or missing tokens mean zero mutations and a vetoed run. REJECT is one flat click, and the mutation never happens. Both engines, ADK and deterministic, pass through this same gate. The hold is keyboard accessible (hold Space or Enter) and the arming decision is measured in elapsed time, not animation frames.

## Design rationale

The UI is a polygraph examination, because that is literally what the agent does: cross-examines a subject whose own testimony says SUCCESS. Cream chart paper (#F5F0E6) with ink-black needle traces draws the three metrics live as the Sentinel sweeps history into today; a breach spikes the needle and inks a red `z=40` annotation. The pipeline's self-report sits in a smug green tile until the red CONTRADICTION stamp slams over it. The examiner's report types itself out character by character. The downstream consumer is a phosphor-green teletype strip running the live revenue query, so you watch the alias flip heal it in real time. No dashboards, no cards, no gradients, no frameworks: one HTML file, one stylesheet, one vanilla JS file, zero external requests.

## What is verified vs what is claimed

Everything above the Roadmap heading is exercised by code in this repo:

- `make demo` runs the full loop on the Python standard library and exits 0.
- `make smoke` asserts the real path end to end against docker: MCP tool discovery, seed, inject, contradiction on `null_rate` with z near 40, diagnosis naming `gross_amount`, token-gated quarantine plus alias flip, downstream null_rate < 0.01 through the alias, incident persisted, reverse, cleanup, demo alias restored.
- The web UI loop (run, SSE stream, press-and-hold approve, flip, verify, reverse; plus the reject path with zero writes) was verified in a real browser in both mock mode and real mode with the deterministic engine.
- The ADK/Gemini engine path (`agents/adk_pipeline.py`) imports cleanly, is wired to the same gate and event stream, and falls back to the deterministic engine on any runtime error. It requires a `GOOGLE_API_KEY`, which this build environment did not have, so it has **not** been executed against the live Gemini API. The engine in use is always labeled honestly in the SSE stream, the UI badge, and the incident doc (`engine`, `report_author`).

## Scope and Roadmap

In scope and working: the full detect, diagnose, approve, quarantine, flip, verify, record, reverse loop in mock and local-real modes; the Polygraph UI; the smoke suite; the Cloud Run image.

Roadmap (not implemented, listed honestly):

- Live verification of the ADK plus Gemini 3.5 Flash engine against the API, and prompt tuning for the Scribe's report prose.
- Elastic Agent Builder Workflows as an alternative actuator (Workflows went GA in 9.4; today the actuator is direct elasticsearch-py).
- Multi-metric incident correlation across days, and notification fan-out (Slack/email) after the operator decision.
- Auth on the web UI (the hosted demo is intentionally open and stateless).

## The 3-minute demo video

The beat-by-beat recording plan lives in [docs/DEMO_SCRIPT.md](docs/DEMO_SCRIPT.md). The shape: 20 seconds of thesis over the green SUCCESS tile, one continuous take of the examination (needles draw, stamp slams, report types), the press-and-hold approval as the centerpiece, the teletype healing on the flip, and the reverse as the closer that proves the action was real.

## Devpost submission checklist

- [ ] Hosted project URL: Cloud Run service (mock mode, zero secrets) from `make deploy-cloud-run`
- [ ] Public repo with detectable OSS license (MIT, see below)
- [ ] About 3 minute demo video following `docs/DEMO_SCRIPT.md`
- [ ] Required tech stated plainly: Google ADK 2.x (Agent Builder family) plus Gemini 3.5 Flash plus the official Elastic MCP server
- [ ] Human-in-the-loop oversight demonstrated on camera (the stamp, the reject path, the reverse)
- [ ] README claims match repo reality (this file)

## Repo map

```
agents/          sentinel, rootcause, guardian, scribe, orchestrator (deterministic engine),
                 adk_pipeline (ADK 2.x SequentialAgent + McpToolset, lazy imports)
integrations/    elastic_client (mock + real), mcp_client (official mcp SDK, streamable HTTP),
                 approval (the token registry behind the stamp)
app/             FastAPI + per-run EventBus (SSE with buffer replay)
ui/              the Polygraph: index.html, styles.css, app.js, no build step
scripts/         run_demo (stdlib), seed_baseline, inject_drift, reset_world, smoke_real + smoke.sh
docker/          Cloud Run image       docker-compose.yml   ES 9.4.2 + Elastic MCP server
```

## License

MIT, copyright (c) 2026 Nihal Nihalani. See [LICENSE](LICENSE).
