"""Server-side auto-arm decision logic.

A judge clicking RUN EXAMINATION must always get the full examination, so real
mode reseeds the poisoned-demo world unless it is already armed for today. The
decision must reuse the world when it IS armed (and never wipe incident memory).
These tests exercise `is_armed`'s decision against a minimal fake ES; the actual
seed/inject writes are covered by scripts/smoke_real.py against a live cluster.
"""
import datetime as dt
import os

os.environ.setdefault("SILENTBREAK_MODE", "mock")

import config  # noqa: E402
from app import arming  # noqa: E402


def _today():
    return dt.date.today().isoformat()


def _today_index():
    return f"{config.index_prefix()}{_today()}"


class _Indices:
    def __init__(self, fake):
        self.f = fake

    def get_alias(self, name):
        if self.f.alias is None:
            raise RuntimeError("missing")  # behaves like ignore_status returns error key
        return {self.f.alias: {"aliases": {name: {}}}}

    def exists(self, index):
        return index in self.f.mappings

    def get_mapping(self, index):
        props = {k: {"type": "x"} for k in self.f.mappings.get(index, set())}
        return {index: {"mappings": {"properties": props}}}


class FakeES:
    """Just enough Elasticsearch surface for arming.is_armed()."""

    def __init__(self, alias=None, mappings=None, status_ids=()):
        self.alias = alias
        self.mappings = mappings or {}
        self.status_ids = set(status_ids)
        self.indices = _Indices(self)

    def options(self, **_kw):
        return self  # ignore_status is a no-op for the fake

    def exists(self, index, id):
        return id in self.status_ids


def _poisoned_world():
    today_idx = _today_index()
    status_id = f"{config.index_prefix()}{_today()}"
    return FakeES(
        alias=today_idx,
        mappings={today_idx: {"order_id", "sku", "gross_amount"}},  # no `amount`
        status_ids={status_id},
    )


def test_armed_world_is_reused():
    assert arming.is_armed(_poisoned_world()) is True


def test_blank_cluster_is_not_armed():
    assert arming.is_armed(FakeES()) is False


def test_healthy_alias_after_remediation_is_not_armed():
    # Post-remediation the alias is flipped to last-known-good (yesterday), so
    # today's poison is no longer current => must reseed.
    yesterday = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    es = _poisoned_world()
    es.alias = f"{config.index_prefix()}{yesterday}"
    assert arming.is_armed(es) is False


def test_repaired_today_index_with_amount_is_not_armed():
    # If today's index carries `amount` (already repaired / not the drift shape),
    # the contradiction is gone => reseed so the examination has something to find.
    es = _poisoned_world()
    es.mappings[_today_index()] = {"order_id", "sku", "amount"}
    assert arming.is_armed(es) is False


def test_missing_status_doc_is_not_armed():
    es = _poisoned_world()
    es.status_ids.clear()  # no SUCCESS status doc = the "green lie" is absent
    assert arming.is_armed(es) is False


def test_probe_error_falls_back_to_not_armed():
    class Boom(FakeES):
        def options(self, **_kw):
            raise RuntimeError("cluster unreachable")

    assert arming.is_armed(Boom()) is False


def test_arm_preserves_incident_memory(monkeypatch):
    # arm() must reset sales/quarantine/baselines/status but NEVER incidents.
    calls = {}

    def fake_reset(es, include_system=True):
        calls["reset_include_system"] = include_system
        return []

    def fake_clear(es, index):
        calls.setdefault("cleared", []).append(index)

    monkeypatch.setattr("reset_world.reset", fake_reset)
    monkeypatch.setattr(arming, "_clear_world_scoped", fake_clear)
    monkeypatch.setattr("seed_baseline.seed", lambda es, days=14: {"days": []})
    monkeypatch.setattr("inject_drift.inject",
                        lambda es: {"index": _today_index(), "rows": 10000})

    out = arming.arm(FakeES())

    assert calls["reset_include_system"] is False  # incidents are NOT wiped
    assert config.INCIDENT_INDEX not in calls.get("cleared", [])
    assert config.BASELINE_INDEX in calls["cleared"]
    assert config.STATUS_INDEX in calls["cleared"]
    assert out["index"] == _today_index()
