"""Elastic MCP client wrapper.

In MODE=mock this is an in-memory Elasticsearch stand-in so the whole detect ->
diagnose -> quarantine -> resync loop runs with zero setup. In MODE=real, the
methods map 1:1 onto Elastic MCP tools (esql_query, get_mappings, reindex,
update_aliases, connector sync) — wire them where marked TODO(real).
"""
from __future__ import annotations
import config


class ElasticClient:
    def __init__(self, mode: str | None = None):
        self.mode = mode or config.MODE
        self.indices: dict[str, list[dict]] = {}     # index -> rows
        self.aliases: dict[str, str] = {}            # alias -> index
        self.quarantine: dict[str, list[dict]] = {}  # quarantine index -> rows
        self.baselines: dict[str, dict] = {}         # metric -> {mean, std}
        self.incidents: list[dict] = []
        if self.mode == "real":
            self._connect_real()

    # ---- setup / seeding (mock) ----
    def index_rows(self, index: str, rows: list[dict]):
        self.indices[index] = rows

    def set_alias(self, alias: str, index: str):
        self.aliases[alias] = index

    def put_baseline(self, metric: str, mean: float, std: float):
        self.baselines[metric] = {"mean": mean, "std": max(std, 1e-9)}

    # ---- detection (maps to esql_query STATS) ----
    def esql_stats(self, index: str) -> dict:
        """Aggregations over a landed partition. Real: an ES|QL STATS query."""
        if self.mode == "real":
            return self._esql_stats_real(index)
        rows = self.indices.get(index, [])
        n = len(rows) or 1
        rf = config.REVENUE_FIELD
        null_rev = sum(1 for r in rows if r.get(rf) is None)
        vals = [r[rf] for r in rows if r.get(rf) is not None]
        return {
            "row_count": len(rows),
            "null_rate": null_rev / n,
            "distinct_sku": len({r.get("sku") for r in rows}),
            "avg_amount": (sum(vals) / len(vals)) if vals else 0.0,
        }

    def get_baseline(self, metric: str) -> dict:
        return self.baselines.get(metric, {"mean": 0.0, "std": 1.0})

    # ---- diagnosis (maps to get_mappings) ----
    def get_mappings(self, index: str) -> set[str]:
        if self.mode == "real":
            return self._get_mappings_real(index)
        fields: set[str] = set()
        for r in self.indices.get(index, []):
            fields.update(r.keys())
        return fields

    # ---- action (maps to reindex + update_aliases + connector sync) ----
    def quarantine_bad_rows(self, source_index: str, q_index: str, predicate) -> int:
        """reindex + $merge equivalent: move rows matching predicate into quarantine."""
        if self.mode == "real":
            return self._quarantine_real(source_index, q_index, predicate)
        rows = self.indices.get(source_index, [])
        bad = [r for r in rows if predicate(r)]
        clean = [r for r in rows if not predicate(r)]
        self.quarantine.setdefault(q_index, []).extend(bad)
        self.indices[source_index] = clean
        return len(bad)

    def update_alias(self, alias: str, target_index: str) -> str:
        """The consequential action: flip the alias so consumers stop reading poison."""
        prev = self.aliases.get(alias)
        if self.mode == "real":
            self._update_alias_real(alias, target_index)
        self.aliases[alias] = target_index
        return prev

    def trigger_resync(self, connector: str) -> str:
        if self.mode == "real":
            return self._trigger_resync_real(connector)
        return f"resync({connector}) queued [mock]"

    def index_incident(self, doc: dict):
        if self.mode == "real":
            return self._index_incident_real(doc)
        self.incidents.append(doc)

    def alias_target(self, alias: str) -> str | None:
        return self.aliases.get(alias)

    # ---- real-mode hooks (wire to Elastic MCP) ----
    def _connect_real(self):
        # TODO(real): construct the Elastic MCP client from ELASTIC_* env vars.
        raise NotImplementedError("Set up the Elastic MCP client (see README).")

    def _esql_stats_real(self, index): raise NotImplementedError
    def _get_mappings_real(self, index): raise NotImplementedError
    def _quarantine_real(self, src, q, pred): raise NotImplementedError
    def _update_alias_real(self, alias, target): raise NotImplementedError
    def _trigger_resync_real(self, connector): raise NotImplementedError
    def _index_incident_real(self, doc): raise NotImplementedError
