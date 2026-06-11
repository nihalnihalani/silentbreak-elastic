"""McpReader — all real-mode READS go through the official Elastic MCP server.

Wraps the official `mcp` Python SDK's streamable-HTTP client against
config.MCP_URL (works for the docker image `docker.elastic.co/mcp/elasticsearch`
run with `http`, and for Elastic Agent Builder's MCP endpoint via
MCP_AUTH_HEADER). Writes never go through here: the server exposes no write
tools, so seeding/reindex/alias flips use elasticsearch-py directly.

Tool parameter names are not hardcoded: on first use we read each tool's
inputSchema from tools/list and map our arguments onto the schema's property
names (the smoke test prints what it discovered).
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import json
import re
import urllib.request
from typing import Any

import config

# The official server's tool results carry a human label first ("Results",
# "Total results: N, showing M.", "Mappings for index ...:") and the JSON
# payload in a following text content (verified live by scripts/smoke_real.py).
_TOTAL_RE = re.compile(r"Total results:\s*(\d+)")


def _run_sync(coro):
    """Run an async coroutine from sync code, even if a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(asyncio.run, coro).result()


def _pick(props: set[str], candidates: tuple[str, ...], default: str) -> str:
    for c in candidates:
        if c in props:
            return c
    return default


class McpReader:
    def __init__(self, url: str | None = None, auth_header: str | None = None):
        self.url = url or config.MCP_URL
        auth = auth_header if auth_header is not None else config.MCP_AUTH_HEADER
        self.headers: dict[str, str] = {"Authorization": auth} if auth else {}
        self._schemas: dict[str, set[str]] | None = None  # tool -> input property names

    # ---- plumbing ----
    async def _session_call(self, fn):
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        async with streamablehttp_client(self.url, headers=self.headers or None) as (
            read,
            write,
            _,
        ):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return await fn(session)

    def _tool_schemas(self) -> dict[str, set[str]]:
        if self._schemas is None:
            async def _list(session):
                result = await session.list_tools()
                return {
                    t.name: set((t.inputSchema or {}).get("properties", {}).keys())
                    for t in result.tools
                }

            self._schemas = _run_sync(self._session_call(_list))
        return self._schemas

    def _call_tool(self, name: str, args: dict[str, Any]) -> tuple[list[str], Any]:
        """Returns (label_texts, payload): payload is the last JSON-parseable
        content item (the server emits a plain-text label before the data)."""
        async def _call(session):
            result = await session.call_tool(name, args)
            texts = [
                c.text for c in result.content if getattr(c, "text", None) is not None
            ]
            if result.isError:
                raise RuntimeError(f"MCP tool {name} failed: {' '.join(texts)[:500]}")
            if result.structuredContent is not None:
                return texts, result.structuredContent
            payload = None
            for text in texts:
                try:
                    payload = json.loads(text)
                except (json.JSONDecodeError, ValueError):
                    continue  # a human label, not data
            return texts, payload

        return _run_sync(self._session_call(_call))

    # ---- public surface ----
    def ping(self) -> bool:
        """Plain-HTTP liveness: GET {base}/ping.

        The official server (elasticsearch-core-mcp-server 0.4.x) answers
        "Ready"; older docs said "pong". Accept both (verified live in smoke).
        """
        base = self.url.rsplit("/mcp", 1)[0]
        try:
            req = urllib.request.Request(f"{base}/ping", headers=self.headers)
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.read().decode().strip().strip('"') in ("pong", "Ready")
        except Exception:
            return False

    def list_tools(self) -> list[str]:
        return sorted(self._tool_schemas().keys())

    def tool_input_properties(self, tool: str) -> set[str]:
        return set(self._tool_schemas().get(tool, set()))

    def esql(self, query: str) -> dict:
        """Run an ES|QL query; returns {"columns": [...], "values": [...]}.

        The server answers with a JSON array of row objects; normalize it to
        the columns/values shape the rest of the codebase consumes.
        """
        props = self._tool_schemas().get("esql", set())
        key = _pick(props, ("query", "esql", "statement", "esql_query"), "query")
        texts, payload = self._call_tool("esql", {key: query})
        if isinstance(payload, dict) and "columns" in payload:
            return payload
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            names = list(payload[0].keys())
            return {
                "columns": [{"name": n} for n in names],
                "values": [[row.get(n) for n in names] for row in payload],
            }
        return {"columns": [], "values": [], "raw": texts}

    def get_mappings(self, index: str) -> dict:
        props = self._tool_schemas().get("get_mappings", set())
        key = _pick(props, ("index", "indices", "index_name"), "index")
        texts, payload = self._call_tool("get_mappings", {key: index})
        return payload if isinstance(payload, dict) else {"raw": texts}

    def search(self, index: str, body: dict) -> dict:
        """Search via MCP; returns an ES-like {"hits": {"total", "hits"}} shape.

        The server answers with "Total results: N, showing M." plus a JSON
        array of _source documents; rebuild the conventional envelope from it.
        """
        props = self._tool_schemas().get("search", set())
        index_key = _pick(props, ("index", "indices", "index_name"), "index")
        args: dict[str, Any] = {index_key: index}
        body_key = _pick(props, ("query_body", "body", "request_body", "queryBody"), "")
        if body_key:
            args[body_key] = body
        else:
            # Some server versions take top-level query/size instead of one body arg.
            for field in ("query", "size", "sort", "track_total_hits", "aggs"):
                if field in body and field in props:
                    args[field] = body[field]
            if len(args) == 1:  # nothing matched; try the conventional name anyway
                args["query_body"] = body
        texts, payload = self._call_tool("search", args)
        if isinstance(payload, dict) and "hits" in payload:
            return payload
        total: int | None = None
        for text in texts:
            m = _TOTAL_RE.search(text)
            if m:
                total = int(m.group(1))
                break
        sources = payload if isinstance(payload, list) else []
        if total is None:
            total = len(sources)
        return {
            "hits": {
                "total": {"value": total},
                "hits": [{"_source": s} for s in sources if isinstance(s, dict)],
            }
        }
