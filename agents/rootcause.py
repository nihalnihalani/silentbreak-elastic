"""RootCause — the diagnoser.

Fires only on a contradiction. Diffs the index mapping (today vs. yesterday) to
name the exact schema mutation, then composes the one-sentence root cause and
an estimated dollar impact (nulled rows x baseline average order value, always
labeled "estimated"). In ADK mode Gemini writes the prose from the same
structured diff; this rule-based version keeps the fallback deterministic.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Diagnosis:
    removed: set = field(default_factory=set)
    added: set = field(default_factory=set)
    sentence: str = ""
    nulled_rows: int = 0
    estimated_impact_usd: float = 0.0

    def as_event(self) -> dict:
        return {
            "sentence": self.sentence,
            "removed_fields": sorted(self.removed),
            "added_fields": sorted(self.added),
            "estimated_impact_usd": round(self.estimated_impact_usd, 2),
            "nulled_rows": self.nulled_rows,
        }


def diagnose(client, today_index: str, yesterday_index: str, contradiction) -> Diagnosis:
    today = client.get_mappings(today_index)
    yesterday = client.get_mappings(yesterday_index)
    removed = yesterday - today
    added = today - yesterday

    rename = None
    if removed and added:
        rename = (sorted(removed)[0], sorted(added)[0])

    stats = client.esql_stats(today_index)
    nulled_rows = round(stats["null_rate"] * stats["row_count"])
    baseline_avg = client.get_baseline("avg_amount")["mean"]
    impact = nulled_rows * baseline_avg

    if rename:
        old, new = rename
        sentence = (f"Upstream renamed `{old}`→`{new}`; the loader kept mapping "
                    f"`{old}`, nulling {contradiction.value:.0%} of revenue rows "
                    f"(estimated ${impact:,.0f} of revenue invisible downstream).")
    else:
        sentence = (f"{contradiction.metric} breached baseline (z={contradiction.z:.0f}) "
                    f"with no schema change — investigate the upstream transform.")

    return Diagnosis(removed=removed, added=added, sentence=sentence,
                     nulled_rows=nulled_rows, estimated_impact_usd=impact)
