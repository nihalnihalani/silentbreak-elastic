"""End-to-end sanity: the stdlib-only mock demo runs clean (no network/docker)."""
import os
import subprocess
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def test_run_demo_exits_zero():
    env = dict(os.environ, SILENTBREAK_MODE="mock")
    proc = subprocess.run(
        [sys.executable, os.path.join("scripts", "run_demo.py")],
        cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, f"demo failed:\n{proc.stdout}\n{proc.stderr}"
    # The demo's contract: it tells the silent-failure story end to end.
    assert "SilentBreak" in proc.stdout
    assert "CONTRADICTION" in proc.stdout
