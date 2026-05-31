"""Orchestrator — the Agent Builder graph (Sentinel -> RootCause -> Guardian -> Scribe).

In production this is an ADK sequential-with-branch workflow on Agent Builder,
triggered by a pipeline.completed webhook. Here it is a plain function so the loop
is runnable and testable without GCP.
"""
from agents import sentinel, rootcause, guardian, scribe


def run(client, event: dict) -> dict:
    """event = {"day", "today_index", "yesterday_index", "pipeline_status"}"""
    today = event["today_index"]
    yesterday = event["yesterday_index"]
    day = event["day"]

    contradiction = sentinel.detect(client, today, event["pipeline_status"])
    if contradiction is None:
        return {"result": "green", "message": "No contradiction. Pipeline data is healthy."}

    diagnosis = rootcause.diagnose(client, today, yesterday, contradiction)
    result = guardian.act(client, today, yesterday, day)
    incident = scribe.record(client, contradiction, diagnosis, result, day)
    return {
        "result": "remediated",
        "contradiction": contradiction,
        "diagnosis": diagnosis,
        "guardian": result,
        "incident": incident,
    }
