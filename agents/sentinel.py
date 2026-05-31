"""Sentinel — the detector.

Computes the landed partition's fingerprint and z-scores it against the rolling
baseline. A CONTRADICTION = the pipeline reported SUCCESS but a metric breaches
the baseline band. In real mode the check selection is Gemini-driven; here it is
a transparent z-score so the logic is auditable.
"""
from dataclasses import dataclass
import config


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


def detect(client, index: str, pipeline_status: str) -> Contradiction | None:
    stats = client.esql_stats(index)
    # The headline signal: revenue null-rate spiking is a silent corruption.
    for metric in ("null_rate", "avg_amount", "distinct_sku"):
        base = client.get_baseline(metric)
        z = abs(stats[metric] - base["mean"]) / base["std"]
        if z >= config.Z_THRESHOLD:
            return Contradiction(
                metric=metric, value=stats[metric],
                baseline_mean=base["mean"], z=z, pipeline_status=pipeline_status,
            )
    return None
