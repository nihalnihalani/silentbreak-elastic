"""Elastic client — one surface, two modes.

MODE=mock: an in-memory Elasticsearch stand-in so the whole detect -> diagnose
-> approve -> quarantine -> verify loop runs with zero setup (stdlib only).

MODE=real: READS (ES|QL stats, mappings, status/baseline docs, verify query)
go through the official Elastic MCP server via McpReader — MCP is load-bearing.
WRITES (bulk seed, reindex quarantine, alias flip, incident doc) go through
elasticsearch-py directly, because the MCP server exposes no write tools.
That split is deliberate and stated honestly everywhere.
"""
from __future__ import annotations

from typing import Any

import config

_NUMERIC_TYPES = {
    "long", "integer", "short", "byte", "double", "float",
    "half_float", "scaled_float", "unsigned_long",
}


class ElasticClient:
    def __init__(self, mode: str | None = None, prefer_mcp: bool = True):
        self.mode = mode or config.MODE
        # in-memory state (mock mode; harmless empty containers in real mode)
        self.indices: dict[str, list[dict]] = {}     # index -> rows
        self.aliases: dict[str, str] = {}            # alias -> index
        self.quarantine: dict[str, list[dict]] = {}  # quarantine index -> rows
        self.baselines: dict[str, dict] = {}         # metric -> {mean, std}
        self.status: dict[str, dict] = {}            # day -> status doc
        self.incidents: list[dict] = []
        self.es: Any = None
        self.mcp: Any = None
        if self.mode == "real":
            self._connect_real(prefer_mcp)

    # ---- real-mode wiring (lazy imports keep the mock path stdlib-only) ----
    def _connect_real(self, prefer_mcp: bool) -> None:
        from elasticsearch import Elasticsearch  # lazy: not needed in mock mode

        if config.ELASTIC_CLOUD_ID:
            self.es = Elasticsearch(
                cloud_id=config.ELASTIC_CLOUD_ID, api_key=config.ELASTIC_API_KEY
            )
        elif config.ELASTIC_API_KEY:
            self.es = Elasticsearch(config.ES_URL, api_key=config.ELASTIC_API_KEY)
        else:
            self.es = Elasticsearch(config.ES_URL)
        if prefer_mcp:
            from integrations.mcp_client import McpReader

            self.mcp = McpReader()
            if not self.mcp.ping():
                raise RuntimeError(
                    "Elastic MCP server unreachable at "
                    f"{config.MCP_URL} (GET /ping != pong). MCP is load-bearing for "
                    "reads in real mode: run `docker compose up -d` or set MCP_URL. "
                    "(Only the write-side setup scripts may pass prefer_mcp=False.)"
                )

    # ---- setup / seeding ----
    def index_rows(self, index: str, rows: list[dict]) -> None:
        if self.mode == "real":
            from elasticsearch import helpers

            actions = ({"_index": index, "_source": r} for r in rows)
            helpers.bulk(self.es, actions, refresh=True)
            return
        self.indices[index] = list(rows)

    def set_alias(self, alias: str, index: str) -> None:
        if self.mode == "real":
            self.es.indices.update_aliases(
                actions=[
                    {"remove": {"index": "*", "alias": alias, "must_exist": False}},
                    {"add": {"index": index, "alias": alias}},
                ]
            )
        self.aliases[alias] = index

    def put_baseline(self, metric: str, mean: float, std: float) -> None:
        # Doc id and scope are world-prefixed so a smoke run never reads/writes
        # the demo world's baselines (same scheme as scripts/seed_baseline.py).
        prefix = config.index_prefix()
        doc = {"metric": metric, "mean": mean, "std": max(std, 1e-9), "scope": prefix}
        if self.mode == "real":
            self.es.index(index=config.BASELINE_INDEX, id=f"{prefix}{metric}", document=doc)
            self.refresh(config.BASELINE_INDEX)
        self.baselines[metric] = {"mean": doc["mean"], "std": doc["std"]}

    def get_baseline(self, metric: str) -> dict:
        if self.mode == "real":
            # Exact doc-id lookup (seed writes id = f"{prefix}{metric}") so the
            # smoke world and the demo world can never read each other's stats.
            doc_id = f"{config.index_prefix()}{metric}"
            body = {"query": {"ids": {"values": [doc_id]}}, "size": 1}
            hits = self._search_hits(config.BASELINE_INDEX, body)
            if hits:
                src = hits[0].get("_source", hits[0])
                return {"mean": float(src["mean"]), "std": float(src["std"])}
            return {"mean": 0.0, "std": 1.0}
        return self.baselines.get(metric, {"mean": 0.0, "std": 1.0})

    # ---- pipeline status (the "green" lie lives in silentbreak-status) ----
    def index_status(self, doc: dict) -> None:
        """doc = {"day": ..., "run": "#4471", "status": "SUCCESS"}"""
        prefix = config.index_prefix()
        if self.mode == "real":
            self.es.index(index=config.STATUS_INDEX, id=f"{prefix}{doc['day']}",
                          document={**doc, "scope": prefix})
            self.refresh(config.STATUS_INDEX)
        self.status[doc["day"]] = dict(doc)

    def get_status(self, day: str) -> dict | None:
        if self.mode == "real":
            # Exact doc-id lookup (seed writes id = f"{prefix}{day}"), world-scoped.
            doc_id = f"{config.index_prefix()}{day}"
            body = {"query": {"ids": {"values": [doc_id]}}, "size": 1}
            hits = self._search_hits(config.STATUS_INDEX, body)
            return hits[0].get("_source", hits[0]) if hits else None
        return self.status.get(day)

    # ---- detection (real: ES|QL through MCP) ----
    def esql_stats(self, index: str) -> dict:
        """{"row_count", "null_rate", "distinct_sku", "avg_amount"} for a partition."""
        if self.mode == "real":
            return self._esql_stats_real(index)
        rows = self._mock_rows(index)
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

    def _esql_stats_real(self, index: str) -> dict:
        # ES|QL errors on unknown columns, so the poisoned index (no `amount`
        # field at all) needs a defensive split:
        #   counts/distinct -> ES|QL; null_rate -> exists query (missing field
        #   counts as null, matching mock semantics); AVG only if the mapping
        #   says `amount` exists and is numeric.
        rf = config.REVENUE_FIELD
        out = self.mcp.esql(
            f"FROM {index} | STATS row_count = COUNT(*), distinct_sku = COUNT_DISTINCT(sku)"
        )
        row = _esql_first_row(out)
        row_count = int(row.get("row_count", 0) or 0)
        distinct_sku = int(row.get("distinct_sku", 0) or 0)

        body = {
            "query": {"bool": {"must_not": {"exists": {"field": rf}}}},
            "size": 0,
            "track_total_hits": True,
        }
        missing = self._count(index, body)
        null_rate = (missing / row_count) if row_count else 0.0

        avg_amount = 0.0
        if self._field_types(index).get(rf) in _NUMERIC_TYPES:
            avg_out = self.mcp.esql(f"FROM {index} | STATS avg_amount = AVG({rf})")
            avg_row = _esql_first_row(avg_out)
            avg_amount = float(avg_row.get("avg_amount") or 0.0)
        return {
            "row_count": row_count,
            "null_rate": null_rate,
            "distinct_sku": distinct_sku,
            "avg_amount": avg_amount,
        }

    # ---- diagnosis (real: get_mappings through MCP) ----
    def get_mappings(self, index: str) -> set[str]:
        if self.mode == "real":
            return set(self._field_types(index).keys())
        fields: set[str] = set()
        for r in self._mock_rows(index):
            fields.update(r.keys())
        return fields

    def _field_types(self, index: str) -> dict[str, str]:
        raw = self.mcp.get_mappings(index)
        props = _find_properties(raw)
        return {name: str(spec.get("type", "object")) for name, spec in props.items()}

    # ---- action (writes; direct client in real mode, token-gated upstream) ----
    def quarantine_missing_field(self, source_index: str, q_index: str, field: str) -> int:
        """Move every row missing `field` into the quarantine index. Returns count."""
        if self.mode == "real":
            query = {"bool": {"must_not": {"exists": {"field": field}}}}
            res = self.es.reindex(
                source={"index": source_index, "query": query},
                dest={"index": q_index},
                wait_for_completion=True,
                refresh=True,
            )
            moved = int(res.get("created", 0)) + int(res.get("updated", 0))
            self.es.delete_by_query(index=source_index, query=query, refresh=True)
            return moved
        rows = self._mock_rows(source_index)
        bad = [r for r in rows if r.get(field) is None]
        clean = [r for r in rows if r.get(field) is not None]
        self.quarantine.setdefault(q_index, []).extend(bad)
        self.indices[source_index] = clean
        return len(bad)

    def update_alias(self, alias: str, target_index: str) -> str | None:
        """THE consequential action: one atomic alias flip. Returns previous target."""
        prev = self.alias_target(alias)
        if self.mode == "real":
            actions: list[dict] = []
            if prev:
                actions.append({"remove": {"index": prev, "alias": alias}})
            actions.append({"add": {"index": target_index, "alias": alias}})
            self.es.indices.update_aliases(actions=actions)
        self.aliases[alias] = target_index
        return prev

    def alias_target(self, alias: str) -> str | None:
        if self.mode == "real":
            try:
                res = self.es.indices.get_alias(name=alias)
                names = sorted(dict(res).keys())
                return names[0] if names else None
            except Exception:
                return None
        return self.aliases.get(alias)

    # ---- incidents ----
    def index_incident(self, doc: dict) -> None:
        if self.mode == "real":
            self.es.index(index=config.INCIDENT_INDEX, id=doc["incident_id"], document=doc)
            self.refresh(config.INCIDENT_INDEX)
        self.incidents.append(dict(doc))

    def list_incidents(self) -> list[dict]:
        if self.mode == "real":
            try:
                body = {"query": {"match_all": {}}, "size": 50}
                hits = self._search_hits(config.INCIDENT_INDEX, body)
                docs = [h.get("_source", h) for h in hits]
                if docs:
                    return sorted(docs, key=lambda d: str(d.get("day", "")), reverse=True)
            except Exception:
                pass  # fall through to the in-process cache
        return list(reversed(self.incidents))

    # ---- housekeeping ----
    def refresh(self, index: str) -> None:
        if self.mode == "real":
            try:
                self.es.indices.refresh(index=index)
            except Exception:
                pass

    def delete_index(self, index: str) -> None:
        if self.mode == "real":
            self.es.indices.delete(index=index, ignore_unavailable=True)
            return
        self.indices.pop(index, None)
        self.quarantine.pop(index, None)

    # ---- internals ----
    def _mock_rows(self, index: str) -> list[dict]:
        if index in self.indices:
            return self.indices[index]
        if index in self.aliases:  # reads through the alias resolve to its target
            return self.indices.get(self.aliases[index], [])
        return self.quarantine.get(index, [])

    def _search_hits(self, index: str, body: dict) -> list[dict]:
        out = self.mcp.search(index, body)
        hits = out.get("hits", out)
        if isinstance(hits, dict):
            inner = hits.get("hits", [])
            return inner if isinstance(inner, list) else []
        return hits if isinstance(hits, list) else []

    def _count(self, index: str, body: dict) -> int:
        out = self.mcp.search(index, body)
        total = out.get("hits", {}).get("total") if isinstance(out, dict) else None
        if isinstance(total, dict) and "value" in total:
            return int(total["value"])
        if isinstance(total, (int, float)):
            return int(total)
        # MCP server returned an unexpected shape; last resort is a direct count
        # (still a real Elasticsearch read, just not via MCP) so the run survives.
        res = self.es.count(index=index, query=body["query"])
        return int(res["count"])


def _esql_first_row(out: dict) -> dict:
    cols = [c.get("name") if isinstance(c, dict) else str(c) for c in out.get("columns", [])]
    values = out.get("values", [])
    if not cols or not values:
        return {}
    return dict(zip(cols, values[0]))


def _find_properties(raw: Any) -> dict[str, dict]:
    """Pull the top-level `properties` map out of whatever shape get_mappings returns."""
    if not isinstance(raw, dict):
        return {}
    if "properties" in raw and isinstance(raw["properties"], dict):
        return raw["properties"]
    for value in raw.values():
        found = _find_properties(value)
        if found:
            return found
    return {}
