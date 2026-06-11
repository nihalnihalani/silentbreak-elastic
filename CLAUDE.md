# CLAUDE.md — SilentBreak

Read VISION.md (intent) and STATE.md (loop spine) before working. Full loop spec: docs/SHIP_LOOP.md.

## Commands
- `make demo` — full loop in-memory, stdlib only, exits 0 (this is the e2e gate)
- `.venv/bin/python -m pytest tests/ -q` — test suite (must be green before any commit)
- `make deploy-cloud-run` — deploy; MUST run gcloud with `--account neal.kakarot@gmail.com`
  and `--project invisible-half-nn-1778532464` (other authed account gets PERMISSION_DENIED)

## Facts that bite
- Health endpoint is `/api/healthz` — Cloud Run GFE reserves `/healthz` on run.app domains.
- Gemini via Vertex ADC needs `GOOGLE_CLOUD_LOCATION=global` (gemini-3.5-flash 404s in us-central1).
- The hosted demo is single-operator: process-global run state, `--max-instances 1`.
- Every `await request.json()` body must be isinstance-checked (dict) and `run_id` must be a str —
  three 500-bug rounds came from exactly this; tests/test_webhook.py guards the class.
- UI posts EMPTY bodies to /api/approve|reject|reverse — non-dict bodies must behave as empty, not 4xx.

## Working rules
- One writer per file at a time; the team lead is the only one who commits/pushes/deploys.
- A fix is not closed until verified on the LIVE deployed revision.
- Push every verified change; never push red; heartbeat-push STATE.md at 20 min.
- Devil's-advocate sign-off = re-attack on the deployed revision, not a diff read.
