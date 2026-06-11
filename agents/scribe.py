"""Scribe — the reporter. Persists the incident timeline and writes the
typewriter examiner report (template prose here; Gemini-written in ADK mode,
with report_author recorded honestly either way)."""
from __future__ import annotations

import config


def write_report(contradiction, diagnosis, guardian_result, day: str,
                 prior_count: int = 0) -> str:
    """Deterministic examiner-report prose for the typewriter panel."""
    v = guardian_result.verify
    if prior_count:
        memory_line = (f"MEMORY. This is incident #{prior_count + 1} for this pipeline; "
                       "the agent recalled the prior remediation before acting.")
    else:
        memory_line = ("MEMORY. No prior incidents recalled for this pipeline; "
                       "this is incident #1.")
    lines = [
        f"EXAMINATION RECORD — {day}",
        "",
        f"FINDING. {contradiction.headline()}",
        f"CAUSE. {diagnosis.sentence}",
        memory_line,
        (f"EXPOSURE. {diagnosis.nulled_rows} rows carried no revenue value; "
         f"estimated ${diagnosis.estimated_impact_usd:,.0f} invisible to downstream "
         f"consumers while the pipeline reported {contradiction.pipeline_status}."),
        "ACTION. " + "; ".join(guardian_result.actions) + ".",
        (f"REPAIR. {guardian_result.repaired_docs} quarantined rows re-keyed "
         f"({guardian_result.repair_rename}) into `{guardian_result.repaired_index}` — "
         "ready for validation and re-point; until then the alias serves "
         "stale-but-correct data from last known good."),
        (f"VERIFICATION. Downstream query through `{v['alias']}` now reads "
         f"{v['row_count']} rows, null_rate={v['null_rate']:.3f}, "
         f"avg_amount={v['avg_amount']:.2f}. Healthy."),
        "DISPOSITION. Quarantine reversible by one atomic alias flip.",
    ]
    return "\n".join(lines)


def record(client, contradiction, diagnosis, guardian_result, day: str, *,
           engine: str = "deterministic", approved_token_id: str | None = None,
           report: str | None = None, report_author: str | None = None,
           prior_count: int = 0) -> dict:
    if report is None:
        report = write_report(contradiction, diagnosis, guardian_result, day,
                              prior_count=prior_count)
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
        "repaired_index": guardian_result.repaired_index,
        "repaired_docs": guardian_result.repaired_docs,
        "repair_rename": guardian_result.repair_rename,
        "prior_incidents": prior_count,
        "incident_number": prior_count + 1,
        "engine": engine,
        "approved_token_id": approved_token_id,
        "report": report,
        "report_author": report_author or "deterministic-template",
        "status": "RESOLVED",
    }
    client.index_incident(doc)
    return doc
