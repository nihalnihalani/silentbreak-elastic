"""Inject a silent schema-drift into today's partition (the demo trigger).

Renames the upstream `amount` column to `gross_amount` so the loader silently
nulls revenue while the pipeline still reports SUCCESS. Mock users get this
automatically inside run_demo.py; this script is for the real-mode live demo.
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def main():
    if config.MODE != "real":
        print("MODE=mock: drift is injected inside `python scripts/run_demo.py`.")
        return
    # TODO(real): write a corrupted partition (amount -> gross_amount) to sales-TODAY
    raise NotImplementedError("Wire to the Elastic MCP — see README.")


if __name__ == "__main__":
    main()
