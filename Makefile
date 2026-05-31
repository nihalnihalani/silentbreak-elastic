.PHONY: demo install web

demo:        ## Run the full detect->diagnose->quarantine loop (stdlib only, no setup)
	python scripts/run_demo.py

install:     ## Install deps for real mode
	pip install -r requirements.txt

web:         ## Run the Cloud Run webhook locally
	uvicorn webhook.main:app --reload --port 8080
