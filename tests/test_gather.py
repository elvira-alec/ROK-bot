"""Auto-gather (user, 2026-11-15): balance the four resources by what a resource point gives,
pick the lowest, count each troop's load in before choosing for the next one, and only gather
from full, free points."""

import pytest

from rokwatch import gather
from rokwatch.screen import Text


def T(s, x=0, y=0):
    return Text(s, (x, y, 10, 10), 1.0)


def test_ratio_from_the_table():
    # the same at every level: stone 3/4 of food/wood, gold 1/3
    from rokwatch.farms import FULL
    for lv in (2, 3, 4, 5, 6):
        assert FULL["stone"][lv] / FULL["food"][lv] == pytest.approx(gather.RATIO["stone"])
        assert FULL["gold"][lv] / FULL["food"][lv] == pytest.approx(gather.RATIO["gold"])
        assert FULL["wood"][lv] == FULL["food"][lv]


@pytest.mark.parametrize("s,v", [("1.0M", 1_000_000), ("776.1K", 776_100), ("974.1K", 974_100),
                                 ("209.9K", 209_900), ("34,295", 34_295), ("468", 468), ("abc", None)])
def test_parse_amount(s, v):
    assert gather.parse_amount(s) == v


def test_lowest_by_what_points_give():
    # the user's storage on 2026-11-15: gold 209.9k is really the lowest (x3 = 630k food)
    st = {"food": 1_000_000, "wood": 776_100, "stone": 974_100, "gold": 209_900}
    assert gather.pick_kinds(st, 1) == ["gold"]
    # 300k gold counts like 900k food: then wood (776k) is lower
    st2 = {"food": 1_000_000, "wood": 776_100, "stone": 974_100, "gold": 300_000}
    assert gather.pick_kinds(st2, 1) == ["wood"]


def test_second_troop_counts_the_first_in():
    st = {"food": 600_000, "wood": 700_000, "stone": 900_000, "gold": 400_000}   # gold = 1.2M
    # food lowest; after one food trip (+450k) wood is lowest
    assert gather.pick_kinds(st, 2, load=450_000) == ["food", "wood"]
    # with a tiny load, both go to food
    assert gather.pick_kinds(st, 2, load=10_000) == ["food", "food"]


def test_read_point_full_and_free():
    texts = [T("Resource Point"), T("X:184Y:400"), T("Level4 Logging Camp"), T("Reserves"),
             T("630,000"), T("Gatherer", 0, 50), T("None", 100, 50), T("Owner"), T("[SRTR]"), T("GATHER")]
    p = gather.read_point(texts)
    assert p == {"xy": (184, 400), "kind": "wood", "level": 4, "reserves": 630_000,
                 "gatherer": None, "full": True}


def test_read_point_half_mined_and_taken():
    texts = [T("X:189 Y:401"), T("Level 4LoggingCamp"), T("Reserves"), T("138,843"),
             T("Gatherer", 0, 50), T("[ABC]Someone", 100, 50)]
    p = gather.read_point(texts)
    assert p["kind"] == "wood" and p["level"] == 4 and not p["full"] and p["gatherer"] == "[ABC]Someone"


def test_barbarians_and_empty_land_are_not_points():
    assert gather.read_point([T("Barbarians"), T("X:186Y:397"), T("Lvl22BarbarianBeast")])["kind"] == "other"
    assert gather.read_point([T("Map"), T("X:173Y:380"), T("Unoccupied"), T("Owner")])["kind"] == "other"


def test_candidates_best_first(tmp_path):
    b = gather.PointBook(tmp_path / "b.json")
    for xy, lv, full, who in [((180, 380), 4, True, None), ((175, 378), 6, True, None),
                              ((176, 379), 6, False, None), ((174, 377), 5, True, "x"),
                              ((200, 420), 6, True, None)]:
        b.note({"xy": xy, "kind": "gold", "level": lv, "reserves": 1, "gatherer": who, "full": full})
    c = [xy for xy, _ in b.candidates("gold", (173, 378), 12)]
    assert c == [(175, 378), (180, 380)]            # half-mined, taken and too-far ones left out
    b.blacklist((175, 378))
    assert [xy for xy, _ in b.candidates("gold", (173, 378), 12)] == [(180, 380)]


def test_never_presses_march_without_new_troop(monkeypatch):
    # live 2026-11-15: with 4/4 troops out, GATHER shows the queue with a GATHERING troop
    # preselected and a 'March' button - pressing it would pull that troop off its point
    clicks = []
    g = type("G", (), {})()
    g.b = type("B", (), {"click": lambda self, x, y: clicks.append((x, y)),
                         "press": lambda self, k: clicks.append(k)})()
    g.close_popups = lambda: clicks.append("close")
    a = gather.AutoGather.__new__(gather.AutoGather)
    a.game, a.load = g, 450_000
    asked = []

    def find(rx, region=None, timeout=3.0):
        asked.append(rx)
        return (None, None) if "new" in rx else (T("March", 500, 500), None)
    monkeypatch.setattr(a, "_find_text", find)
    assert a._dispatch() == "no troop"
    assert clicks == ["close"] and len(asked) == 1          # March never looked for, never pressed


def test_watcher_pauses_auto_gather():
    a = gather.AutoGather.__new__(gather.AutoGather)
    a.paused_until, a._at, a.every = 0.0, 0.0, 60
    import time as _t
    assert a.due(_t.time())
    a.pause(10)
    assert not a.due(_t.time() + 300) and a.due(_t.time() + 601)


def test_storage_with_a_missed_number_is_read_again(monkeypatch):
    # live: the gold number was missed and the gems (468) slid into gold's place ('0k')
    import numpy as np
    from rokwatch import screen
    f = np.zeros((1024, 1821, 3), np.uint8)
    bar = [T("1.0M", 900), T("776.1K", 1000), T("1.1M", 1100), T("468", 1300)]
    monkeypatch.setattr(screen, "read_text", lambda frame, region=None, strip=False: bar)
    assert gather.read_storage(f) is None
    bar.insert(3, T("209.9K", 1200))
    assert gather.read_storage(f) == {"food": 1_000_000, "wood": 776_100, "stone": 1_100_000, "gold": 209_900}


# ------------------------------------------------------------- reading points at the searching zoom

import json as _json
from pathlib import Path as _Path
import cv2 as _cv2

_R = _Path(__file__).parent / "real_screens" / "resources"


def test_icons_and_levels_read_on_live_views():
    # every point labelled from its own box (type + level) is read right from the map alone
    from rokwatch import screen
    import math
    labels = _json.loads((_R / "labels.json").read_text())
    n = 0
    for i, l in enumerate(labels):
        if i in (6, 11):                      # a gem and a barbarian fort matched by distance
            continue
        f = _cv2.imread(str(_R / f"view_{l['view']}.jpg"))
        if screen.gather_badge_on(f, l["mark"]):
            continue                          # someone gathering there: its badge covers the icon
        pts = [p for p in screen.find_resource_points(f) if math.dist(p["mark"], l["mark"]) < 8]
        assert pts and pts[0]["kind"] == l["kind"] and pts[0]["level"] == l["level"], (i, l, pts)
        n += 1
    assert n >= 12


def test_night_and_taken():
    from rokwatch import screen
    f = _cv2.imread(str(_R / "night_altar.png"))
    got = {p["mark"]: (p["kind"], p["level"], p["taken"]) for p in screen.find_resource_points(f)}
    assert got[(317, 135)] == ("food", 4, False)
    assert got[(1552, 825)] == ("gold", 6, True)           # your troop's green badge on it


def test_border_line_is_not_a_badge():
    # the blue territory border passing above an icon marked free points as 'taken'
    from rokwatch import screen
    labels = _json.loads((_R / "labels.json").read_text())
    for i in (3, 7):
        l = labels[i]
        assert not screen.gather_badge_on(_cv2.imread(str(_R / f"view_{l['view']}.jpg")), l["mark"])
    l = labels[0]                                            # your green badge on the gold 6
    assert screen.gather_badge_on(_cv2.imread(str(_R / f"view_{l['view']}.jpg")), l["mark"])


def test_barbarians_are_not_resource_points():
    from rokwatch import screen
    for v in range(9):
        f = _cv2.imread(str(_R / f"view_{v}.jpg"))
        for p in screen.find_resource_points(f):
            assert p["kind"] in ("food", "wood", "stone", "gold", "gems")
