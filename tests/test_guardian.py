"""Guardian + repair_reindex behavioral contracts (mock mode, no network).

The repair step is the newest write path; these tests pin its semantics:
the rename is applied, the alias is never touched by the repair, the doc
counts reconcile, and none of it happens without a consumable token.
"""
import pytest

import config
from agents import guardian
from integrations.approval import registry
from integrations.elastic_client import ElasticClient


DAY = "2026-06-01"
TODAY = f"sales-{DAY}"
YESTERDAY = "sales-2026-05-31"


def make_client(n_corrupt: int = 50, n_healthy: int = 5) -> ElasticClient:
    client = ElasticClient(mode="mock")
    rows = [{"order_id": i, "sku": f"SKU-{i}", "gross_amount": 10.0 + i}
            for i in range(n_corrupt)]
    rows += [{"order_id": 1000 + i, "sku": f"SKU-H{i}", "amount": 5.0}
             for i in range(n_healthy)]
    client.indices[TODAY] = rows
    client.indices[YESTERDAY] = [
        {"order_id": i, "sku": f"SKU-{i}", "amount": 40.0} for i in range(100)]
    client.aliases[config.ALIAS] = TODAY
    return client


def approved_token(run_id: str) -> str:
    registry.request(run_id, ["test plan"])
    return registry.approve(run_id)


def test_repair_reindex_renames_field_and_counts():
    client = make_client()
    q_index = config.quarantine_index(DAY)
    moved = client.quarantine_missing_field(TODAY, q_index, config.REVENUE_FIELD)
    assert moved == 50
    repaired = client.repair_reindex(q_index, f"{TODAY}-repaired",
                                     "gross_amount", config.REVENUE_FIELD)
    assert repaired == 50
    rows = client.indices[f"{TODAY}-repaired"]
    assert len(rows) == 50
    assert all(config.REVENUE_FIELD in r for r in rows)
    assert all("gross_amount" not in r for r in rows)
    # values survive the rename
    assert {r[config.REVENUE_FIELD] for r in rows} == {10.0 + i for i in range(50)}


def test_repair_reindex_never_touches_alias():
    client = make_client()
    client.aliases[config.ALIAS] = YESTERDAY
    q_index = config.quarantine_index(DAY)
    client.quarantine_missing_field(TODAY, q_index, config.REVENUE_FIELD)
    client.repair_reindex(q_index, f"{TODAY}-repaired",
                          "gross_amount", config.REVENUE_FIELD)
    assert client.aliases[config.ALIAS] == YESTERDAY


def test_guardian_act_full_plan_with_token():
    client = make_client()
    run_id = "test-guardian-ok"
    token = approved_token(run_id)
    steps: list[dict] = []
    result = guardian.act(client, TODAY, YESTERDAY, DAY,
                          registry=registry, run_id=run_id, token=token,
                          on_action=steps.append)
    assert [s["step"] for s in steps][:2] == ["quarantine", "alias_flip"]
    assert client.aliases[config.ALIAS] == YESTERDAY
    assert result.repaired_index == f"{TODAY}-repaired"
    assert result.repaired_docs == 50
    assert "gross_amount" in result.repair_rename


def test_guardian_act_refuses_without_valid_token():
    client = make_client()
    before_aliases = dict(client.aliases)
    with pytest.raises(guardian.ApprovalDenied):
        guardian.act(client, TODAY, YESTERDAY, DAY,
                     registry=registry, run_id="test-guardian-no", token="bogus")
    # zero writes: no quarantine index, no repaired index, alias untouched
    assert client.aliases == before_aliases
    assert config.quarantine_index(DAY) not in client.quarantine
    assert f"{TODAY}-repaired" not in client.indices


def test_guardian_act_token_single_use():
    client = make_client()
    run_id = "test-guardian-reuse"
    token = approved_token(run_id)
    guardian.act(client, TODAY, YESTERDAY, DAY,
                 registry=registry, run_id=run_id, token=token)
    client2 = make_client()
    with pytest.raises(guardian.ApprovalDenied):
        guardian.act(client2, TODAY, YESTERDAY, DAY,
                     registry=registry, run_id=run_id, token=token)
