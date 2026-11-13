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
    assert tr.update([], 2) == []                  # not seen once: searched for again first...
    assert [e[0] for e in tr.update([], 3)] == ["all_home"]   # ...still not there: home
    # a line from his city to your camp: alert at once, direction or not
    ev = tr.update([seen((165, 378), (CITY, (205, 424)))], 3)
    assert [e[0] for e in ev] == ["army_out"] and ev[0][2]["target"] == "logging camp"
    assert tr.update([seen((170, 384), (CITY, (205, 424)))], 4) == []          # no repeat
    # an army that was at his city and now walks away from it: "sent out"
    tr2 = Tracker(CITY, SITES)
    tr2.update([seen((163, 375))], 0)
    ev = tr2.update([seen((156, 367), (CITY, (120, 330)))], 1)
    assert [e[0] for e in ev] == ["army_out"] and ev[0][2]["target"] is None


def test_tracker_scouts_are_not_troops():
    tr = Tracker(CITY, SITES)
    s0 = ArmySeen((163, 375), False, None, 0, kind="scout")
    tr.match([s0], 0)
    assert tr.events() == []
    # his scout heading for your camp
    ev = tr.update([ArmySeen((168, 381), True, (CITY, (205, 424)), 1, kind="scout")], 1)
    assert [e[0] for e in ev] == ["scout_at"] and ev[0][2]["target"] == "logging camp"
    # a scout doesn't make "all home" / "pulling back" happen
    tr2 = Tracker(CITY, SITES)
    tr2.update([ArmySeen((150, 390), False, None, 0, kind="scout"),
                ArmySeen((180, 360), False, None, 0, kind="scout")], 0)
    assert tr2.update([], 1) == []


def test_tracker_picks_the_spot_it_is_really_aimed_at():
    # your city and camp are close together: the alert must name the one it heads for
    tr = Tracker(CITY, SITES)
    ev = tr.update([ArmySeen((168, 381), True, (CITY, (205, 424)), 0)], 0)
    assert ev[0][2]["target"] == "logging camp", ev
    tr2 = Tracker(CITY, SITES)
    ev = tr2.update([ArmySeen((168, 380), True, (CITY, (209, 418)), 0)], 0)
    assert ev[0][2]["target"] == "my city", ev
    # line cut off by the screen edge: judged by direction
    tr3 = Tracker(CITY, SITES)
    ev = tr3.update([ArmySeen((168, 381), True, (CITY, (183, 398)), 0)], 0)
    assert ev[0][2]["target"] == "logging camp", ev


def test_tracker_moving_close_without_targeting():
    tr = Tracker(CITY, SITES)
    # his army marching from out in the field to a point ~14 tiles from your camp
    ev = tr.update([ArmySeen((180, 410), True, ((175, 405), (190, 425)), 0)], 0)
    assert [e[0] for e in ev] == ["army_close"] and ev[0][2]["target"] == "logging camp", ev
    assert tr.update([ArmySeen((182, 412), True, ((175, 405), (190, 425)), 1)], 1) == []   # once
    # far away: nothing
    tr2 = Tracker(CITY, SITES)
    assert tr2.update([ArmySeen((120, 330), True, ((110, 320), (130, 340)), 0)], 0) == []


def test_tracker_pulling_back_even_if_lines_missed():
    tr = Tracker(CITY, SITES)
    tr.update([ArmySeen((150, 390), False, None, 0), ArmySeen((180, 360), False, None, 0)], 0)
    # next sweep: both clearly closer to his city, but no march line seen under them
    ev = tr.update([ArmySeen((154, 385), False, None, 1), ArmySeen((176, 365), False, None, 1)], 1)
    assert [e[0] for e in ev] == ["pulling_back"], ev


# ---------------------------------------------------------------- on the test page

@pytest.fixture
def game(tmp_path):
    with GameBrowser(PAGE, str(tmp_path / "p"), (1434, 660), headless=True,
                     executable_path=EXE) as gb:
        gb.page.wait_for_function("window.sim && sim.ready()")
        time.sleep(0.3)
        yield Game(gb, settle=0.4)


def hunter(game, tmp_path, radius=20, method="close", track=0):
    cfg = {"hunt": {"player_name": NAME, "city": list(CITY), "radius": radius, "axes_close": AXES,
                    "method": method, "checks_per_sweep": 4, "track_my_gathering_minutes": track},
           "patrol": {"axes": AXES}}     # the test page draws every zoom at the same scale
    return Hunter(game, cfg, SITES, str(tmp_path / "his.csv"))


def js(game, expr):
    return game.b.page.evaluate(expr)


def test_finds_his_armies_with_map_coordinates(game, tmp_path):
    js(game, f"sim.farmer('{NAME}', [155, 368]); sim.farmer('{NAME}', [172, 371]);"
             "sim.farmer('[XX]Someone Else', [165, 380])")
    h = hunter(game, tmp_path)
    seen, stops, *_ = h.sweep()
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
    titles = [a.title for a in h.round()] + [a.title for a in h.round()]   # confirmed on the 2nd look
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
    seen, stops, *_ = h.sweep()
    assert stops > 1
    assert any(math.dist(s.tile, (130, 400)) < 1.5 for s in seen), [s.tile for s in seen]



# ---------------------------------------------------------------- zoomed-out sweep + zoom in to check names

def test_zoomed_out_finds_diamonds_and_checks_names(game, tmp_path):
    js(game, f"sim.farmer('{NAME}', [155, 368]); sim.farmer('{NAME}', [172, 371]);"
             "sim.farmer('[XX]Someone Else', [166, 384])")
    h = hunter(game, tmp_path, method="zoomed_out")
    assert h.round() == []
    owners = sorted((a.owner or "?", round(a.tile[0]), round(a.tile[1])) for a in h.tracker.armies)
    # his two must be identified; the other player's army must never count as his
    # ("other" when its name is read, unknown if not - on GitHub's machine the test
    # page's label for it isn't readable, while the real game uses its own font)
    assert [o[0] for o in owners if o[0] == "him"] == ["him", "him"], (owners, h.seen_text)
    other = [o for o in owners if o[1:] == (166, 384)]
    assert other and other[0][0] in ("other", "?"), (owners, h.seen_text)
    his = [a for a in h.tracker.summary() if a["his"]]
    assert len(his) == 2
    assert js(game, "sim.state.zoom") == 1       # back to the zoomed-out view afterwards
    # second sweep: already identified, no zooming in again
    h.round()
    # identified armies aren't zoomed in on again (an unread one may be retried, up to 3 times)
    assert all(a.checks == 1 for a in h.tracker.armies if a.owner)
    assert all(a.checks <= 3 for a in h.tracker.armies)


def test_zoomed_out_full_story(game, tmp_path):
    js(game, f"sim.farmer('{NAME}', [155, 368]); sim.farmer('{NAME}', [172, 371])")
    h = hunter(game, tmp_path, method="zoomed_out")
    assert h.round() == []
    first = [(a.owner, a.tile, a.moving) for a in h.tracker.armies]
    js(game, "sim.sendFarmer(0, [162, 374], 0.004); sim.sendFarmer(0, [162, 374], 0.004)")
    time.sleep(1)
    titles = []
    for _ in range(3):      # a slow machine may need another sweep to see both moving
        titles += [a.title for a in h.round()]
        if "He's pulling his troops back" in titles:
            break
    debug = (first, [(a.owner, a.tile, a.moving, a.direction, a.ends) for a in h.tracker.armies])
    assert "He's pulling his troops back" in titles, (titles, debug)
    js(game, "sim.clear()")
    assert [a.title for a in h.round()] + [a.title for a in h.round()] == ["All his troops are home"]
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



def test_zoomed_out_his_scout_coming(game, tmp_path):
    """Scouting is only alerted when the game itself shows it (red eye / red border),
    not from a scout's march line; the scout is still seen and listed."""
    js(game, f"sim.march('enemy', 'camp', 'white', 0.15, 0.002, '{NAME}', 'scout')")
    h = hunter(game, tmp_path, method="zoomed_out")
    alerts = h.round()
    assert not [a.title for a in alerts if "SCOUT" in a.title], [a.title for a in alerts]
    assert [a.kind for a in h.tracker.armies] == ["scout"]


def test_tracks_your_gathering_troops_and_warns_when_he_moves_close(game, tmp_path):
    """Your troops gather at camp / stone / farm on the test page. The hunter reads where
    they are by itself; then his army marches to a point near (not onto) the farm."""
    cfg_sites = []                                    # nothing typed in by hand
    h = Hunter(game, {"hunt": {"player_name": NAME, "city": list(CITY), "radius": 30,
                               "axes_close": AXES, "method": "zoomed_out", "checks_per_sweep": 4,
                               "track_my_gathering_minutes": 10},
                      "patrol": {"axes": AXES}}, cfg_sites, str(tmp_path / "h.csv"))
    js(game, f"sim.march([165, 385], [195, 436], 'white', 0.3, 0.001, '{NAME}')")   # ends ~15 from camp
    alerts = h.round()
    spots = [pos for _, pos in h.tracker.my_sites]
    assert sorted(spots) == sorted([(205, 424), (200, 410), (215, 430)]), spots
    titles = [a.title for a in alerts]
    assert "HIS ARMY IS MOVING CLOSE TO YOU" in titles, titles
    msg = alerts[titles.index("HIS ARMY IS MOVING CLOSE TO YOU")].message
    assert "205,424" in msg, msg              # names your troop's spot it found by itself


def test_sweeps_then_follows_his_armies_then_watches_his_city(game, tmp_path):
    """First round searches the whole area; then it only visits his known armies (+ his
    city), keeping the ones it didn't look at; with none out it just watches his city."""
    js(game, f"sim.farmer('{NAME}', [155, 368]); sim.farmer('{NAME}', [180, 392])")
    h = hunter(game, tmp_path, method="zoomed_out")
    h.round()
    assert h.mode == "sweep"
    ids = sorted(a.id for a in h.tracker.armies if a.owner == "him")
    assert len(ids) == 2
    clicks = js(game, "sim.state.clicks.length")
    t0 = time.time()
    h.round()
    assert h.mode == "track"
    assert sorted(a.id for a in h.tracker.armies if a.owner == "him") == ids   # same armies, still known
    took = time.time() - t0
    js(game, "sim.clear()")
    h.round()
    h.round()
    assert not [a for a in h.tracker.armies if a.owner == "him"]
    assert h.plan(time.time()) == "base"
    h._last_full = 1                                                        # long ago
    assert h.plan(time.time()) == "sweep"
    print("track round took", round(took, 1), "s;", js(game, "sim.state.clicks.length") - clicks, "clicks")


def test_red_eye_means_scouted_red_swords_mean_attacked():
    import cv2
    from rokwatch import screen
    real = Path(__file__).resolve().parent / "real_screens"
    for n in ("scouted_my_city", "scouted_his_city", "scouted_close_red_marker"):
        assert screen.danger_icons(cv2.imread(str(real / f"{n}.png"))) == {"scouted"}, n
    # crossed swords (also bottom right): you're being attacked
    for n in ("icons/attacked_sword_1", "icons/attacked_sword_2", "attack_red_dashes_close",
              "battle_close_red_lines"):
        assert screen.danger_icons(cv2.imread(str(real / f"{n}.png"))) == {"attacked"}, n
    for n in ("own_city_gathering", "march_simple_view", "his_city_swords_simple", "recall_panel"):
        assert screen.danger_icons(cv2.imread(str(real / f"{n}.png"))) == set(), n
