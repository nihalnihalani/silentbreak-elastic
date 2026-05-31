"""Central config for SilentBreak.

The mock demo runs with no external services. Set SILENTBREAK_MODE=real and fill
.env to wire the real Elastic MCP + Gemini.
"""
import os

MODE = os.getenv("SILENTBREAK_MODE", "mock")  # "mock" | "real"

# The field the loader expects to populate (the one an upstream rename can silently null).
REVENUE_FIELD = "amount"

# Downstream consumers read through this alias; flipping it is the consequential action.
ALIAS = "revenue_current"

# z-score above which a metric deviation counts as a contradiction.
Z_THRESHOLD = 5.0

# Index naming
def partition_index(day: str) -> str:
    return f"sales-{day}"

def quarantine_index(day: str) -> str:
    return f"silentbreak-quarantine-{day}"

BASELINE_INDEX = "silentbreak-baselines"
INCIDENT_INDEX = "silentbreak-incidents"
