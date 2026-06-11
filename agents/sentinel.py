"""Sentinel — the detector.

Reads the pipeline's own status doc (the "green" lie), fingerprints the landed
partition, and z-scores each metric against the rolling baseline. A
CONTRADICTION = the pipeline reported SUCCESS but a metric breaches the
baseline band. The math is a transparent z-score so the logic is auditable;
in ADK mode Gemini drives the same checks through the Elastic MCP tools.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import config

METRICS = ("null_rate", "avg_amount", "distinct_sku")


@dataclass
class Check:
    metric: str
    value: float
    baseline_mean: float
    baseline_std: float
    z: float
    breach: bool

    def as_event(self) -> dict:
        return {
            "metric": self.metric,
            "value": self.value,
            "baseline_mean": self.baseline_mean,
            "baseline_std": self.baseline_std,
            "z": self.z,
            "breach": self.breach,
        }


@dataclass
class Contradiction:
    metric: str
    value: float
    baseline_mean: float
    z: float
    pipeline_status: str

    def headline(self) -> str:
        return (f"CONTRADICTION: status={self.pipeline_status}, data=CORRUPT — "
                f"{self.metric} {self.baseline_mean:.3f}→{self.value:.3f} (z={self.z:.0f})")


def recall_memory(client) -> dict:
    """Memory layer: at run start, recall prior incidents for this pipeline
    from silentbreak-incidents (MCP search w/ es-py fallback in real mode,
    in-memory store in mock). Payload feeds the `memory` SSE event and the
    Scribe's "incident #N+1" reference."""
    try:
        prior = client.recall_incidents()
    except Exception:
        prior = []
    payload: dict = {"prior_count": len(prior), "last_id": None, "last_summary": None}
    if prior:
        last = prior[0]
        what = str(last.get("root_cause") or last.get("contradiction") or "incident")
        if len(what) > 120:
            what = what[:117] + "..."
        payload["last_id"] = last.get("incident_id")
        payload["last_summary"] = (
            f"{last.get('day', '?')}: {what} — action: quarantined "
            f"{last.get('rows_quarantined', '?')} rows, alias -> "
            f"{last.get('alias_flipped_to', '?')}")
    return payload


def read_status(client, day: str, fallback: str = "UNKNOWN") -> dict:
    """The status doc is the lie this whole project exists to catch."""
    doc = client.get_status(day)
    if doc:
        return doc
    return {"day": day, "run": "#4471", "status": fallback}


def check_index(client, index: str) -> list[Check]:
    """One fingerprint pass: z-score every watched metric against its baseline."""
    stats = client.esql_stats(index)
    checks: list[Check] = []
    for metric in METRICS:
        base = client.get_baseline(metric)
        z = abs(stats[metric] - base["mean"]) / base["std"]
        checks.append(Check(
            metric=metric, value=float(stats[metric]),
            baseline_mean=float(base["mean"]), baseline_std=float(base["std"]),
            z=float(z), breach=z >= config.Z_THRESHOLD,
        ))
    return checks


def detect(client, index: str, pipeline_status: str,
           on_check: Callable[[Check], None] | None = None) -> Contradiction | None:
    """Run the per-metric checks; return the first breach as a Contradiction."""
    for check in check_index(client, index):
        if on_check:
            on_check(check)
        if check.breach:
            return Contradiction(
                metric=check.metric, value=check.value,
                baseline_mean=check.baseline_mean, z=check.z,
                pipeline_status=pipeline_status,
            )
    return None
