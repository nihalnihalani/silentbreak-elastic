"""The /pipeline-completed compat webhook rejects malformed input cleanly.

Regression for devil's-advocate round 3: an unguarded request.json() turned
any malformed POST into a raw 500 on the live instance.
"""
import os

import pytest

os.environ.setdefault("SILENTBREAK_MODE", "mock")

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402


@pytest.fixture()
def client():
    with TestClient(app) as c:
        yield c


def test_empty_body_is_400_not_500(client):
    resp = client.post("/pipeline-completed")
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_json"


def test_non_json_body_is_400(client):
    resp = client.post("/pipeline-completed", content=b"not json",
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 400


@pytest.mark.parametrize("scalar", ["42", "null", "true"])
def test_scalar_json_body_is_400(client, scalar):
    # Round-4 regression: "day" in 42 raised TypeError -> raw 500.
    resp = client.post("/pipeline-completed", content=scalar.encode(),
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 400
    assert resp.json()["error"] == "invalid_json"


def test_null_valued_required_fields_is_422(client):
    resp = client.post("/pipeline-completed", json={
        "day": None, "today_index": None, "yesterday_index": "sales-x"})
    assert resp.status_code == 422
    assert set(resp.json()["fields"]) == {"day", "today_index"}


def test_missing_required_fields_is_422(client):
    resp = client.post("/pipeline-completed", json={"garbage": True})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "missing_fields"
    assert set(body["fields"]) == {"day", "today_index", "yesterday_index"}


@pytest.mark.parametrize("endpoint", ["/api/approve", "/api/reject", "/api/reverse"])
@pytest.mark.parametrize("run_id", [[1], {"a": 1}, 42])
def test_hitl_endpoints_tolerate_unhashable_run_id(client, endpoint, run_id):
    # Round-6 regression: RUNS.get(unhashable) raised TypeError -> 500.
    # Non-string run_id values must behave like an absent run_id.
    resp = client.post(endpoint, json={"run_id": run_id})
    assert resp.status_code == 404
    assert resp.json()["error"] == "no_such_run"


@pytest.mark.parametrize("endpoint", ["/api/approve", "/api/reject", "/api/reverse"])
@pytest.mark.parametrize("payload", ["42", "[]", '"x"'])
def test_hitl_endpoints_tolerate_non_dict_bodies(client, endpoint, payload):
    # Round-5 regression: body.get() on a non-dict raised AttributeError -> 500.
    # Non-dict bodies must behave like empty bodies (404 no_such_run here,
    # since no run is active) — never a raw 500.
    resp = client.post(endpoint, content=payload.encode(),
                       headers={"Content-Type": "application/json"})
    assert resp.status_code == 404
    assert resp.json()["error"] == "no_such_run"
