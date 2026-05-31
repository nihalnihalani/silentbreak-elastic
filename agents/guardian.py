"""Guardian — the actuator (the consequential action).

Quarantines the corrupt partition, flips the alias off the poisoned index so every
downstream consumer instantly stops reading bad data, and triggers a clean resync.
Every step is a real, reversible Elastic state change.
"""
from dataclasses import dataclass, field
import config


@dataclass
class GuardianResult:
    quarantined: int
    q_index: str
    alias: str
    alias_was: str | None
    alias_now: str
    resync: str
    actions: list = field(default_factory=list)


def act(client, today_index: str, yesterday_index: str, day: str) -> GuardianResult:
    rf = config.REVENUE_FIELD
    q_index = config.quarantine_index(day)

    # 1) quarantine the poisoned rows (reindex/$merge into an isolation index)
    n = client.quarantine_bad_rows(today_index, q_index, predicate=lambda r: r.get(rf) is None)

    # 2) THE VISIBLE ACTION: flip the alias to the last-known-good index
    alias_was = client.update_alias(config.ALIAS, yesterday_index)

    # 3) trigger a clean resync from the corrected upstream
    resync = client.trigger_resync("sales_connector")

    return GuardianResult(
        quarantined=n, q_index=q_index, alias=config.ALIAS,
        alias_was=alias_was, alias_now=yesterday_index, resync=resync,
        actions=[
            f"reindex {n} corrupt rows -> {q_index}",
            f"update_aliases: {config.ALIAS} {alias_was} -> {yesterday_index}",
            resync,
        ],
    )


def rollback(client, alias_was: str | None):
    """Reversibility — flip the alias back (proves it is a real state change, not a video)."""
    if alias_was:
        client.update_alias(config.ALIAS, alias_was)
