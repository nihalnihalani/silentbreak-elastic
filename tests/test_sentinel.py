"""Sentinel pure logic — z-score breach math against a rolling baseline.

Uses a stdlib fake client so the tests cover the behavioral contract
(detect/check_index) without depending on ElasticClient internals.
"""
import config
from agents import sentinel

# Baselines as run_demo builds them, including the documented std floors
# (0.025 / 0.5 / 5) that keep a zero-variance baseline from producing an
# absurd z-score.
HEALTHY_BASELINES = {
    "null_rate": {"mean": 0.002, "std": 0.025},
    "avg_amount": {"mean": 43.0, "std": 0.5},
    "distinct_sku": {"mean": 500.0, "std": 5.0},
}
HEALTHY_STATS = {"null_rate": 0.002, "avg_amount": 43.1, "distinct_sku": 499}
CORRUPT_STATS = {"null_rate": 1.0, "avg_amount": 0.0, "distinct_sku": 500}


class FakeClient:
    def __init__(self, stats, baselines=HEALTHY_BASELINES, status=None):
        self._stats = dict(stats)
        self._baselines = baselines
        self._status = status

    def esql_stats(self, index):
        return dict(self._stats)

    def get_baseline(self, metric):
        return dict(self._baselines[metric])

    def get_status(self, day):
        return self._status


def test_z_math_matches_formula():
    client = FakeClient(CORRUPT_STATS)
    checks = {c.metric: c for c in sentinel.check_index(client, "sales-x")}
    c = checks["null_rate"]
    expected_z = abs(1.0 - 0.002) / 0.025
    assert abs(c.z - expected_z) < 1e-9
    assert c.breach is (expected_z >= config.Z_THRESHOLD)
    assert c.breach is True


def test_breach_with_std_floor_is_finite_and_large():
    # The std floor guarantees the z is huge-but-finite when revenue nulls out.
    client = FakeClient(CORRUPT_STATS)
    checks = {c.metric: c for c in sentinel.check_index(client, "sales-x")}
    z = checks["avg_amount"].z
    assert z == abs(0.0 - 43.0) / 0.5
    assert z >= config.Z_THRESHOLD
    assert z != float("inf")


def test_no_breach_on_healthy_stats():
    client = FakeClient(HEALTHY_STATS)
    assert all(not c.breach for c in sentinel.check_index(client, "sales-x"))
    assert sentinel.detect(client, "sales-x", "SUCCESS") is None


def test_detect_returns_contradiction_on_corrupt():
    client = FakeClient(CORRUPT_STATS)
    contradiction = sentinel.detect(client, "sales-x", "SUCCESS")
    assert contradiction is not None
    assert contradiction.pipeline_status == "SUCCESS"
    assert contradiction.z >= config.Z_THRESHOLD
    assert "CONTRADICTION" in contradiction.headline()


def test_check_as_event_is_additive_tolerant():
    # The event payload must carry at least these keys; extra keys are fine.
    client = FakeClient(CORRUPT_STATS)
    event = sentinel.check_index(client, "sales-x")[0].as_event()
    assert {"metric", "value", "baseline_mean", "baseline_std", "z", "breach"} <= set(event)
