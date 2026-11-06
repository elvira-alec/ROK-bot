"""Hunting his armies on the test page: sweep, track, and the events in order."""

import math
import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright")
pytest.importorskip("rapidocr_onnxruntime")

from rokwatch.browser import GameBrowser  # noqa: E402
from rokwatch.game import Game  # noqa: E402
from rokwatch.hunter import Hunter, Tracker, ArmySeen, sweep_points, to_tile, view_size_tiles  # noqa: E402
from test_end_to_end import EXE  # noqa: E402

PAGE = (Path(__file__).resolve().parent / "fake_game/rok.html").as_uri()
M = np.array([[24, 10], [-8, 20]], float)          # the test page's tile -> pixel mapping
AXES = np.linalg.inv(M).tolist()
NAME = "[P83L]JONH WICK"
CITY = (162, 374)
SITES = [("logging camp", (205, 424)), ("my city", (209, 418))]


# ---------------------------------------------------------------- pure maths

def test_to_tile_matches_page_mapping():
    # a tile 5 right / 3 down from the camera is drawn at centre + M @ (5, 3)
    px = np.array([717, 330]) + M @ np.array([5, 3])
    x, y = to_tile((100, 200), AXES, px, (660, 1434))
    assert abs(x - 105) < 1e-6 and abs(y - 203) < 1e-6


def test_sweep_points_cover_area_city_first():
    pts = sweep_points((100, 100), 45, AXES, (660, 1434))
    assert pts[0] == (100, 100) and len(pts) == len(set(pts))
    # every tile within the radius is on at least one screen
    A = np.linalg.inv(np.array(AXES))
    for tx in range(55, 146, 5):
        for ty in range(55, 146, 5):
            if math.dist((tx, ty), (100, 100)) > 45:
                continue
            on = [p for p in pts if abs((A @ np.subtract((tx, ty), p))[0]) < 700
                  and abs((A @ np.subtract((tx, ty), p))[1]) < 290]
            assert on, (tx, ty)


def test_view_size():
    w, h = view_size_tiles(AXES, (660, 1434))
    assert 55 < w < 70 and 40 < h < 55


def test_tracker_events_in_order():
    tr = Tracker(CITY, SITES)
    t = 0.0

    def seen(tile, ends=None):
        return ArmySeen(tile, ends is not None, ends, t)
    assert tr.update([seen((150, 390)), seen((180, 360))], t) == []           # parked farmers
    # both now on lines to his city (one look can't say which way: their history does)
    ev = tr.update([seen((151, 389), ((150, 390), CITY)), seen((179, 361), ((180, 360), CITY))], 1)
    assert [e[0] for e in ev] == ["pulling_back"], ev
    assert [e[0] for e in tr.update([], 2)] == ["all_home"]
    # a line from his city to your camp: alert at once, direction or not
    ev = tr.update([seen((165, 378), (CITY, (205, 424)))], 3)
    assert [e[0] for e in ev] == ["army_out"] and ev[0][2]["target"] == "logging camp"
    assert tr.update([seen((170, 384), (CITY, (205, 424)))], 4) == []          # no repeat
    # an army that was at his city and now walks away from it: "sent out"
    tr2 = Tracker(CITY, SITES)
    tr2.update([seen((163, 375))], 0)
    ev = tr2.update([seen((156, 367), (CITY, (120, 330)))], 1)
    assert [e[0] for e in ev] == ["army_out"] and ev[0][2]["target"] is None


# ---------------------------------------------------------------- on the test page

@pytest.fixture
def game(tmp_path):
    with GameBrowser(PAGE, str(tmp_path / "p"), (1434, 660), headless=True,
                     executable_path=EXE) as gb:
        gb.page.wait_for_function("window.sim && sim.ready()")
        time.sleep(0.3)
        yield Game(gb, settle=0.4)


def hunter(game, tmp_path, radius=20, method="close"):
    cfg = {"hunt": {"player_name": NAME, "city": list(CITY), "radius": radius, "axes_close": AXES,
                    "method": method, "checks_per_sweep": 4},
           "patrol": {"axes": AXES}}     # the test page draws every zoom at the same scale
    return Hunter(game, cfg, SITES, str(tmp_path / "his.csv"))


def js(game, expr):
    return game.b.page.evaluate(expr)


def test_finds_his_armies_with_map_coordinates(game, tmp_path):
    js(game, f"sim.farmer('{NAME}', [155, 368]); sim.farmer('{NAME}', [172, 371]);"
             "sim.farmer('[XX]Someone Else', [165, 380])")
    h = hunter(game, tmp_path)
    seen, stops = h.sweep()
    tiles = sorted(s.tile for s in seen)
    assert len(tiles) == 2, tiles                     # his two, not the other player's
    for got, want in zip(tiles, [(155, 368), (172, 371)]):
        assert math.dist(got, want) < 1.5, (got, want)
    assert not any(s.moving for s in seen)


def test_full_story(game, tmp_path):
    """Farmers out -> pulled back home -> all home -> army sent at your camp."""
    js(game, f"sim.farmer('{NAME}', [155, 368]); sim.farmer('{NAME}', [172, 371])")
    h = hunter(game, tmp_path)
    assert h.round() == []                                      # first look: baseline

    js(game, "sim.sendFarmer(0, [162, 374], 0.004); sim.sendFarmer(0, [162, 374], 0.004)")
    time.sleep(1)
    titles = [a.title for a in h.round()]
    assert "He's pulling his troops back" in titles, titles

    js(game, "sim.clear()")
    titles = [a.title for a in h.round()]
    assert titles == ["All his troops are home"], titles

    js(game, f"sim.march('enemy', 'camp', 'white', 0.15, 0.002, '{NAME}')")
    alerts = h.round()
    assert [a.title for a in alerts] == ["HE SENT AN ARMY OUT AT YOU"], [a.title for a in alerts]
    assert "logging camp" in alerts[0].message and alerts[0].urgent
    assert "marching" in [a["state"] for a in h.tracker.summary()]
    assert (tmp_path / "his.csv").read_text().count("\n") >= 3


def test_bigger_area_takes_several_stops(game, tmp_path):
    js(game, f"sim.farmer('{NAME}', [130, 400])")      # off the first screen
    h = hunter(game, tmp_path, radius=45)
    seen, stops = h.sweep()
    assert stops > 1
    assert any(math.dist(s.tile, (130, 400)) < 1.5 for s in seen), [s.tile for s in seen]



# ---------------------------------------------------------------- zoomed-out sweep + zoom in to check names

def test_zoomed_out_finds_diamonds_and_checks_names(game, tmp_path):
    js(game, f"sim.farmer('{NAME}', [155, 368]); sim.farmer('{NAME}', [172, 371]);"
             "sim.farmer('[XX]Someone Else', [166, 384])")
    h = hunter(game, tmp_path, method="zoomed_out")
    assert h.round() == []
    owners = sorted((a.owner, round(a.tile[0]), round(a.tile[1])) for a in h.tracker.armies)
    assert [o[0] for o in owners] == ["him", "him", "other"], owners
    his = [a for a in h.tracker.summary() if a["his"]]
    assert len(his) == 2
    assert js(game, "sim.state.zoom") == 1       # back to the zoomed-out view afterwards
    # second sweep: already identified, no zooming in again
    h.round()
    assert all(a.checks == 1 for a in h.tracker.armies)


def test_zoomed_out_full_story(game, tmp_path):
    js(game, f"sim.farmer('{NAME}', [155, 368]); sim.farmer('{NAME}', [172, 371])")
    h = hunter(game, tmp_path, method="zoomed_out")
    assert h.round() == []
    js(game, "sim.sendFarmer(0, [162, 374], 0.004); sim.sendFarmer(0, [162, 374], 0.004)")
    time.sleep(1)
    assert "He's pulling his troops back" in [a.title for a in h.round()]
    js(game, "sim.clear()")
    assert [a.title for a in h.round()] == ["All his troops are home"]
    js(game, f"sim.march('enemy', 'camp', 'white', 0.15, 0.002, '{NAME}')")
    titles = [a.title for a in h.round()]
    assert titles == ["HE SENT AN ARMY OUT AT YOU"], titles


def test_zoomed_out_ignores_other_players_marching(game, tmp_path):
    js(game, "sim.march([150, 360], [205, 424], 'white', 0.2, 0.002, '[XX]Someone Else')")
    h = hunter(game, tmp_path, method="zoomed_out")
    h.round()
    js(game, "sim.state.marches[0].progress = 0.3")
    alerts = h.round()
    assert alerts == [], [a.title for a in alerts]       # heading for your camp, but not him
    assert [a.owner for a in h.tracker.armies] == ["other"]
