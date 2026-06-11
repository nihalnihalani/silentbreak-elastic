"""Shared test fixtures. Keeps tests runnable from any cwd."""
import os
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)


@pytest.fixture(autouse=True)
def _isolate_run_state():
    """Reset the app's process-global run registry between tests.

    app.main keeps RUNS/RUN_ORDER as module globals (single-operator design),
    so a run started by one test would otherwise be visible to the next as
    "the latest run" — e.g. /api/reverse would find a stale run instead of
    returning 404. Tear them down after each test.
    """
    yield
    try:
        from app import main
    except Exception:
        return
    for rec in list(main.RUNS.values()):
        task = getattr(rec, "task", None)
        if task is not None and not task.done():
            task.cancel()
    main.RUNS.clear()
    main.RUN_ORDER.clear()
