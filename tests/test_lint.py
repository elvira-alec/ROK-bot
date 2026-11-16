"""Undefined names crash a round only when that line runs (1.53: `tags` left in look_spot after
a refactor - every guard round failed at the base look). Caught here instead."""
import subprocess
import sys
from pathlib import Path


def test_no_undefined_names():
    import pytest
    pytest.importorskip("pyflakes")
    root = Path(__file__).resolve().parent.parent
    files = [str(p) for p in (root / "rokwatch").glob("*.py")]
    r = subprocess.run([sys.executable, "-m", "pyflakes", *files], capture_output=True, text=True)
    bad = [l for l in r.stdout.splitlines() if "undefined name" in l]
    assert not bad, "\n".join(bad)
