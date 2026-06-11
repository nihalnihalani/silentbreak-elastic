# Real-mode targets auto-use .venv/bin/python when it exists (`make venv` creates it).
# Override with: make <target> PYTHON=/path/to/python
PYTHON ?= $(shell [ -x .venv/bin/python ] && echo .venv/bin/python || echo python3)

.PHONY: demo venv check-python install web up down seed inject reset smoke deploy-cloud-run

demo:            ## Full detect->diagnose->quarantine loop in-memory (stdlib only, no setup)
	$(PYTHON) scripts/run_demo.py

venv:            ## One-time: create .venv and install real-mode deps (needs Python 3.11+)
	@PY=""; for cand in python3.12 python3.11 python3.13 python3; do \
		if command -v $$cand >/dev/null 2>&1 && \
		   $$cand -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then \
			PY=$$cand; break; \
		fi; \
	done; \
	[ -n "$$PY" ] || { echo "ERROR: no Python 3.11+ found (tried python3.12, python3.11, python3.13, python3; python3 is $$(python3 -V 2>&1))."; \
		echo "Fix: install a newer Python (e.g. \`brew install python@3.12\`) and re-run \`make venv\`."; exit 1; }; \
	echo "[venv] using $$PY ($$($$PY -V 2>&1))"; \
	$$PY -m venv .venv
	.venv/bin/python -m pip install -r requirements.txt

check-python:    ## Guard: real mode needs Python 3.11+ (stock macOS python3 is 3.9)
	@$(PYTHON) -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' || \
		{ echo "ERROR: $(PYTHON) is too old; real mode needs Python 3.11+."; \
		  echo "Fix: run \`make venv\` with a 3.11+ python3, then re-run this target."; exit 1; }

install: check-python  ## Install deps for real mode + the web UI
	$(PYTHON) -m pip install -r requirements.txt

web: check-python      ## Serve the Polygraph UI + API (port 8000; MCP owns 8080)
	$(PYTHON) -m uvicorn app.main:app --port 8000

up:              ## Start Elasticsearch 9.x + the official Elastic MCP server
	docker compose up -d

down:            ## Stop and remove the local stack
	docker compose down

seed: check-python     ## Seed 14 days of healthy sales partitions + baselines into real ES
	SILENTBREAK_MODE=real $(PYTHON) scripts/seed_baseline.py

inject: check-python   ## Land today's silently-corrupted partition (the demo villain)
	SILENTBREAK_MODE=real $(PYTHON) scripts/inject_drift.py

reset: check-python    ## Delete every SilentBreak index from real ES (clean slate)
	SILENTBREAK_MODE=real $(PYTHON) scripts/reset_world.py

smoke: check-python    ## End-to-end real-mode test: seed->inject->detect->flip->verify->reset
	PYTHON=$(PYTHON) bash scripts/smoke.sh

deploy-cloud-run: ## Build + deploy the hosted (mock-mode) UI to Cloud Run
	gcloud run deploy silentbreak --source . --region us-central1 --allow-unauthenticated \
		--no-cpu-throttling --max-instances 1 --timeout 3600
