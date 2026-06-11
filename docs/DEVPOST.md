# Devpost submission text (paste-ready)

> One placeholder left at the bottom (the video URL) — fill it after recording, and
> recording. Everything else is final copy. Keep the radical-honesty voice if you edit.

---

## Project name

**SilentBreak**

## Tagline (elevator pitch field)

The polygraph for data pipelines that say SUCCESS while shipping corrupt data. A multi-agent Gemini + Elastic system that detects the lie, diagnoses the schema break, and remediates — but only after a human press-and-holds the APPROVE stamp.

---

## Inspiration

The most expensive data failures are silent. The orchestrator exits 0, the status doc says `SUCCESS`, every uptime monitor is green — and the revenue table is wrong. We have all lived some version of the villain in our demo: an upstream SDK migration renames `amount` to `gross_amount`, the loader keeps mapping `amount`, 100% of revenue rows land null, and run #4471 reports SUCCESS. Nobody notices until finance closes the books.

Dashboards cannot catch this, because dashboards trust the pipeline's own testimony. So we built a system on one idea: **status and data are two witnesses, and an agent should cross-examine them.** When the pipeline says SUCCESS and the data says corrupt, that contradiction is the alarm. And because the detector and the actuator live in the same Elasticsearch cluster, the agent does not just alert — it acts, reversibly, with a human's stamp.

## What it does

SilentBreak runs a nine-step loop, rendered as a literal polygraph examination in the browser:

1. **Recall** — at run start, the Sentinel agent reads prior incidents back from the `silentbreak-incidents` index: agent memory written to Elasticsearch by previous runs, recalled by this one.
2. **Detect** — Sentinel reads the pipeline's green status doc, then fingerprints the landed data partition with ES|QL stats (null rate, average amount, distinct SKUs), z-scored against a rolling baseline. The demo run breaches at z = 40 while the status doc still says SUCCESS. That contradiction raises the alarm.
3. **Diagnose** — the RootCause agent diffs today's index mapping against yesterday's, names the exact mutation (`- amount` / `+ gross_amount`), and estimates the dollar exposure, always labeled "estimated".
4. **The operator gate** — the run pauses. The UI shows the evidence and the exact action plan, including the trade-off stated in writing: flipping to yesterday serves stale-but-correct data until the repair is validated. Nothing mutates Elasticsearch until a human press-and-holds the APPROVE stamp for 1.2 seconds, which mints a single-use token (TTL 300s). REJECT means zero writes, provably.
5. **Act** — the Guardian agent consumes the token (no token, no writes), reindexes the corrupt rows into a quarantine index, and flips the `revenue_current` alias to the last known good index in one atomic `update_aliases` call. Every downstream consumer heals at once, no code changes anywhere.
6. **Repair** — Guardian then reindexes the quarantined rows into a `-repaired` index via a painless script that renames `gross_amount` back to `amount`. The data is fixed, not just hidden.
7. **Verify** — the downstream revenue query re-runs through the alias, live on a phosphor teletype strip, and shows healthy again.
8. **Record** — the Scribe agent persists a full incident document to Elasticsearch: the memory the next run recalls.
9. **Reverse** — one button flips the alias back. A state change, not a video effect: the proof the remediation was real.

A four-lamp agent rail lights each agent as it works, so the multi-agent workflow is visible rather than asserted.

## How we built it

**The Google side.** The agent pipeline is built on **Google ADK 2.0 (GA May 2026)** — the Agent Development Kit, now part of the **Gemini Enterprise Agent Platform (formerly Vertex AI)**. A **`SequentialAgent`** orchestrates four `LlmAgent`s — Sentinel, RootCause, Guardian, Scribe — with multi-agent orchestration and human-in-the-loop, ADK 2.0's headline feature, landing exactly on our Guardian approval gate. The model is **`gemini-3.5-flash` (GA at Google I/O 2026)**, served on the platform's global endpoint, chosen because it is Google's flagship agentic model and this is wall-to-wall tool calling. Sentinel, RootCause, and Scribe get Elastic's MCP tools via ADK's **`McpToolset`** over streamable HTTP; Guardian gets exactly one FunctionTool, gated on the human-approval token. Credentials work two ways: `GOOGLE_API_KEY`, or Vertex application-default credentials (`GOOGLE_GENAI_USE_VERTEXAI=1`) — the natural fit on Cloud Run.

**The Elastic side.** All agent *reads* (ES|QL stats, mapping diffs, status docs, verification queries) go through MCP into **Elasticsearch 9.4**: in production, the **Elastic Agent Builder MCP endpoint** (`{KIBANA_URL}/api/agent_builder/mcp`), which exposes **ES|QL-backed tools over MCP**; in local dev, the Elastic MCP container in docker-compose. The MCP server exposes no write tools, so writes (quarantine reindex, repair reindex, the atomic alias flip, incident docs) go through elasticsearch-py directly, token-gated — a split we state honestly everywhere. Agents also **write memory back to Elasticsearch**: Scribe persists incident documents that Sentinel recalls at the start of the next run.

**The serving layer.** A **FastAPI** app streams typed Server-Sent Events from a per-run EventBus (with buffer replay, so a finished examination is re-watchable) to a vanilla HTML/CSS/JS polygraph UI — no frameworks, zero external requests. The hosted demo runs on **Cloud Run in full real mode**: a security-enabled Elasticsearch 9.4.2 and the Elastic MCP server (both Cloud Run services in the same project) back every read and write, and the ADK engine runs live on `gemini-3.5-flash` via Vertex ADC. The server arms the examination world automatically before each run, so every judge click yields a complete, repeatable examination — no seeding, no setup. A second, deterministic engine runs the identical loop through the identical approval gate (also the honest fallback if the daily Gemini budget is spent), and the engine in use is always labeled in the UI, the event stream, and the incident record.

## How it addresses the judging criteria

**Technological Implementation.** This is a real multi-agent system on the required stacks, not a wrapper: ADK 2.x `SequentialAgent` + `McpToolset` + Gemini 3.5 Flash on the Google side; Agent Builder MCP endpoint, ES|QL tools over MCP, agent memory written to and recalled from Elasticsearch on the Elastic side. The MCP client discovers tool schemas at runtime from `tools/list`, resolves argument names against each tool's inputSchema, and normalizes response shapes — so it survives a moving MCP server (deprecated container today, Agent Builder endpoint tomorrow) without code changes. A world-namespaced smoke suite asserts the entire real path end to end against docker and restores all state afterwards; a pytest suite runs in GitHub Actions CI.

**Design.** The UI is a polygraph because that is literally what the agent does: cross-examine a subject whose own testimony says SUCCESS. Ink needles draw live on cream chart paper as the Sentinel sweeps history; the red CONTRADICTION stamp slams over the smug green status tile; the examiner's report types itself; the downstream consumer is a phosphor teletype that heals on camera. The centerpiece is the approval gate: a press-and-hold rubber stamp (1.2s, radial progress ring, keyboard accessible) — because authorizing a production mutation should cost deliberate physical effort, not a reflex click.

**Potential Impact.** Silent data corruption is a universal, expensive, unsolved class of failure — every team with a pipeline has shipped a green run with red data. Gartner puts the cost of poor data quality at an average of $12.9 million per organization per year, and a corrupted-but-green pipeline is exactly how that bill gets run up unnoticed. SilentBreak's pattern generalizes far beyond the demo: cross-examine status against data, propose a reversible remediation, put a human gate with an explicit trade-off statement in front of every write, repair rather than merely hide, and leave an incident memory the next run learns from. Because detector, actuator, and memory are all Elasticsearch, the same loop applies to any indexed dataset.

**Quality of the Idea.** "A polygraph for pipelines" reframes anomaly detection as contradiction detection: the alarm is not "this number is weird," it is "your own status report and your own data disagree." Pairing that with HITL remediation that names its costs out loud (stale-but-correct until the repair validates), mints single-use approval tokens, and proves reversibility on camera is, as far as we know, a genuinely fresh take on agentic data operations.

## Challenges we ran into

- **The MCP server moved under us.** The standalone Elastic MCP container is deprecated (since v0.4.6) in favor of the Agent Builder MCP endpoint, and tool parameter schemas were not stable documentation. We answered with runtime schema discovery, argument-name resolution, and response normalization, locked down by the smoke test — and we support both endpoints through the same two environment variables.
- **HITL that is actually strict.** ADK's built-in confirmation flow had open issues we could not bet a centerpiece on, so we built our own `ApprovalRegistry`: single-use tokens, TTL, both engines through the same gate. A late hardening fix: if the ADK engine fails *after* approval, the run ends with an error instead of falling back and re-running — an operator's approval is never spent twice.
- **Querying a poisoned index.** ES|QL errors on columns that do not exist — which is exactly the condition we detect. The stats path splits defensively: counts and distincts via ES|QL, null rate via an `exists` query, averages only when the mapping confirms the field is present and numeric.
- **Honesty engineering.** Keeping a stdlib-only demo, a deterministic fallback, and a live LLM engine emitting the same labeled event stream took more discipline than any single feature.

## Accomplishments that we're proud of

- A complete, reversible, human-gated remediation loop — recall, detect, diagnose, approve, quarantine, flip, repair, verify, record, reverse — that runs identically in mock and real mode.
- The stamp. People who try the demo remember the stamp.
- An agent memory layer that is just... Elasticsearch: Scribe writes, Sentinel recalls, the report cites prior incidents. No extra infrastructure.
- A smoke suite rigorous enough that we trust it against a live demo stack: fully namespaced, asserts the contradiction, the diagnosis, the gate, the flip, the repair, and the reverse, then restores the world.
- Radical honesty as a feature: the engine badge never lies, the dollar figure is always labeled an estimate, the report records its own author, and the README says exactly what is verified versus claimed.

## What we learned (findings and learnings)

- **Contradiction beats anomaly.** Z-scoring data is table stakes; the signal with teeth is *data versus the system's own status report*. Framing detection as cross-examination changed the architecture: the status doc became a first-class input, not a label.
- **Auditable arithmetic earns approvals.** We deliberately used hand-rolled z-scores instead of Elastic ML anomaly detection, because an operator cannot be asked to approve an opaque ML score — the HITL gate needs arithmetic the human can check. (Honesty note we state in the product too: z = 40 is finite only because we floor sigma at measurement noise, 0.025; the raw value is off the chart, and the floor is regularization.)
- **MCP rewards defensive clients.** Discovering tool schemas at runtime instead of hard-coding them is what let us ride out a deprecated server and an endpoint migration mid-hackathon.
- **Remediation needs a "who fixes the data" answer.** Flipping an alias hides poison; reindexing the quarantine through a rename script actually fixes it. Judges and operators both ask the same question, and "repair" is the answer.
- **State trade-offs belong in the approval prompt.** Putting "downstream serves stale-but-correct data until the repair validates" in writing, in the plan, before the stamp, is what turns a confirm dialog into informed consent.
- **Honest fallbacks make demos antifragile.** Because the deterministic engine emits the same labeled events through the same gate, no missing API key, flaky network, or SDK regression can kill the demo — and nothing on screen is ever mislabeled.

## What's next for SilentBreak

- Elastic ML anomaly detection alongside the auditable z-scores (the z-scores stay; ML adds coverage).
- Elastic Agent Builder Workflows as an alternative actuator.
- Multi-metric incident correlation across days, and notification fan-out (Slack/email) after the operator decision.
- Auth on the web UI (the hosted demo is intentionally open and stateless).
- Prompt tuning for the Scribe's report prose on the live Gemini engine.

## Data sources

Synthetic e-commerce sales data, self-generated by the repo's own seeding scripts: daily `sales-YYYY-MM-DD` partitions (order id, SKU, amount, timestamp) with realistic statistical noise, plus pipeline status documents, rolling baselines, and incident records — all indexed into Elasticsearch. The corrupted partition is generated by `scripts/inject_drift.py`, which faithfully reproduces the silent-failure villain: `amount` renamed to `gross_amount`, status doc still SUCCESS. No external or third-party datasets; nothing sensitive.

## Built with (tag list)

`gemini-3.5-flash` · `google-adk` · `google-cloud-agent-builder` · `vertex-ai` · `cloud-run` · `elasticsearch` · `elastic-agent-builder` · `esql` · `mcp` · `python` · `fastapi` · `server-sent-events` · `docker` · `javascript` · `github-actions`

## Links

- **Hosted URL (judges click this; stays live through July 6):** https://silentbreak-941948267289.us-central1.run.app
- **Demo video (≤3 min, public YouTube/Vimeo):** `TODO — video URL after recording docs/DEMO_SCRIPT.md`
- **Public repo (MIT license at root):** https://github.com/nihalnihalani/silentbreak-elastic
