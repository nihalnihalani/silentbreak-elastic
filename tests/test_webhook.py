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


def test_missing_required_fields_is_422(client):
    resp = client.post("/pipeline-completed", json={"garbage": True})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"] == "missing_fields"
    assert set(body["fields"]) == {"day", "today_index", "yesterday_index"}
