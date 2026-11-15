import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest


@pytest.fixture(autouse=True)
def _own_scale_cache(tmp_path, monkeypatch):
    """Each test gets its own map-scale file: the hunter saves the scale it measured
    (scale_cache.json) and reuses it at the next start - tests mustn't share one."""
    try:
        from rokwatch import hunter
    except Exception:
        return
    monkeypatch.setattr(hunter, "SCALE_CACHE", tmp_path / "scale_cache.json")
