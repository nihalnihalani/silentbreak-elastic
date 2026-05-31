"""Cloud Run webhook — receives pipeline.completed and runs the SilentBreak graph.

    uvicorn webhook.main:app --reload

POST /pipeline-completed
  { "day": "2026-06-01", "today_index": "sales-2026-06-01",
    "yesterday_index": "sales-2026-05-31", "pipeline_status": "SUCCESS" }
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi import FastAPI, Request
from integrations.elastic_client import ElasticClient
from agents import orchestrator

app = FastAPI(title="SilentBreak")
client = ElasticClient()  # MODE from env


@app.get("/healthz")
def healthz():
    return {"ok": True, "mode": client.mode}


@app.post("/pipeline-completed")
async def pipeline_completed(request: Request):
    event = await request.json()
    out = orchestrator.run(client, event)
    # serialize dataclasses for the response
    if out.get("result") == "remediated":
        c, g, inc = out["contradiction"], out["guardian"], out["incident"]
        return {"result": "remediated", "contradiction": c.headline(),
                "root_cause": out["diagnosis"].sentence, "actions": g.actions,
                "incident": inc}
    return out
