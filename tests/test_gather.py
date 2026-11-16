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


def test_a_free_troop_is_sent_without_waiting(tmp_path):
    # live: a troop came home and it took ~4 min (5 rounds) to send it out again
    import time as _t
    a = gather.AutoGather.__new__(gather.AutoGather)
    a.book = gather.PointBook(tmp_path / "b.json")
    a.book.troops, a.paused_until, a.every = 4, 0.0, 60
    now = _t.time()
    a._at = now - 20
    three = {i: {"index": i, "state": "gathering"} for i in (1, 2, 3)}
    four = {**three, 4: {"index": 4, "state": "gathering"}}
    assert a.due(now, three) and not a.due(now, four)
    a._retry_at = now + 30                                 # found it free but sent nothing
    assert not a.due(now, three)
    # a troop heading home: the points are looked over before it arrives
    a._scan, scans = (0.0, []), []
    a._scan_area = lambda stop=None: scans.append(1) or []
    assert not a.prescan(three) and not scans
    assert a.prescan({**three, 4: {"index": 4, "state": "returning"}}) and scans == [1]
    assert not a.prescan({**three, 4: {"index": 4, "state": "returning"}})   # fresh: once


def test_troops_already_out_count_before_picking(tmp_path):
    # live 2026-11-16: 3 troops gathering gold, the 4th was sent to gold too
    import time as _t
    a = gather.AutoGather.__new__(gather.AutoGather)
    a.book = gather.PointBook(tmp_path / "b.json")
    a._scan, a.load = (0.0, []), 450_000
    a.pending = [{"xy": (161, 389), "kind": "gold", "at": _t.time()},      # marching there
                 {"xy": (157, 364), "kind": "gold", "at": _t.time()}]      # already gathering
    troops = {1: {"index": 1, "state": "gathering", "x": 157, "y": 364, "kind": "gold"},
              2: {"index": 2, "state": "gathering", "x": 177, "y": 374, "kind": "gold"}}
    assert a.coming(troops)["gold"] == 3 * 150_000
    storage = {"food": 5_400_000, "wood": 3_700_000, "stone": 4_200_000, "gold": 820_000}
    b = gather.balanced(storage)
    for k, v in a.coming(troops).items():
        b[k] += v / gather.RATIO[k]
    assert min(b, key=b.get) == "wood"


def test_storage_with_a_lost_suffix_is_not_believed(monkeypatch, tmp_path):
    # live 2026-11-16: 1M+ food and wood read as a few units and 4 troops went for them
    import numpy as np
    from rokwatch import screen
    f = np.zeros((1024, 1821, 3), np.uint8)
    bar = [T("1.7", 900), T("1.2M", 1000), T("1.0M", 1100), T("2.1M", 1200), T("458", 1300)]
    monkeypatch.setattr(screen, "read_text", lambda frame, region=None, strip=False: bar)
    assert gather.read_storage(f) is None                    # a decimal without its M
    bar[0:1] = [T("1.7", 880), T("M", 920)]                 # the M read on its own
    assert gather.read_storage(f)["food"] == 1_700_000
    # map level tags under the bar (live: food 16, wood 25)
    bar[:] = [T("16", 850), T("5.3M", 900), T("25", 950), T("3.6M", 1000), T("4.2M", 1100),
              T("20", 1150), T("1.0M", 1200), T("458", 1300)]
    assert gather.read_storage(f) == {"food": 5_300_000, "wood": 3_600_000, "stone": 4_200_000, "gold": 1_000_000}
    # a 1M+ resource suddenly reading tiny: not trusted until a second zoom-in agrees
    a = gather.AutoGather.__new__(gather.AutoGather)
    a.book = gather.PointBook(tmp_path / "b.json")
    a.storage = None
    a.book.storage = {"food": 1_700_000, "wood": 1_200_000, "stone": 1_000_000, "gold": 2_100_000}
    odd = {"food": 17, "wood": 12, "stone": 1_000_000, "gold": 2_100_000}
    assert not a._believable(odd)
    assert a._believable(odd)                                # read the same again: real
    assert a._believable({"food": 1_600_000, "wood": 1_200_000, "stone": 900_000, "gold": 2_000_000})


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


def test_threat_seen_while_gathering_stops_it_and_alerts():
    # the watcher must not be blind while auto-gather has the game: every scan picture gets
    # the guard's check, and a threat stops auto-gather (user, 2026-11-15)
    import numpy as np
    from rokwatch.watcher import Alert

    class Hn:
        dangers, warned, _guard_alerts = set(), False, []

        def guard_frame(self, frame, cam):
            return [Alert("guard-troop-1", "AN ARMY IS MARCHING AT YOUR TROOP 1", "x", urgent=True)]
    a = gather.AutoGather.__new__(gather.AutoGather)
    a.hunter, a.threat = Hn(), False
    assert a._watch(np.zeros((10, 10, 3), np.uint8), (180, 380)) is True and a.threat
    assert a.hunter._guard_alerts[0].title.startswith("AN ARMY")


def test_quiet_picture_lets_it_carry_on():
    import numpy as np

    class Hn:
        dangers, warned, _guard_alerts = set(), False, []

        def guard_frame(self, frame, cam):
            return []
    a = gather.AutoGather.__new__(gather.AutoGather)
    a.hunter, a.threat = Hn(), False
    assert a._watch(np.zeros((10, 10, 3), np.uint8), (180, 380)) is False
    Hn.dangers = {"attacked"}                    # the red swords showing: stop too
    assert a._watch(np.zeros((10, 10, 3), np.uint8), (180, 380)) is True


def test_march_button_by_colour():
    # pressed straight away (a text read took ~5 s in all) - and never the queue's 'March'
    # for a troop that's already out
    from rokwatch.gather import find_march_button
    d = _Path(__file__).parent / "real_screens" / "gather_send"
    f = _cv2.resize(_cv2.imread(str(d / "new_troop_window.jpg")), (1821, 1024))
    x, y = find_march_button(f)
    assert abs(x - 1196) < 20 and abs(y - 780) < 20
    for n in ("queue_new_troop", "queue_full_march"):
        assert find_march_button(_cv2.resize(_cv2.imread(str(d / f"{n}.jpg")), (1821, 1024))) is None


def test_a_scout_alerts_but_does_not_stop_auto_gather():
    import numpy as np
    from rokwatch.watcher import Alert

    class Hn:
        dangers, warned, _guard_alerts = set(), False, []

        def guard_frame(self, frame, cam):
            return [Alert("guard-troop-2", "A SCOUT IS MARCHING AT YOUR TROOP 2", "x", urgent=True)]
    a = gather.AutoGather.__new__(gather.AutoGather)
    a.hunter, a.threat = Hn(), False
    assert a._watch(np.zeros((10, 10, 3), np.uint8), (180, 380)) is False
    assert a.hunter._guard_alerts[0].title.startswith("A SCOUT")          # still alerted


def test_only_intact_points():
    """Only a point whose reserves are its level's full amount is sent to (the user: "make
    sure that it's intact... if it doesn't match, you don't do it")."""
    a = gather.AutoGather.__new__(gather.AutoGather)

    class G:
        def close_popups(self):
            pass
    a.game = G()
    a.book = gather.PointBook.__new__(gather.PointBook)
    a.book.points = {}
    for reserves in (295_115, 944_000):
        p = {"xy": (177, 390), "kind": "stone", "level": 6, "reserves": reserves, "gatherer": None,
             "full": False}
        assert a._check_and_gather(p, []) == "not full"


def test_caravan_is_not_an_army():
    # a grey diamond with a blue diamond badge under it: a caravan (user's screenshot)
    from rokwatch import screen
    f = _cv2.imread(str(_Path(__file__).parent / "real_screens" / "caravan" / "caravan_on_canvas.png"))
    assert screen.find_map_icons(f) == []


def test_scan_level_read_twice(monkeypatch):
    """One picture read a level 3 wood as 2 (live 2026-11-15): a disagreement is settled by a
    third picture; no majority -> unknown (still a candidate, never dropped)."""
    import numpy as np
    from rokwatch import gather as G
    reads = iter([[{"mark": (100, 100), "kind": "wood", "level": 2, "taken": False},
                   {"mark": (300, 300), "kind": "stone", "level": 6, "taken": False},
                   {"mark": (500, 500), "kind": "food", "level": 4, "taken": False}],
                  [{"mark": (101, 100), "kind": "wood", "level": 3, "taken": False},
                   {"mark": (300, 301), "kind": "stone", "level": 6, "taken": False},
                   {"mark": (500, 500), "kind": "food", "level": 5, "taken": False}]])
    monkeypatch.setattr(G.screen, "find_resource_points", lambda f: next(reads))
    monkeypatch.setattr(G.screen, "plate_level", lambda f, m: {(100, 100): 3, (500, 500): 1}[tuple(m)])
    monkeypatch.setattr(G.time, "sleep", lambda s: None)

    class Fake:
        def frame(self):
            return np.zeros((1024, 1821, 3), np.uint8)
    pts = G.AutoGather._settled_points(Fake(), np.zeros((1024, 1821, 3), np.uint8))
    assert [(p["kind"], p["level"]) for p in pts] == [("wood", 3), ("stone", 6), ("food", None)]


def test_cut_off_none_is_free():
    """'Gatherer: Non' (a cut-off None) is a free point, not taken (live 2026-11-15)."""
    from rokwatch import gather as G
    from types import SimpleNamespace as T
    import rokwatch.screen as S
    texts = [T(text="Level 4 Stone Deposit"), T(text="X:189 Y:399"), T(text="472,500")]
    orig = S.read_gatherer
    try:
        S.read_gatherer = lambda t, n: ("Non", None)
        p = G.read_point(texts)
    finally:
        S.read_gatherer = orig
    assert p["kind"] == "stone" and p["gatherer"] is None


def test_highest_level_first_past_radius():
    """A level 4 just past the radius goes before a level 3 inside it (the user: "always the
    highest ones")."""
    import time as _t
    from rokwatch import gather as G
    a = G.AutoGather.__new__(G.AutoGather)
    a.centre, a.radius = (173, 378), 10
    a._scan = (_t.time(), [{"kind": "stone", "level": 3, "taken": False, "tile": (175, 380)},
                           {"kind": "stone", "level": 4, "taken": False, "tile": (176, 366)}])
    a._scan_taken, a._checked = [], set()
    a.book = G.PointBook.__new__(G.PointBook)
    a.book.black, a.book.points = {}, {}
    assert [lv for _, lv in a.scan("stone")] == [4, 3]


def test_fullness_slider():
    """Panel slider 'Fullness required': 100 % = intact only (default)."""
    p = {"kind": "stone", "level": 6, "reserves": 944_000}
    assert not gather.full_enough(p, 100)
    assert gather.full_enough({**p, "reserves": 945_000}, 100)
    assert gather.full_enough({**p, "reserves": 295_115}, 30)
    assert not gather.full_enough({**p, "reserves": 295_115}, 35)
    assert not gather.full_enough({**p, "reserves": None}, 10)
    a = gather.AutoGather.__new__(gather.AutoGather)
    assert a.min_full == 100
    gather.LIVE["min_full"] = 50
    try:
        assert a.min_full == 50
    finally:
        gather.LIVE.clear()


def test_badge_colour_green_on_own_troop():
    """Your own gatherer's pickaxe is green (live 2026-11-15, level 3 wood at 187,380)."""
    import cv2 as _cv
    from rokwatch import screen
    f = _cv.resize(_cv.imread(str(_R.parent / "gather" / "green_pickaxe_l3_wood.jpg")), (1821, 1024))
    pts = [p for p in screen.find_resource_points(f)
           if p["kind"] == "wood" and abs(p["mark"][0] - 598 * 1821 / 1200) < 40
           and abs(p["mark"][1] - 350 * 1024 / 675) < 40]
    assert pts and screen.gather_badge_on(f, pts[0]["mark"]) == "green"


def _sender(results):
    import time as _t
    a = gather.AutoGather.__new__(gather.AutoGather)
    a.threat, a.sent, a._scan_taken, a._checked, a.pending = False, [], [], set(), []
    a.centre, a.radius = (173, 378), 12
    a.book = gather.PointBook.__new__(gather.PointBook)
    a.book.points, a.book.black = {}, {}
    a.book.save = lambda: None
    a.say = lambda m: None
    a._scan = (_t.time(), [])
    calls = []

    def est(tile, kind, level):
        calls.append((tuple(round(v) for v in tile), level))
        res, xy, box_level = results.pop(0)
        a._last_box = {"level": box_level, "xy": xy}
        return res, xy
    a.try_estimate = est
    return a, calls


def test_lower_level_requeued(monkeypatch):
    """The scan read a level 3 wood as 5 (a troop went to a 3 with 4s around, 2026-11-15):
    the box says 3 -> it goes back in the queue as a 3, the 4 is tried first."""
    a, calls = _sender([("lower", (187, 380), 3), ("sent", (180, 386), 4)])
    monkeypatch.setattr(a, "scan", lambda kind, stop=None: [((187.5, 379.6), 5), ((180.3, 385.5), 4),
                                                             ((175.2, 390.6), 3)])
    assert a.send_one("wood") == (180, 386)
    assert calls == [((188, 380), 5), ((180, 386), 4)]
    assert a.pending and a.pending[0]["xy"] == (180, 386) and a.sent[-1]["level"] == 4


def test_box_level_checked():
    a, _ = _sender([])

    class G:
        def close_popups(self):
            pass
    a.game = G()
    p = {"xy": (187, 380), "kind": "wood", "level": 3, "reserves": 472_500, "gatherer": None}
    assert a._check_and_gather(p, [], 5) == "lower"


class _H:
    MOVING_STATES = ("marching", "returning", "fighting", "stopped")

    def __init__(self, troops):
        self.my_troops = troops
        self.recalled = []

    def _recall_troop(self, i):
        self.recalled.append(i)
        return [f"troop {i + 1}: recalled"]


def test_pending_someone_else_got_there_first(monkeypatch):
    """Troop on its way, a red pickaxe appears on the point: ours is recalled."""
    a, _ = _sender([])
    a.note_sent((187, 380), "wood", 5)
    h = _H({0: {"index": 0, "state": "gathering", "x": 179, "y": 370},
            2: {"index": 2, "state": "marching", "x": 192, "y": 410}})
    monkeypatch.setattr(a, "_badge_at", lambda h, pd, jump=False: "none")
    assert a.check_pending(h) == [] and a.pending[0]["troop"] == 2
    monkeypatch.setattr(a, "_badge_at", lambda h, pd, jump=False: "red")
    out = a.check_pending(h)
    assert h.recalled == [2] and not a.pending and "got there first" in out[0]


def test_pending_done_when_gathering_and_stranded_recalled(monkeypatch):
    a, _ = _sender([])
    monkeypatch.setattr(a, "_badge_at", lambda h, pd, jump=False: "none")
    a.note_sent((187, 380), "wood", 5)
    h = _H({2: {"index": 2, "state": "gathering", "x": 187, "y": 380}})
    a.check_pending(h)
    assert not a.pending                                  # ours is gathering: done
    a.note_sent((176, 366), "stone", 4)
    h = _H({1: {"index": 1, "state": "other", "x": 176, "y": 366}})
    assert a.check_pending(h) == [] and a.pending        # once: could be arriving
    out = a.check_pending(h)
    assert h.recalled == [1] and "stranded" in out[0] and not a.pending


def test_no_second_troop_to_a_point_on_the_way():
    import time as _t
    a, _ = _sender([])
    a._scan = (_t.time(), [{"kind": "wood", "level": 5, "taken": False, "tile": (187.4, 380.3)},
                           {"kind": "wood", "level": 4, "taken": False, "tile": (180.3, 385.5)}])
    a.note_sent((187, 380), "wood", 5)
    assert [lv for _, lv in a.scan("wood")] == [4]


def test_misread_plate_is_kept(monkeypatch, tmp_path):
    import numpy as np
    a, _ = _sender([("lower", (187, 380), 3), ("gone", None, None)])
    monkeypatch.setattr(a, "MISS_DIR", tmp_path)
    crop = np.zeros((60, 80, 3), np.uint8)
    a._scan = (a._scan[0], [{"kind": "wood", "level": 5, "taken": False, "tile": (187.5, 379.6),
                             "mark": (900, 500), "crop": (crop, (860, 470), (1024, 1821))}])
    monkeypatch.setattr(a, "scan", lambda kind, stop=None: [((187.5, 379.6), 5)])
    a.send_one("wood")
    assert any(p.name.endswith("_read5_true3.png") for p in tmp_path.iterdir())


def test_box_below_a_waiting_candidate_is_not_sent():
    """An unread plate went first; its box said 3 with level 4s still waiting: not sent."""
    a, calls = _sender([("lower", (182, 374), 3), ("sent", (176, 364), 4)])
    a.scan = lambda kind, stop=None: [((182.1, 373.7), None), ((176.0, 363.9), 4), ((150.5, 391.4), 3)]
    assert a.send_one("stone") == (176, 364)
    assert calls[0] == ((182, 374), None)


def test_need_from_waiting_candidates():
    a, _ = _sender([])

    class G:
        def close_popups(self):
            pass
    a.game, a._need = G(), 4
    p = {"xy": (182, 374), "kind": "stone", "level": 3, "reserves": 354_375, "gatherer": None}
    assert a._check_and_gather(p, [], None) == "lower"


def test_pending_every_point_watched_nearest_first(monkeypatch):
    """All four points troops are on their way to are checked every pass, nearest first; one
    not seen is tried again with a typed-in jump (2026-11-15: only 2 of 4 were checked)."""
    a, _ = _sender([])
    for xy, k in (((178, 388), "wood"), ((176, 364), "stone"), ((181, 382), "food"), ((191, 370), "gold")):
        a.note_sent(xy, k, 4)
    h = _H({i: {"index": i, "state": "marching", "x": 205, "y": 420} for i in range(4)})

    class G:
        _cam = (195, 372)
    a.game = G()
    calls = []

    def badge(h, pd, jump=False):
        calls.append((pd["xy"], jump))
        return None if (pd["xy"] == (181, 382) and not jump) else "none"
    monkeypatch.setattr(a, "_badge_at", badge)
    assert a.check_pending(h) == [] and len(a.pending) == 4
    assert [c[0] for c in calls] == [(191, 370), (181, 382), (181, 382), (178, 388), (176, 364)]
    assert calls[2] == ((181, 382), True)
