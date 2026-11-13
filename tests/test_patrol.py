"""Patrol, coordinate jumps and recall, against the real-UI test page (tests/fake_game/rok.html)."""

import time
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("playwright")
pytest.importorskip("rapidocr_onnxruntime")

from rokwatch.browser import GameBrowser  # noqa: E402
from rokwatch.game import Game  # noqa: E402
from rokwatch.patrol import Patrol, Site, activity_report, learn_axes  # noqa: E402
from test_end_to_end import EXE  # noqa: E402

PAGE = (Path(__file__).resolve().parent / "fake_game/rok.html").as_uri()
SITES = [Site("his city", 162, 374, "enemy"), Site("logging camp", 205, 424, "mine"),
         Site("stone", 200, 410, "mine")]


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    with GameBrowser(PAGE, str(tmp_path_factory.mktemp("p")), (1434, 660), headless=True,
                     executable_path=EXE) as gb:
        yield gb


@pytest.fixture
def game(browser):
    browser.page.reload()
    browser.page.wait_for_function("window.sim && sim.ready()")
    time.sleep(0.3)
    return Game(browser, settle=0.4)


def js(game, expr):
    return game.b.page.evaluate(expr)


def test_reads_camera_and_jumps(game):
    assert game.look().camera == (4183, 209, 418)
    assert game.goto(162, 374)
    assert js(game, "[sim.state.cam.x, sim.state.cam.y]") == [162, 374]
    assert js(game, "sim.state.dialog") is None  # search box closed again
    assert js(game, "sim.state.typed") == "162374"
    assert not js(game, "sim.state.starClicks")       # never the favourites star


@pytest.mark.parametrize("simple", [False, True])
def test_jump_taps_magnifier_not_star(game, simple):
    """The star sits right after the magnifier: tapping it opens Favorites instead."""
    if simple:
        js(game, "sim.state.zoom = 1; sim.state.simple = true")
        time.sleep(0.3)
    from rokwatch import screen
    v = game.look()
    bar = screen.camera_coords(v.frame, v.texts)
    x, y = screen.search_button(v.frame, bar[3])
    mg = js(game, "(() => { const cb = sim.state.simple ? {x:75,w:160} : {x:285,w:160};"
                  " return [cb.x + cb.w + 12, 18]; })()")
    assert abs(x - mg[0]) <= 6 and abs(y - mg[1]) <= 6, ((x, y), mg)
    assert game.goto(176, 380)
    assert not js(game, "sim.state.starClicks")


def test_zoom_out_to_patrol_level(game):
    assert game.zoom() == "close"
    assert game.zoom_to_patrol_level()
    assert js(game, "sim.state.zoom") == 1


def test_zoom_back_in_when_too_far(game):
    js(game, "sim.state.zoom = 2; sim.state.simple = true")
    time.sleep(0.2)
    assert game.zoom() == "far"
    assert game.zoom_to_patrol_level()
    assert js(game, "sim.state.zoom") == 1


def test_troop_panel_and_location(game):
    used, total, slots = game.troops()
    assert (used, total) == (3, 3)
    gathering = [s for s in slots if s.state == "gathering"]
    assert len(gathering) == 3
    assert game.troop_location(gathering[1]) == (200, 410)
    game.close_popups()


def test_recall_only_threatened(game):
    report = game.recall_all(near=[(205, 424)])
    assert report == ["troop 1: recalled"], report
    assert js(game, "sim.state.troops.map(t => t.recalled)") == [True, False, False]


def test_recall_everything(game):
    report = game.recall_all()
    assert report == [f"troop {i}: recalled" for i in (1, 2, 3)], report
    assert js(game, "sim.state.troops.every(t => t.recalled)")


def test_learn_axes(game):
    m = learn_axes(game, px=240)
    truth = np.linalg.inv(np.array([[24, 10], [-8, 20]], float))
    assert m is not None and np.allclose(m, truth, atol=0.012), (m, truth)


def test_patrol_his_march_without_axes(game, tmp_path):
    js(game, "sim.march('enemy', 'camp', 'white', 0.2)")
    p = Patrol(game, SITES, {}, str(tmp_path / "a.csv"), str(tmp_path / "d"))
    alerts = p.visit(SITES[0])
    assert [a.title for a in alerts] == ["He has troops moving"], [a.title for a in alerts]
    assert "march" in (tmp_path / "a.csv").read_text()
    # same march next visit: no repeat
    assert [a for a in p.visit(SITES[0]) if "moving" in a.title] == []


def test_patrol_his_march_toward_you_recalls(game, tmp_path):
    js(game, "sim.march('enemy', 'camp', 'white', 0.2)")
    axes = np.linalg.inv(np.array([[24, 10], [-8, 20]], float)).tolist()
    p = Patrol(game, SITES, {"axes": axes, "auto_recall": True}, str(tmp_path / "a.csv"),
               str(tmp_path / "d"))
    alerts = p.visit(SITES[0])
    titles = [a.title for a in alerts]
    assert "ENEMY MARCH HEADING YOUR WAY" in titles, titles
    assert "logging camp" in alerts[titles.index("ENEMY MARCH HEADING YOUR WAY")].message
    rec = [a for a in alerts if a.title == "Auto-recall"]
    assert rec and "recalled" in rec[0].message, titles
    assert js(game, "sim.state.troops.map(t => t.recalled)") == [True, False, False]
    # the bot recalled it itself, so no "stopped gathering" warning about its own action
    later = p.visit(SITES[1])
    assert "One of your troops stopped gathering" not in [a.title for a in later]


def test_patrol_incoming_at_your_spot(game, tmp_path):
    js(game, "sim.march('enemy', 'camp', 'white', 0.5); sim.march('city', 'stone', 'green', 0.5)")
    p = Patrol(game, SITES, {}, str(tmp_path / "a.csv"), str(tmp_path / "d"))
    camp = p.visit(SITES[1])
    assert [a.title for a in camp] == ["MARCH COMING AT YOUR SPOT"], [a.title for a in camp]
    stone = p.visit(SITES[2])  # only your own green march goes there
    assert stone == [], [a.title for a in stone]


def test_patrol_quiet(game, tmp_path):
    js(game, "sim.march([100, 300], [140, 320], 'white', 0.5)")  # someone far away
    p = Patrol(game, SITES, {}, str(tmp_path / "a.csv"), str(tmp_path / "d"))
    alerts = p.round()
    assert alerts == [], [a.title for a in alerts]


def test_activity_report(tmp_path):
    path = tmp_path / "a.csv"
    path.write_text("time_utc,site,kind,event,detail\n"
                    "2026-11-05 04:10:00,his city,enemy,march,x\n"
                    "2026-11-05 04:40:00,his city,enemy,march,x\n"
                    "2026-11-06 21:05:00,his city,enemy,march,x\n"
                    "2026-11-06 21:06:00,camp,mine,incoming,x\n")
    rep = activity_report(str(path))
    assert "3 over 2 day(s)" in rep and "Most active around: 04:00" in rep
    assert "  21:00" in rep and "  13:00" not in rep  # only hours he was seen


def test_your_attack_on_him_is_not_his_march(game, tmp_path):
    # green = you / alliance. A green line into his city means YOU are attacking HIM.
    js(game, "sim.march('city', 'enemy', 'green', 0.6)")
    p = Patrol(game, SITES, {}, str(tmp_path / "a.csv"), str(tmp_path / "d"))
    alerts = p.visit(SITES[0])
    assert alerts == [], [a.title for a in alerts]
    assert ",march," not in (tmp_path / "a.csv").read_text()


def test_tells_leaving_from_returning(game, tmp_path):
    js(game, "sim.march('enemy', 'camp', 'white', 0.08, 0.012)")     # going out
    js(game, "sim.march('enemy', [120, 400], 'white', 0.3, -0.012)")  # coming back home
    p = Patrol(game, SITES, {}, str(tmp_path / "a.csv"), str(tmp_path / "d"))
    first = p.visit(SITES[0])
    assert [a.title for a in first].count("He has troops moving") == 2, [a.title for a in first]
    time.sleep(4)
    second = p.visit(SITES[0])
    titles = [a.title for a in second]
    assert titles == ["He sent troops OUT"], titles
    log = (tmp_path / "a.csv").read_text()
    assert ",leaving," in log and ",returning," in log, log
    # once worked out, it isn't reported again
    time.sleep(2)
    assert p.visit(SITES[0]) == []


def test_zoom_bounces_between_two_levels(game):
    """Set up at the searching zoom; then only ever goes between it and the close zoom."""
    js(game, "sim.state.zoom = 1; sim.state.simple = true")
    time.sleep(0.2)
    gap, ok = game.calibrate_zoom()
    assert ok and gap == 1 and js(game, "sim.state.zoom") == 1      # 1 notch to the close view here
    assert game.to_close() and js(game, "sim.state.zoom") == 0
    assert game.to_sweep() and js(game, "sim.state.zoom") == 1
    js(game, "sim.state.zoom = 2; sim.state.simple = true")   # someone zoomed too far out
    game._at = None
    assert game.to_sweep() and js(game, "sim.state.zoom") == 1   # back in, not further out


def test_pan_to_drags_instead_of_typing(game):
    axes = learn_axes(game)
    assert axes
    typed = js(game, "sim.state.typed || ''")
    assert game.pan_to(220, 425, axes)
    cam = js(game, "[sim.state.cam.x, sim.state.cam.y]")
    assert abs(cam[0] - 220) <= 2 and abs(cam[1] - 425) <= 2, cam
    assert js(game, "sim.state.typed || ''") == typed      # no coordinates typed
    assert game.pan_to(100, 300, axes)                     # far away: types them in
    assert js(game, "[sim.state.cam.x, sim.state.cam.y]") == [100, 300]
