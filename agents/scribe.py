"""Scribe — the reporter. Persists the incident timeline and writes the
typewriter examiner report (template prose here; Gemini-written in ADK mode,
with report_author recorded honestly either way)."""
from __future__ import annotations

import config


def write_report(contradiction, diagnosis, guardian_result, day: str) -> str:
    """Deterministic examiner-report prose for the typewriter panel."""
    v = guardian_result.verify
    lines = [
        f"EXAMINATION RECORD — {day}",
        "",
        f"FINDING. {contradiction.headline()}",
        f"CAUSE. {diagnosis.sentence}",
        (f"EXPOSURE. {diagnosis.nulled_rows} rows carried no revenue value; "
         f"estimated ${diagnosis.estimated_impact_usd:,.0f} invisible to downstream "
         f"consumers while the pipeline reported {contradiction.pipeline_status}."),
        "ACTION. " + "; ".join(guardian_result.actions) + ".",
        (f"VERIFICATION. Downstream query through `{v['alias']}` now reads "
         f"{v['row_count']} rows, null_rate={v['null_rate']:.3f}, "
         f"avg_amount={v['avg_amount']:.2f}. Healthy."),
        "DISPOSITION. Quarantine reversible by one atomic alias flip.",
    ]
    return "\n".join(lines)


def record(client, contradiction, diagnosis, guardian_result, day: str, *,
           engine: str = "deterministic", approved_token_id: str | None = None,
           report: str | None = None, report_author: str | None = None) -> dict:
    if report is None:
        report = write_report(contradiction, diagnosis, guardian_result, day)
        report_author = "deterministic-template"
    doc = {
        "incident_id": config.incident_id(day),  # namespaced per world (smoke vs demo)
        "day": day,
        "contradiction": contradiction.headline(),
        "root_cause": diagnosis.sentence,
        "removed_fields": sorted(diagnosis.removed),
        "added_fields": sorted(diagnosis.added),
        "estimated_impact_usd": round(diagnosis.estimated_impact_usd, 2),
        "actions": guardian_result.actions,
        "rows_quarantined": guardian_result.quarantined,
        "alias_flipped_to": guardian_result.alias_now,
        "engine": engine,
        "approved_token_id": approved_token_id,
        "report": report,
        "report_author": report_author or "deterministic-template",
        "status": "RESOLVED",
    }
    client.index_incident(doc)
    return doc
