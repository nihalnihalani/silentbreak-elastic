"""RootCause — the diagnoser.

Fires only on a contradiction. Diffs the index mapping (today vs. yesterday) to
name the exact schema mutation, then composes the one-sentence root cause. In real
mode, Gemini 3 turns the structured diff + deltas into prose; the rule-based
version here keeps the demo deterministic.
"""
from dataclasses import dataclass


@dataclass
class Diagnosis:
    removed: set
    added: set
    sentence: str


def diagnose(client, today_index: str, yesterday_index: str, contradiction) -> Diagnosis:
    today = client.get_mappings(today_index)
    yesterday = client.get_mappings(yesterday_index)
    removed = yesterday - today
    added = today - yesterday

    rename = None
    if removed and added:
        rename = (sorted(removed)[0], sorted(added)[0])

    if rename:
        old, new = rename
        sentence = (f"Upstream renamed `{old}`→`{new}`; the loader kept mapping "
                    f"`{old}`, nulling {contradiction.value:.0%} of revenue rows.")
    else:
        sentence = (f"{contradiction.metric} breached baseline (z={contradiction.z:.0f}) "
                    f"with no schema change — investigate the upstream transform.")

    # Estimated dollar impact (illustrative): nulled rows x avg order value.
    return Diagnosis(removed=removed, added=added, sentence=sentence)
