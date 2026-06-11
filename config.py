"""Central config for SilentBreak.

The mock demo runs with no external services on the Python stdlib alone.
Set SILENTBREAK_MODE=real (plus .env) to wire real Elasticsearch, the official
Elastic MCP server (reads), and Gemini via ADK (optional; deterministic
fallback engine is used when no GOOGLE_API_KEY is present).
"""
from __future__ import annotations

import os

try:  # optional: the stdlib-only mock path must not require python-dotenv
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

MODE: str = os.getenv("SILENTBREAK_MODE", "mock")  # "mock" | "real"

# --- Elasticsearch (real mode; writes go direct via elasticsearch-py) ---
ES_URL: str = os.getenv("ES_URL", "http://localhost:9200")
ELASTIC_CLOUD_ID: str | None = os.getenv("ELASTIC_CLOUD_ID") or None
ELASTIC_API_KEY: str | None = os.getenv("ELASTIC_API_KEY") or None

# --- Official Elastic MCP server (real mode; ALL reads go through this) ---
MCP_URL: str = os.getenv("MCP_URL", "http://localhost:8080/mcp")
# e.g. "ApiKey xxx" when pointing MCP_URL at Elastic Agent Builder's MCP endpoint
MCP_AUTH_HEADER: str | None = os.getenv("MCP_AUTH_HEADER") or None

# --- Gemini / ADK (optional) ---
GOOGLE_API_KEY: str | None = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or None
GEMINI_MODEL: str = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")

# --- HITL approval ---
APPROVAL_TTL_SECONDS: int = int(os.getenv("APPROVAL_TTL_SECONDS", "300"))

# The field the loader expects to populate (the one an upstream rename can silently null).
REVENUE_FIELD = "amount"

# Downstream consumers read through this alias; flipping it is the consequential action.
ALIAS = "revenue_current"

# z-score above which a metric deviation counts as a contradiction.
Z_THRESHOLD = 5.0

BASELINE_INDEX = "silentbreak-baselines"
INCIDENT_INDEX = "silentbreak-incidents"
STATUS_INDEX = "silentbreak-status"  # pipeline status docs (the "green" lie)


# Index naming. The prefix is read at call time so the smoke test can isolate
# its data with SILENTBREAK_INDEX_PREFIX=sales-smoke- without import-order games.
def index_prefix() -> str:
    return os.getenv("SILENTBREAK_INDEX_PREFIX", "sales-")


def world_tag() -> str:
    """Empty for the default world; "smoke-" (etc.) when an alternate prefix is set.

    Namespaces every derived artifact (quarantine indices, incident ids, and the
    baseline/status doc ids) so a smoke run can never touch the demo world's data.
    """
    prefix = index_prefix()
    if prefix == "sales-":
        return ""
    tag = prefix.removeprefix("sales-").strip("-")
    return f"{tag}-" if tag else ""


def partition_index(day: str) -> str:
    return f"{index_prefix()}{day}"


def quarantine_index(day: str) -> str:
    return f"silentbreak-quarantine-{world_tag()}{day}"


def incident_id(day: str) -> str:
    return f"SB-{world_tag().upper()}{day}"
