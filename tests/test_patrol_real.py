"""Patrol decisions on real game screenshots (the browser part replaced by a stand-in)."""

from pathlib import Path

import cv2
import pytest

pytest.importorskip("rapidocr_onnxruntime")

from rokwatch import screen  # noqa: E402
from rokwatch.game import View  # noqa: E402
from rokwatch.patrol import Patrol, Site  # noqa: E402

DIR = Path(__file__).resolve().parent / "real_screens"


class ScreenshotGame:
    """Pretends to be the game: every look returns the next screenshot."""

    def __init__(self, names):
        self.names = list(names)
        self.i = 0

    def goto(self, x, y):
        return True

    def wait(self, s=None):
        pass

    def look(self):
        name = self.names[min(self.i, len(self.names) - 1)]
        self.i += 1
        frame = cv2.imread(str(DIR / f"{name}.png"))
        texts = screen.read_text(frame)
        cam = screen.camera_coords(frame, texts)
        return View(frame, texts, cam[:3] if cam else None, screen.is_simple_view(texts, frame))

    def recall_all(self, near=None):
        return ["(not in this test)"]


def patrol(game, tmp_path):
    return Patrol(game, [Site("my city", 210, 422, "mine")], {"looks_per_site": 2},
                  str(tmp_path / "a.csv"), str(tmp_path / "d"))


def test_warning_from_real_scout_screens(tmp_path):
    p = patrol(ScreenshotGame(["scouted_simple_1", "scouted_simple_2"]), tmp_path)
    titles = [a.title for a in p.visit(p.sites[0])]
    assert "YOU'RE BEING SCOUTED OR ATTACKED" in titles, titles
    assert ",warning," in (tmp_path / "a.csv").read_text()


def test_no_warning_on_quiet_screens(tmp_path):
    p = patrol(ScreenshotGame(["own_city_gathering", "recall_panel"]), tmp_path)
    titles = [a.title for a in p.visit(p.sites[0])]
    assert "YOU'RE BEING SCOUTED OR ATTACKED" not in titles, titles


def test_fighting_alert_from_real_battle(tmp_path):
    p = patrol(ScreenshotGame(["own_city_gathering", "own_city_gathering",
                               "battle_me_attacking", "battle_me_attacking"]), tmp_path)
    p.visit(p.sites[0])                                 # troops gathering
    titles = [a.title for a in p.visit(p.sites[0])]     # now in a battle
    assert "YOUR TROOPS ARE FIGHTING" in titles, titles
