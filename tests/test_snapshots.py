"""Pictures saved for improving the bot."""

import io
import json
import time
import zipfile
from pathlib import Path

import cv2
import numpy as np

from rokwatch import snapshots
from rokwatch.game import Game
from rokwatch.worker import Worker

REAL = Path(__file__).resolve().parent / "real_screens"


def test_save_list_prune_zip(tmp_path):
    img = np.full((50, 80, 3), 120, np.uint8)
    names = [snapshots.save(img, f"alert {i}", {"i": i}, folder=tmp_path, keep=5) for i in range(8)]
    assert len(set(names)) == 8                                    # same second: still unique
    pics = sorted(tmp_path.glob("*.jpg"))
    assert len(pics) == 5 and len(list(tmp_path.glob("*.json"))) == 5   # oldest pruned
    items = snapshots.listing(tmp_path)
    assert items[0]["tag"] == "alert 7" and len(items) == 5
    meta = json.loads((tmp_path / items[0]["name"]).with_suffix(".json").read_text())
    assert meta["i"] == 7 and meta["picture"] == items[0]["name"]
    z = zipfile.ZipFile(io.BytesIO(snapshots.zip_bytes(tmp_path)))
    assert len(z.namelist()) == 10
    assert snapshots.path_of("../x.png", tmp_path) is None
    assert snapshots.path_of(items[0]["name"], tmp_path) is not None
    png = snapshots.save(img, "manual", folder=tmp_path, png=True)
    assert png.endswith(".png") and cv2.imread(str(tmp_path / png)).shape == img.shape


class FakeShots:
    """A 'browser' that shows a real screenshot."""
    def __init__(self, path):
        self.frame = cv2.imread(str(path))

    def screenshot(self, area=None):
        return self.frame.copy()


def test_describe_real_screen():
    g = Game(FakeShots(REAL / "own_city_gathering.png"), settle=0)
    v = g.look()
    info = snapshots.describe(v)
    assert info["camera"][1:] == [209, 418] and info["troops"]
    json.dumps(info, default=str)


def test_worker_snapshots(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text("snapshots: {every_minutes: 1}\nnotify: {}\n")
    w = Worker(str(cfg))
    w.gb = FakeShots(REAL / "march_simple_view.png")
    monkeypatch.setattr("rokwatch.commands.make_game", lambda gb, cfg: Game(gb, settle=0), raising=False)
    w._do_snap(note="his scout")
    items = snapshots.listing()
    assert len(items) == 1 and items[0]["name"].endswith(".png") and "his scout" in items[0]["tag"]
    # while patrolling: the request is picked up at the next step from the last picture
    w.status.phase = "patrolling"
    w.request_snap("busy one")
    assert w._snap_wanted == "busy one"
    w._last_sample = time.time()             # no sample due
    w._maybe_sample()
    assert w._snap_wanted is None and len(snapshots.listing()) == 2
    # samples every few minutes
    w._last_sample = 0
    w._maybe_sample()
    assert [i["tag"] for i in snapshots.listing()].count("sample") == 1

    class A:
        key, title, message, urgent, image, cooldown = "k", "His army", "near Farm", True, None, False

    class N:
        def send(self, *a):
            pass
    w._alert(N(), A())
    assert any(i["tag"].startswith("alert") for i in snapshots.listing())
