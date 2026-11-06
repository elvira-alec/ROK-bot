"""The game inside a cloud-gaming page (menus around it, game in a frame)."""

import copy
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright")
pytest.importorskip("rapidocr_onnxruntime")

from rokwatch.browser import GameBrowser  # noqa: E402
from rokwatch.commands import make_game  # noqa: E402
from rokwatch.patrol import Patrol, Site  # noqa: E402
from test_end_to_end import BASE_CFG, EXE  # noqa: E402

SITE = (Path(__file__).resolve().parent / "fake_game/cloud_site.html").as_uri()


def open_site(tmp_path, size, hash_=""):
    gb = GameBrowser(SITE + hash_, str(tmp_path / "p"), size, headless=True, executable_path=EXE)
    gb.__enter__()
    gb.page.wait_for_selector("iframe#game")
    for _ in range(50):
        frames = [f for f in gb.page.frames if f.url.endswith(("rok.html", "rok.html" + hash_))]
        if frames:
            break
        time.sleep(0.1)
    frames[0].wait_for_function("window.sim && sim.ready()")
    time.sleep(0.3)
    return gb


def test_finds_game_picture_and_patrols(tmp_path):
    gb = open_site(tmp_path, (1700, 800), "#march=enemy,camp,white")
    try:
        area, clipped = gb.find_game_area()
        assert area == [150, 90, 1434, 660] and not clipped
        cfg = copy.deepcopy(BASE_CFG)
        cfg["patrol"]["settle_seconds"] = 0.5
        game = make_game(gb, cfg)
        assert game.area == [150, 90, 1434, 660]
        assert game.look().camera == (4183, 209, 418)
        sites = [Site("his city", 162, 374, "enemy"), Site("logging camp", 205, 424, "mine")]
        p = Patrol(game, sites, cfg["patrol"], str(tmp_path / "a.csv"), str(tmp_path / "d"))
        titles = [a.title for a in p.visit(sites[0])]
        assert titles == ["He has troops moving"], titles   # jumped there and saw his march
        titles = [a.title for a in p.visit(sites[1])]
        assert titles == ["MARCH COMING AT YOUR SPOT"], titles
    finally:
        gb.__exit__(None, None, None)


def test_warns_when_window_too_small(tmp_path):
    gb = open_site(tmp_path, (1300, 600))
    try:
        area, clipped = gb.find_game_area()
        assert clipped and area == [150, 90, 1150, 510]
    finally:
        gb.__exit__(None, None, None)
