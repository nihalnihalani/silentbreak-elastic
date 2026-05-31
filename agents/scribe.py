"""Scribe — the reporter. Persists the incident timeline."""
import config


def record(client, contradiction, diagnosis, guardian_result, day: str) -> dict:
    doc = {
        "incident_id": f"SB-{day}",
        "day": day,
        "contradiction": contradiction.headline(),
        "root_cause": diagnosis.sentence,
        "removed_fields": sorted(diagnosis.removed),
        "added_fields": sorted(diagnosis.added),
        "actions": guardian_result.actions,
        "rows_quarantined": guardian_result.quarantined,
        "alias_flipped_to": guardian_result.alias_now,
        "status": "RESOLVED",
    }
    client.index_incident(doc)
    return doc
