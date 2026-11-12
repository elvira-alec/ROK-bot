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


def test_hunter_reads_his_marching_armies_on_real_screen(tmp_path):
    from rokwatch.hunter import Hunter
    view = ScreenshotGame(["his_army_marching_close"]).look()
    # map directions at this zoom aren't known for the real game yet; any will do to
    # check that both of his armies are found and recognised as marching
    cfg = {"hunt": {"player_name": "[P83L]JONH WICK", "city": [162, 374],
                    "axes_close": [[0.01, -0.01], [0.01, 0.01]]}}
    seen = Hunter(None, cfg, [], str(tmp_path / "h.csv")).read_view(view)
    assert len(seen) == 2 and all(s.moving for s in seen), seen


def test_hunter_ignores_his_city_label_on_real_screen(tmp_path):
    from rokwatch.hunter import Hunter
    cfg = {"hunt": {"player_name": "[P83L]JONH WICK", "city": [162, 374],
                    "axes_close": [[0.01, -0.01], [0.01, 0.01]]}}
    for name in ("enemy_city_close", "enemy_city_simple"):
        view = ScreenshotGame([name]).look()
        assert Hunter(None, cfg, [], str(tmp_path / "h.csv")).read_view(view) == []


def test_patrol_real_scout_notification_and_red_army(tmp_path):
    p = patrol(ScreenshotGame(["scouted_toast_simple", "scouted_toast_simple"]), tmp_path)
    titles = [a.title for a in p.visit(p.sites[0])]
    assert "YOU'VE BEEN SCOUTED" in titles, titles
    assert "ENEMY ARMY MARCHING AT YOU" in titles, titles
    assert "YOU'RE BEING SCOUTED OR ATTACKED" in titles, titles     # the red border too
    log = (tmp_path / "a.csv").read_text()
    assert "toast_scouted" in log and "hostile_army" in log


def test_patrol_real_red_army_without_warning(tmp_path):
    # red army diamond on the map, warning between pulses: still caught
    p = patrol(ScreenshotGame(["attack_red_diamond_simple", "attack_red_diamond_simple"]), tmp_path)
    titles = [a.title for a in p.visit(p.sites[0])]
    assert "ENEMY ARMY MARCHING AT YOU" in titles, titles
