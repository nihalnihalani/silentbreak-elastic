PYTHON ?= python3

.PHONY: demo install web up down seed inject reset smoke deploy-cloud-run

demo:            ## Full detect->diagnose->quarantine loop in-memory (stdlib only, no setup)
	$(PYTHON) scripts/run_demo.py

install:         ## Install deps for real mode + the web UI
	$(PYTHON) -m pip install -r requirements.txt

web:             ## Serve the Polygraph UI + API (port 8000; MCP owns 8080)
	$(PYTHON) -m uvicorn app.main:app --port 8000

up:              ## Start Elasticsearch 9.x + the official Elastic MCP server
	docker compose up -d

down:            ## Stop and remove the local stack
	docker compose down

seed:            ## Seed 14 days of healthy sales partitions + baselines into real ES
	SILENTBREAK_MODE=real $(PYTHON) scripts/seed_baseline.py

inject:          ## Land today's silently-corrupted partition (the demo villain)
	SILENTBREAK_MODE=real $(PYTHON) scripts/inject_drift.py

reset:           ## Delete every SilentBreak index from real ES (clean slate)
	SILENTBREAK_MODE=real $(PYTHON) scripts/reset_world.py

smoke:           ## End-to-end real-mode test: seed->inject->detect->flip->verify->reset
	PYTHON=$(PYTHON) bash scripts/smoke.sh

deploy-cloud-run: ## Build + deploy the hosted (mock-mode) UI to Cloud Run
	gcloud run deploy silentbreak --source . --region us-central1 --allow-unauthenticated
