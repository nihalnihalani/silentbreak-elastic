> Historical artifact: this is the internal build spec the team executed during the
> hackathon sprint, kept in docs/ for transparency. `README.md` is the source of truth
> for what the repo actually does today.

# SilentBreak BUILD_SPEC v1 (FINAL — builders execute this verbatim)

Architect-signed spec for the three parallel builders. Deadline today 2:00 PM PT.
If this spec and your instinct disagree, the spec wins. If the spec is silent, pick the
smallest honest thing that keeps `python scripts/run_demo.py` working.

---

## 0. Prime directives (restated, non-negotiable)

1. `python scripts/run_demo.py` must run on the Python stdlib alone, at every commit.
   `config.py`, `agents/*`, and `integrations/elastic_client.py` (mock path) may therefore
   NOT have unconditional third-party imports. Use lazy/guarded imports.
2. MCP is load-bearing: in real mode all READS (ES|QL stats, mappings, status doc, verify
   query) go through the official Elastic MCP server. WRITES (bulk seed, reindex
   quarantine, alias flip, incident doc) go through elasticsearch-py directly, because the
   MCP server exposes no write tools. This split is stated honestly everywhere.
3. Nothing mutates Elastic state without a minted approval token (see §6). Both engines
   (ADK/Gemini and deterministic fallback) pass through the same gate.
4. Model string is `gemini-3.5-flash`. Never reference any Gemini 2.0 model.
5. Nobody touches `README.md` (Harden phase owns it). Nobody touches `LICENSE` except
   INFRA filling the copyright holder (see §10).

---

## 1. Final file tree

```
silentbreak-elastic/
├── config.py                  AGENT   upgraded (env, modes, dotenv-optional)
├── agents/                    AGENT
│   ├── __init__.py                    kept
│   ├── sentinel.py                    upgraded: status-doc read + per-metric checks
│   ├── rootcause.py                   upgraded: mapping diff via client, impact estimate
│   ├── guardian.py                    upgraded: token-gated quarantine_and_flip + reverse
│   ├── scribe.py                      upgraded: incident doc + examiner-report prose
│   ├── orchestrator.py                upgraded: sync run() (run_demo) + async run_async()
│   └── adk_pipeline.py        NEW     ADK 2.x SequentialAgent + McpToolset (lazy import)
├── integrations/              AGENT
│   ├── __init__.py                    kept
│   ├── elastic_client.py              upgraded: mock kept verbatim-in-spirit; real impl
│   ├── mcp_client.py          NEW     streamable-HTTP MCP reader (official `mcp` SDK)
│   └── approval.py            NEW     ApprovalRegistry (tokens, asyncio.Event, TTL)
├── app/                       AGENT   RENAMED from webhook/ (webhook/ deleted)
│   ├── __init__.py            NEW
│   ├── events.py              NEW     EventBus + event envelope + SSE serialization
│   └── main.py                NEW     FastAPI: UI mount, /api/*, SSE
├── ui/                        UI      all-new, vanilla HTML/CSS/JS, no build step
│   ├── index.html
│   ├── styles.css
│   ├── app.js
│   └── assets/                        (optional: inline SVG preferred over files)
├── scripts/                   INFRA
│   ├── run_demo.py                    kept working; minimal edits allowed only to track
│   │                                  renamed client methods (§4) — stdlib only, forever
│   ├── seed_baseline.py               real impl (direct ES client)
│   ├── inject_drift.py                real impl (direct ES client)
│   ├── smoke_real.py          NEW     end-to-end assertion script (§8)
│   └── smoke.sh               NEW     orchestrates the smoke run (§8)
├── docker/                    INFRA
│   └── Dockerfile             NEW     Cloud Run image
├── docker-compose.yml         INFRA   NEW  ES 9.4.2 + elastic-mcp (http)
├── Makefile                   INFRA   upgraded targets (§9)
├── requirements.txt           INFRA   exact pins in §9
├── .env.example               INFRA   exact contents in §9
├── .gitignore                 INFRA   add __pycache__/, .env, *.pyc, sessions.db
├── LICENSE                    INFRA   fill copyright holder only (§10)
└── README.md                  NOBODY  (Harden phase)
```

Deleted: `webhook/` (after `app/` lands), all `__pycache__/` directories (and gitignored).

---

## 2. Directory ownership (hard boundaries)

| Builder | Owns (exclusive write access) |
|---|---|
| Builder-INFRA | `docker/`, `docker-compose.yml`, `scripts/`, `.env.example`, `Makefile`, `requirements.txt`, `.gitignore`, `LICENSE` (holder line only) |
| Builder-AGENT | `agents/`, `integrations/`, `app/`, `config.py` |
| Builder-UI | `ui/` only |
| Harden phase | `README.md`, final commit |

Cross-boundary needs are satisfied by the interfaces in §§3–7, not by editing the other
builder's files. `scripts/run_demo.py` is INFRA-owned but its behavior contract belongs to
this spec: output stays semantically identical to today (baseline → corrupt landing →
detect → diagnose → approval line → quarantine+flip → incident → flip back).

---

## 3. Run modes (three, exactly)

### Mode A: mock (zero setup — judges' fallback, hosted default)
- `SILENTBREAK_MODE=mock` (default).
- `python scripts/run_demo.py` — stdlib-only CLI loop, as today.
- `uvicorn app.main:app` — full Polygraph UI against the in-memory `ElasticClient`.
  `POST /api/run` seeds the 30-day baseline in-memory (same generator logic as
  run_demo), lands the corrupted partition, then drives the deterministic engine with
  `asyncio.sleep` pacing (0.4–0.8s between sentinel checks) so the strip chart draws
  live. Approval gate is fully real (UI stamp → token → flip of in-memory alias).
- Engine: deterministic always (no network, no keys). SSE `engine` event labels it.

### Mode B: local-real (docker-compose ES + official Elastic MCP server)
- `SILENTBREAK_MODE=real`. `docker compose up -d` brings up:
  - `elasticsearch`: `docker.elastic.co/elasticsearch/elasticsearch:9.4.2`, single node,
    `xpack.security.enabled=false`, port 9200, healthcheck per research brief.
  - `elastic-mcp`: `docker.elastic.co/mcp/elasticsearch`, command `http`, port 8080,
    `ES_URL=http://elasticsearch:9200`, plus dummy `ES_USERNAME=elastic` /
    `ES_PASSWORD=changeme` (resolves the unverified no-auth-env behavior: security-off ES
    ignores creds, and the vars satisfy any server-side requiredness). `depends_on`
    elasticsearch healthy. Health: `GET http://localhost:8080/ping` → `pong`.
- Reads go through MCP at `MCP_URL=http://localhost:8080/mcp`
  (tools: `esql`, `get_mappings`, `search`, `list_indices`). Writes via
  elasticsearch-py 9.x against `ES_URL`.
- With `GOOGLE_API_KEY` set: ADK pipeline (§5) — Gemini 3.5 Flash reasons over MCP tool
  results. Without it: deterministic engine, same loop, same MCP reads, SSE-labeled
  `{"engine": "deterministic", "reason": "no GOOGLE_API_KEY"}`.

### Mode C: cloud (Elastic Cloud + Cloud Run)
- Cloud Run image (`docker/Dockerfile`) defaults to `SILENTBREAK_MODE=mock` so the hosted
  judge-testable URL needs zero secrets and is fully interactive (this satisfies the
  Devpost hosted-URL requirement honestly).
- Real-on-cloud is configuration only: set `SILENTBREAK_MODE=real`,
  `ES_URL`/`ELASTIC_API_KEY` (or `ELASTIC_CLOUD_ID`), and `MCP_URL` pointing at either a
  reachable standalone MCP server or Elastic Agent Builder's MCP endpoint
  (`{KIBANA_URL}/api/agent_builder/mcp`, `Authorization: ApiKey …` header via
  `MCP_AUTH_HEADER`). stdio-docker MCP is NOT used on Cloud Run (no docker-in-docker).

---

## 4. Shared client + config interfaces (Builder-AGENT implements, INFRA consumes)

### 4.1 `config.py` (full replacement)
```python
MODE: str                  # SILENTBREAK_MODE, "mock" | "real", default "mock"
ES_URL: str                # default "http://localhost:9200"
ELASTIC_CLOUD_ID: str | None
ELASTIC_API_KEY: str | None
MCP_URL: str               # default "http://localhost:8080/mcp"
MCP_AUTH_HEADER: str | None  # e.g. "ApiKey xxx" for Agent Builder endpoint
GOOGLE_API_KEY: str | None # reads GOOGLE_API_KEY, falls back to GEMINI_API_KEY
GEMINI_MODEL: str          # default "gemini-3.5-flash"
APPROVAL_TTL_SECONDS: int  # default 300
REVENUE_FIELD = "amount"   # kept
ALIAS = "revenue_current"  # kept
Z_THRESHOLD = 5.0          # kept
STATUS_INDEX = "silentbreak-status"      # NEW: pipeline status docs (the "green" lie)
BASELINE_INDEX = "silentbreak-baselines" # kept
INCIDENT_INDEX = "silentbreak-incidents" # kept
partition_index(day) / quarantine_index(day)  # kept
```
dotenv loading: `try: from dotenv import load_dotenv; load_dotenv()` guarded by
`except ImportError: pass` — stdlib mock path must not require python-dotenv.
If `GOOGLE_API_KEY` path is used, also set `GOOGLE_GENAI_USE_VERTEXAI=FALSE` in env
inside `adk_pipeline.py` (not config) unless already set.

### 4.2 `integrations/elastic_client.py` — `ElasticClient`
Mock behavior stays as-is. Real mode constructs lazily:
`from elasticsearch import Elasticsearch` inside `_connect_real()` only.
Reads delegate to `McpReader` (`integrations/mcp_client.py`) when MCP is reachable;
constructor takes `prefer_mcp: bool = True`. If MCP is unreachable in real mode the
client raises a clear error (MCP is load-bearing; smoke verifies it) — except in
`scripts/seed_baseline.py`/`inject_drift.py`, which construct with `prefer_mcp=False`
(pure write-side setup tools).

Method contract (BREAKING renames marked; INFRA updates `run_demo.py` accordingly):
```python
index_rows(index, rows)                      # mock seed / real: helpers.bulk + refresh
set_alias(alias, index)                      # real: atomic remove-all+add via update_aliases
alias_target(alias) -> str | None            # real: GET alias, first index
put_baseline(metric, mean, std)              # real: doc into silentbreak-baselines
get_baseline(metric) -> {"mean","std"}       # real: read via MCP `search`
index_status(doc)                            # NEW {"day","run","status":"SUCCESS"}
get_status(day) -> dict                      # NEW read via MCP `search` on STATUS_INDEX
esql_stats(index) -> {"row_count","null_rate","distinct_sku","avg_amount"}
get_mappings(index) -> set[str]              # field names; real: MCP get_mappings
quarantine_missing_field(src, q_index, field) -> int   # RENAMED from quarantine_bad_rows
                                             # real: _reindex with bool must_not exists;
                                             # mock: move rows where field is None/missing
update_alias(alias, target) -> str | None    # returns previous target; real: ONE atomic
                                             # update_aliases call (remove prev + add new)
index_incident(doc)                          # real: index into silentbreak-incidents
refresh(index)                               # real: indices.refresh; mock: no-op
```
`trigger_resync` is DELETED everywhere (it was fake in real mode; honesty rule). The
Guardian step formerly called "resync" becomes "verify downstream" (§5 loop step 6).

Real `esql_stats` must survive the poisoned index where `amount` does not exist
(ES|QL errors on unknown columns):
- `row_count`, `distinct_sku` via ES|QL: `FROM {index} | STATS row_count=COUNT(*), distinct_sku=COUNT_DISTINCT(sku)`
- `null_rate` via MCP `search` with `{"query":{"bool":{"must_not":{"exists":{"field":"amount"}}}},"size":0,"track_total_hits":true}` divided by row_count (missing field counts as null — matches the mock semantics)
- `avg_amount` via ES|QL `STATS AVG(amount)` ONLY if `get_mappings` shows `amount`
  present and numeric; else `0.0`.

### 4.3 `integrations/mcp_client.py` — `McpReader`
Wraps the official `mcp` Python SDK (already a transitive dep of google-adk) streamable
HTTP client against `config.MCP_URL` (+ optional `MCP_AUTH_HEADER`). Exposes sync-facing
helpers (run the async SDK via `asyncio.run` / a private loop):
```python
ping() -> bool                       # GET {base}/ping == "pong" (plain HTTP)
list_tools() -> list[str]
esql(query: str) -> dict             # {"columns":[...], "values":[...]}
get_mappings(index: str) -> dict
search(index: str, body: dict) -> dict
```
Tool parameter names are unverified upstream; resolve at runtime: on first use, read the
tool's inputSchema from `tools/list` and map our args to the schema's property names
(expected `index`, `query`; fall back gracefully). Smoke test (§8) locks this down.

### 4.4 `integrations/approval.py` — `ApprovalRegistry`
```python
request(run_id, plan: list[str]) -> None      # marks run as awaiting
wait(run_id, timeout: float) -> str | None    # awaits decision; returns token or None(rejected/timeout)
approve(run_id) -> str                        # mints secrets.token_hex(16), TTL APPROVAL_TTL_SECONDS, sets event
reject(run_id) -> None
consume(run_id, token) -> bool                # single-use validation; False on mismatch/expired/reused
state(run_id) -> "idle"|"awaiting"|"approved"|"rejected"
```
Pure stdlib (`asyncio`, `secrets`, `time`). One global instance imported by app + agents.

---

## 5. The agent loop (both engines emit the SAME event sequence)

Canonical loop (deterministic engine = `orchestrator.run_async`; ADK engine mirrors it):
1. `run_started` → land/read context, read pipeline status doc (`get_status`) → `pipeline_status`
2. Sentinel: for each metric in (`null_rate`, `avg_amount`, `distinct_sku`) emit
   `sentinel_check`; on first z ≥ `Z_THRESHOLD` emit `contradiction`
   (if none → `run_complete {result:"green"}`)
3. RootCause: `get_mappings` today vs yesterday → `diagnosis` (incl. estimated dollar
   impact = nulled_rows × baseline avg_amount, labeled "estimated")
4. Emit `awaiting_approval` with the exact action plan; `registry.request`; await decision
   (timeout 300s → `run_complete {result:"vetoed", reason:"timeout"}`)
5. Guardian (only with valid token): `quarantine_missing_field` →
   `guardian_action {step:"quarantine",...}`; `update_alias` to yesterday →
   `guardian_action {step:"alias_flip",...}`
6. Verify: re-run `esql_stats` THROUGH the alias → `verify` + `teletype` lines showing
   downstream revenue query now healthy
7. Scribe: build + persist incident doc → `incident`; emit `run_complete {result:"remediated"}`

`orchestrator.run(client, event, approver=None)` stays sync for `run_demo.py`: same steps,
print-based, `approver` defaults to a function that prints
`[approval ] operator pressed APPROVE (auto-approved in CLI demo)` and returns a minted
token via the registry. The CLI demo's reversibility proof (flip back) stays.

### 5.1 ADK engine — `agents/adk_pipeline.py`
- All ADK/google-genai imports inside a `build_pipeline()` function or guarded at module
  top with try/except so mock mode never needs the package. Import compat shim:
  ```python
  try:
      from google.adk.tools.mcp_tool import McpToolset
  except ImportError:
      from google.adk.tools.mcp_tool import MCPToolset as McpToolset
  ```
- `McpToolset(connection_params=StreamableHTTPConnectionParams(url=config.MCP_URL, headers=...),
  tool_filter=["list_indices","get_mappings","search","esql"])` defined synchronously.
  Streamable HTTP (not stdio-docker) is THE chosen transport: one code path for local
  compose and cloud, no docker-in-docker problem.
- Four `LlmAgent`s (`model=config.GEMINI_MODEL` explicit), `output_key`s:
  `sentinel_findings`, `rootcause_diagnosis`, `guardian_result`, `incident_report`;
  wrapped in `SequentialAgent(name="silentbreak_pipeline", sub_agents=[...])`.
- Guardian's tool is OUR token gate, not ADK confirmation (resolves the flaky
  request_confirmation/resume issue deliberately — decision is final):
  ```python
  async def quarantine_and_flip(day: str, today_index: str, yesterday_index: str) -> dict:
      registry.request(run_id, plan); token = await registry.wait(run_id, 300)
      if not token or not registry.consume(run_id, token): return {"status": "vetoed"}
      # direct-client writes + verify, emitting guardian_action/verify events
  ```
  Exposed as a plain `FunctionTool`. Sentinel/RootCause/Scribe get MCP tools only;
  Guardian gets this function tool only.
- Runner: `Runner` + `InMemorySessionService`, `await session_service.create_session(...)`,
  iterate `runner.run_async(...)`, map ADK events → our SSE envelope (tool calls →
  `teletype` lines like `> esql FROM revenue_current | STATS ...`; agent text →
  the corresponding typed event; unknown → `teletype`).
- Selection logic in `app/main.py`: `engine = "adk" if (config.MODE=="real" and
  config.GOOGLE_API_KEY and adk importable) else "deterministic"`. Any ADK runtime
  exception mid-run → emit `error`, fall back to deterministic engine for that run,
  emit `engine` event explaining why. Never crash the server.

---

## 6. HITL approval contract (the APPROVE stamp)

1. Run reaches step 4 → server emits SSE `awaiting_approval`
   `{action:"quarantine", plan:[…strings], index, target_index, estimated_impact_usd}`.
2. UI renders the press-and-hold stamp. Hold ≥ 1200 ms → on release POST
   `/api/approve {"run_id": "..."}`.
3. Server: `registry.approve(run_id)` mints a single-use token (TTL 300s), emits SSE
   `approved {token_id: token[:8]}` (never the full token), the paused coroutine resumes.
4. Guardian tool calls `registry.consume(run_id, token)`; only on True does any Elastic
   write happen. Invalid/expired/reused token → no mutation, `run_complete {result:"vetoed"}`.
5. REJECT button (no hold) → POST `/api/reject` → `rejected` event →
   `run_complete {result:"vetoed", reason:"operator_rejected"}`. Nothing was written.
6. REVERSE: after a remediated run, POST `/api/reverse {"run_id"}` flips the alias back to
   the (still-quarantine-cleaned) today index in one atomic `update_aliases`, emits
   `reversed {alias, now}` on the same stream. 409 if there is nothing to reverse.
   Idempotent: a second call 409s.

---

## 7. FastAPI app contract (`app/main.py`) — Builder-AGENT implements, Builder-UI consumes

Static: `ui/` mounted at `/` (`html=True`); `GET /` serves `ui/index.html`.

| Method/Path | Request | Response |
|---|---|---|
| GET `/healthz` | — | `{"ok": true, "mode": "...", "engine": "..."}` |
| GET `/api/state` | — | `{"mode","engine","phase","alias","alias_target","active_run_id","last_incident"}` |
| POST `/api/run` | `{}` (scenario fixed: "drift") | 202 `{"run_id","mode","engine"}`; 409 `{"error":"run_active","run_id"}` |
| GET `/api/events` | query `run_id` (optional; default latest) | SSE stream (below) |
| POST `/api/approve` | `{"run_id"}` | 200 `{"status":"approved","token_id"}`; 404; 409 `{"error":"not_awaiting"}` |
| POST `/api/reject` | `{"run_id"}` | 200 `{"status":"rejected"}`; 404; 409 |
| POST `/api/reverse` | `{"run_id"}` | 200 `{"status":"reversed","alias","now"}`; 409 `{"error":"nothing_to_reverse"}` |
| GET `/api/incidents` | — | `{"incidents":[doc,...]}` |
| POST `/pipeline-completed` | kept (compat): same body as today | 202 `{"run_id"}` (starts a run with given indices) |

`phase` ∈ `idle | landing | detect | diagnose | awaiting_approval | quarantine | verify |
resolved | vetoed | green`.

### SSE wire format
`EventBus` per run buffers all events; `GET /api/events` replays the buffer then streams
live (UI may connect after POST /api/run). Heartbeat comment line `: hb` every 15s.
```
event: <type>
data: {"seq": <int>, "ts": "<ISO8601>", "type": "<type>", "data": {...}}
```
Event types and `data` payloads (the complete set — UI builds to exactly this):
```
run_started        {run_id, mode, engine, day, alias, alias_target}
engine             {name: "adk"|"deterministic", model?: "gemini-3.5-flash", reason?}
pipeline_status    {status: "SUCCESS", run_number: "#4471", day}
sentinel_check     {metric, value, baseline_mean, baseline_std, z, breach: bool}
contradiction      {headline, metric, value, baseline_mean, z, pipeline_status}
diagnosis          {sentence, removed_fields:[...], added_fields:[...],
                    estimated_impact_usd, nulled_rows}
awaiting_approval  {action:"quarantine", plan:[str,...], index, target_index,
                    estimated_impact_usd}
approved           {token_id}
rejected           {reason}
guardian_action    {step:"quarantine"|"alias_flip", detail, rows?, alias?, from?, to?}
verify             {alias, target, row_count, null_rate, avg_amount}
teletype           {line}            # phosphor strip: raw queries + downstream readouts
incident           {doc}             # the Scribe document
reversed           {alias, now}
run_complete       {result:"remediated"|"green"|"vetoed", reason?, duration_ms}
error              {message}
```
Incident doc shape (Scribe): as current `scribe.py` plus `estimated_impact_usd`,
`engine`, `approved_token_id`, `report` (typewriter prose; Gemini-written in ADK mode,
template-written in deterministic mode — field `report_author: "gemini-3.5-flash" |
"deterministic-template"` for honesty).

---

## 8. Smoke test contract (Builder-INFRA) — `scripts/smoke.sh`

`bash scripts/smoke.sh` from repo root; `set -euo pipefail`; exit 0 = pass. Steps:
1. `docker compose up -d`; poll `http://localhost:9200/_cluster/health` until
   yellow/green (max 120s) and `http://localhost:8080/ping` == `pong` (max 60s).
2. `python scripts/smoke_real.py` (env `SILENTBREAK_MODE=real`), which asserts in order:
   a. `McpReader.list_tools()` contains `esql` and `get_mappings` (locks unverified
      schemas; print the discovered inputSchema property names).
   b. Seed: 30 healthy `sales-smoke-*` days + baselines + status doc + alias →
      yesterday (calls the same functions `seed_baseline.py` exposes; scripts must be
      importable: `def seed(client, days, prefix)` etc.).
   c. Inject: corrupted today partition (`gross_amount`, no `amount`), alias → today,
      status doc SUCCESS (same function `inject_drift.py` exposes).
   d. Detect+diagnose+act: run `orchestrator.run(client, event, approver=auto_approver)`
      (deterministic; auto-approver mints a real token through ApprovalRegistry and
      prints that it did — same gate, test-labeled).
   e. Assert: contradiction metric == `null_rate` with z ≥ Z_THRESHOLD; diagnosis
      mentions `gross_amount`; alias target == yesterday; verify stats through alias
      show null_rate < 0.01; incident doc exists in `silentbreak-incidents`.
   f. Reverse: flip alias back; assert target == today; assert quarantine index
      row count > 0.
   g. Cleanup: delete all `sales-smoke-*`, quarantine, baseline/status/incident smoke
      docs. Print `SMOKE PASS`.
3. smoke.sh prints `SMOKE PASS` and exits 0; any failure exits non-zero with the step name.

Also: `make demo` must still print the full mock loop and exit 0 (CI sanity for rule 2).
Smoke uses index prefix `sales-smoke-` (config override via env `SILENTBREAK_INDEX_PREFIX`
read in `partition_index`) so it never collides with demo data. AGENT implements the
prefix hook in config.py.

---

## 9. INFRA exact contents

### requirements.txt
```
# Mock demo needs nothing: `python scripts/run_demo.py` is stdlib-only.
fastapi>=0.115
uvicorn>=0.30
elasticsearch>=9.0,<10
google-adk>=2.2.0,<3
google-genai>=2.0
python-dotenv>=1.0
mcp>=1.0
```

### .env.example
```
# --- mode ---
SILENTBREAK_MODE=mock            # mock | real
# --- Gemini (optional; deterministic fallback used when absent) ---
GOOGLE_API_KEY=                  # or GEMINI_API_KEY
GEMINI_MODEL=gemini-3.5-flash
GOOGLE_GENAI_USE_VERTEXAI=FALSE
# --- Elastic (real mode) ---
ES_URL=http://localhost:9200
ELASTIC_CLOUD_ID=
ELASTIC_API_KEY=
# --- Elastic MCP server (reads go through this) ---
MCP_URL=http://localhost:8080/mcp
MCP_AUTH_HEADER=                 # "ApiKey ..." when using Agent Builder MCP endpoint
```

### docker-compose.yml
Per §3 Mode B (use the research-brief YAML verbatim, image 9.4.2, plus the dummy
ES_USERNAME/ES_PASSWORD on elastic-mcp).

### docker/Dockerfile
`FROM python:3.12-slim` → copy repo → `pip install -r requirements.txt` →
`ENV SILENTBREAK_MODE=mock` → `CMD ["sh","-c","uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080}"]`.

### Makefile targets
`demo` (unchanged), `install`, `web` (uvicorn app.main:app --port 8000 — note 8000, MCP
owns 8080), `up` (docker compose up -d), `down`, `seed`, `inject`, `smoke`
(bash scripts/smoke.sh), `deploy-cloud-run` (gcloud one-liner, documented not required).

---

## 10. UI spec (Builder-UI) — the POLYGRAPH

Single page, vanilla JS (EventSource + fetch), zero external requests (no CDN fonts —
font stack: `"Courier Prime", "Courier New", monospace` for the report; a humanist mono
or system stack for chrome). Must render perfectly from `file://`-style static serving.

Palette (exact): paper `#F5F0E6`, ink `#14120F`, faint rule lines `#D8CFBC`, stamp red
`#A4282A`, phosphor green `#33FF66` on near-black `#0A0F0A`, status-lie green `#2E7D32`.
NO purple, NO gradients-as-decoration, NO nested cards, NO generic dashboard chrome.

Layout (top to bottom):
1. **Header strip**: "SILENTBREAK — POLYGRAPH EXAMINATION" + mode/engine badge (from
   `/api/state` + `engine` event; deterministic mode badge reads "DETERMINISTIC FALLBACK
   (no Gemini key)") + the green pipeline-status tile (`pipeline_status` event:
   "run #4471 SUCCESS ✓") that gets a red CONTRADICTION overlay stamp on `contradiction`.
2. **Strip chart** (the centerpiece): cream chart-paper `<canvas>` with faint horizontal
   rule lines; three ink needle traces (null_rate, avg_amount, distinct_sku) drawing
   left-to-right as `sentinel_check` events arrive; baseline band shaded; on `breach:true`
   the needle spikes violently and a red vertical line + label `z=NN` is inked.
3. **Examiner report**: typewriter panel; types out `diagnosis.sentence` character-by-
   character (~18ms/char), then the field diff as `- amount` / `+ gross_amount` lines,
   then estimated impact. On `incident`, types the full report.
4. **HITL gate**: appears on `awaiting_approval`: the action plan as a numbered evidence
   list, then the press-and-hold APPROVE stamp (circular, stamp-red, 1200ms hold with
   radial progress ring; on release fires `/api/approve`; lands rotated ~-8° with a
   stamped "QUARANTINE APPROVED" impression + slight ink texture) and a small flat
   REJECT button. Disable both after decision. Keyboard accessible (hold Space/Enter).
5. **Teletype strip**: full-width phosphor terminal (green on near-black, subtle
   scanline), streaming `teletype` lines + a recurring downstream readout line after
   `verify` (e.g. `> FROM revenue_current | STATS …  null_rate=0.002  OK`).
6. **Footer controls**: RUN EXAMINATION button (`POST /api/run`, disabled while active),
   REVERSE lever/button (enabled after `run_complete result=remediated`; fires
   `/api/reverse`; on `reversed` the teletype shows the alias target change and the
   header tile annotates "alias restored — reversibility proven").

Behavior: on load, `GET /api/state`; if `active_run_id`, attach EventSource to it
(buffer replay makes reconnect safe). All renders driven solely by the §7 SSE schema.
Empty/error states: `error` event prints in the teletype in red; never blank-screens.

---

## 11. Resolution of every UNVERIFIED research item (final decisions)

| Item | Decision |
|---|---|
| MCP server npx path | Not used. Docker image only. |
| MCP tool param schemas | Discovered at runtime from `tools/list` inputSchema (McpReader §4.3); locked by smoke step 2a. |
| MCP server with no auth vars vs security-off ES | Always pass dummy `ES_USERNAME=elastic`/`ES_PASSWORD=changeme` in compose. |
| Agent Builder MCP endpoint tool names | Not depended on. Supported via `MCP_URL`+`MCP_AUTH_HEADER` config only; documented as cloud option. |
| `McpToolset` vs `MCPToolset` spelling | try/except import shim (§5.1). |
| `ToolContext`/`request_confirmation` flow (flaky issues #3567/#3018) | NOT used. Our ApprovalRegistry token gate replaces it for both engines. |
| ADK graph runtime class names | Not used. `SequentialAgent` only. |
| `Runner`/`InMemorySessionService` import paths on 2.2.0 | Use 1.x paths (restored in 2.2.0); guarded import + runtime fallback to deterministic engine on any ImportError. |
| `AVG(revenue)` erroring on poisoned mapping | Defensive stats split (§4.2): ES|QL for count/distinct, `exists` search for null_rate, AVG only when mapping confirms numeric. |
| stdio-docker MCP on Cloud Run | Streamable HTTP transport everywhere; stdio never required. |

---

## 12. Builder acceptance checklists

**INFRA done when:** `make demo` exits 0 on stdlib python; `docker compose up -d` →
both healthchecks pass; `bash scripts/smoke.sh` prints SMOKE PASS exit 0; `.env.example`
matches §9; Dockerfile builds and serves mock UI on `$PORT`; LICENSE holder filled
("Nihal Nihalani").

**AGENT done when:** `python scripts/run_demo.py` still exits 0 unmodified-in-behavior;
`SILENTBREAK_MODE=mock uvicorn app.main:app` serves the API per §7 (curl-able:
run → events stream → approve → remediated → reverse); real mode passes smoke;
`python -c "import agents.adk_pipeline"` succeeds without google-adk installed
(lazy imports); engine fallback labeled in SSE.

**UI done when:** full mock run renders end-to-end in a browser: needles draw, red
contradiction stamp, typewriter report, press-and-hold stamp drives a real `/api/approve`,
teletype streams, REVERSE works; zero console errors; zero external network requests;
works at 1280×800 (demo recording size).
