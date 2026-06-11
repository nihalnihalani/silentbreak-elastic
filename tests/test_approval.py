"""ApprovalRegistry — the single HITL gate. Behavioral contract tests:
mint, single-use consume, TTL expiry, reject path, unknown tokens/runs.
"""
from integrations.approval import ApprovalRegistry

RUN = "run-test-1"
PLAN = ["quarantine corrupt rows", "flip alias to last-known-good"]


def fresh(ttl=300):
    reg = ApprovalRegistry(ttl_seconds=ttl)
    reg.request(RUN, PLAN)
    return reg


def test_approve_mints_token():
    reg = fresh()
    token = reg.approve(RUN)
    assert isinstance(token, str) and len(token) >= 16
    assert reg.state(RUN) == "approved"


def test_consume_once_succeeds():
    reg = fresh()
    token = reg.approve(RUN)
    assert reg.consume(RUN, token) is True


def test_second_consume_fails():
    reg = fresh()
    token = reg.approve(RUN)
    assert reg.consume(RUN, token) is True
    assert reg.consume(RUN, token) is False


def test_expired_token_fails():
    # A negative TTL means the token is already past its expiry at mint time.
    reg = fresh(ttl=-1)
    token = reg.approve(RUN)
    assert reg.consume(RUN, token) is False


def test_expiry_via_clock_advance(monkeypatch):
    import integrations.approval as approval_mod

    reg = fresh(ttl=10)
    token = reg.approve(RUN)
    real_monotonic = approval_mod.time.monotonic
    monkeypatch.setattr(approval_mod.time, "monotonic", lambda: real_monotonic() + 11)
    assert reg.consume(RUN, token) is False


def test_reject_leaves_no_consumable_token():
    reg = fresh()
    reg.reject(RUN)
    assert reg.state(RUN) == "rejected"
    # No token was ever minted; nothing should consume.
    assert reg.consume(RUN, None) is False
    assert reg.consume(RUN, "anything") is False


def test_wrong_or_missing_token_fails():
    reg = fresh()
    real = reg.approve(RUN)
    assert reg.consume(RUN, "deadbeef" * 4) is False
    assert reg.consume(RUN, None) is False
    assert reg.consume(RUN, "") is False
    # The real token is still intact after bad attempts.
    assert reg.consume(RUN, real) is True


def test_unknown_run_fails():
    reg = fresh()
    token = reg.approve(RUN)
    assert reg.consume("no-such-run", token) is False
    assert reg.state("no-such-run") == "idle"
