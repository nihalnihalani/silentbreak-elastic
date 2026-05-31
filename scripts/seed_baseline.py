"""Seed a 30-day baseline + daily partitions into Elasticsearch (real mode).

Mock mode users don't need this — `scripts/run_demo.py` seeds in-memory.
In real mode this writes `silentbreak-baselines` and `sales-YYYY-MM-DD` indices
via the Elastic MCP. Fill in the TODO with your MCP calls.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def main():
    if config.MODE != "real":
        print("MODE=mock: run `python scripts/run_demo.py` instead (no seeding needed).")
        return
    # TODO(real): use the Elastic MCP to bulk-index 30 days of healthy partitions
    #             and compute/store the per-metric baseline (mean/std).
    raise NotImplementedError("Wire to the Elastic MCP — see README 'Getting Started'.")


if __name__ == "__main__":
    main()
