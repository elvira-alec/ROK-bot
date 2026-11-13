"""His farmers: red pickaxe badge at the sweeping zoom; name only in the resource
point's box (tap it at the close zoom). Real screenshots in real_screens/gather."""

from pathlib import Path

import math
import time

import cv2
import pytest

from rokwatch import screen
from rokwatch.game import Game
from rokwatch.hunter import Army, Hunter

G = Path(__file__).resolve().parent / "real_screens" / "gather"
REAL = G.parent
NAME = "[P83L]JONH WICK"


def img(n):
    return cv2.imread(str(G / f"{n}.png"))


def test_red_pickaxe_badges():
    assert len(screen.find_gather_badges(img("sweep_red_pickaxe"))) == 1
    assert len(screen.find_gather_badges(img("close_his_camp"))) == 1
    # a red banner with a white symbol isn't one
    assert screen.find_gather_badges(cv2.imread(str(REAL / "his_army_marching_close.png"))) == []
    for n in ("own_city_gathering", "recall_panel", "scouted_my_city"):   # your own: green
        assert screen.find_gather_badges(cv2.imread(str(REAL / f"{n}.png"))) == [], n


def test_reads_gatherer_from_the_box():
    f = img("camp_popup_gatherer")
    name, xy = screen.read_gatherer(screen.read_text(f), f.shape[0])
    assert screen.name_score(name, NAME) >= 0.75 and xy == (164, 380)


class Clicky:
    """Shows his camp; after a tap near it, the resource point's box."""
    def __init__(self):
        self.frames = [img("close_his_camp"), img("camp_popup_gatherer")]
        self.i, self.clicks, self.keys, self.drags = 0, [], [], []

    def screenshot(self, area=None):
        return self.frames[self.i].copy()

    def click(self, x, y):
        self.clicks.append((x, y))
        if abs(x - 1349) < 60 and 552 < y < 640:          # on the camp, under its badge
            self.i = 1

    def press(self, k):
        self.keys.append(k)          # Esc: on the PC game this does NOT close the box

    def drag(self, x0, y0, x1, y1, steps=15):
        self.drags.append((x0, y0, x1, y1))
        if self.i == 1 and x0 < 1443:   # dragging the map beside the box closes it
            self.i = 0


def test_identify_farm_by_tapping_the_camp(tmp_path):
    b = Clicky()
    g = Game(b, settle=0)
    h = Hunter(g, {"hunt": {"player_name": NAME, "city": [162, 374], "axes_close": [[0.02, 0], [0, 0.02]]},
                   "patrol": {"axes": [[0.05, 0], [0, 0.05]]}}, [], str(tmp_path / "h.csv"))
    a = Army(1, (160.0, 383.0), 0, 0, kind="farm")
    h._identify_farm(a)
    assert a.owner == "him" and a.info["xy"] == [164, 380] and a.tile == (160.0, 383.0)
    assert b.i == 0 and b.drags and b.keys == []                  # closed by dragging, no Esc


def test_sweep_view_sees_his_farm(tmp_path):
    h = Hunter(Game(Clicky(), settle=0), {"hunt": {"player_name": NAME, "city": [162, 374]},
                                          "patrol": {"axes": [[0.012, 0.004], [-0.004, 0.012]]}},
               [], str(tmp_path / "h.csv"))
    f = img("sweep_red_pickaxe")
    v = Game(Clicky(), settle=0).look(f)
    farms = [s for s in h.read_view_out(v) if s.kind == "farm"]
    assert len(farms) == 1 and not farms[0].moving


def test_alert_when_his_farmer_leaves():
    from rokwatch.hunter import ArmySeen, Tracker
    t = Tracker((162, 374), [("camp", (205, 424))])
    t.match([ArmySeen((170.0, 390.0), False, None, 0, kind="farm")], 0)
    t.armies[0].owner = "him"
    assert t.events() == []
    # looked there again (that area was on screen) and the farm is gone: once could be a
    # missed look, so only the second time counts
    t.match([], 60, covered=[(170, 390, 10, 8)])
    assert t.events() == [] and t.armies
    t.match([], 90, covered=[(170, 390, 10, 8)])
    ev = t.events()
    assert [e[0] for e in ev] == ["farm_left"] and "170,390" in ev[0][1]
    # somewhere we didn't look: kept, no alert
    t.match([ArmySeen((150.0, 360.0), False, None, 100, kind="farm")], 100)
    t.armies[0].owner = "him"
    t.match([], 160, covered=[(200, 420, 10, 8)])
    assert t.armies and t.events() == []


def test_spanish_box_and_closing_rules():
    """The info box is found whatever the language (photos of the Spanish game)."""
    for n in ("photo_es_popup_farm.jpg", "photo_es_popup_profile.jpg", "camp_popup_gatherer.png"):
        assert screen.find_info_box(cv2.imread(str(G / n))), n
    for n in ("close_his_camp.png", "sweep_red_pickaxe.png"):
        assert screen.find_info_box(cv2.imread(str(G / n))) is None, n
    # Spanish power number ("2 032 680") means the close view, like "2,015,180"
    from rokwatch.screen import Text, is_simple_view
    import numpy as np
    f = np.zeros((1320, 2868, 3), np.uint8)
    assert not is_simple_view([Text("2 032 680", (300, 20, 200, 40), 1)], f)
    assert not is_simple_view([Text("2032680", (300, 20, 200, 40), 1)], f)
    assert is_simple_view([Text("#4183 X:165 Y:380", (300, 20, 200, 40), 1)], f)


def test_stuck_state_from_a_real_snapshot():
    """Snapshot from the game: resource box open AND the profile panel (opened by Esc).
    The box is found and read; the drag to close it lands on the open map to its
    right (away from the profile panel); the coordinate readout is hidden, so the
    profile counts as 'something covering the map' to close next."""
    f = cv2.imread(str(G / "snap_stuck_box_and_profile.jpg"))
    H, W = f.shape[:2]
    box = screen.find_info_box(f)
    assert box
    bx, by, bw, bh = box
    drag_x = int(bx - 0.12 * W) if bx > W / 2 else int(bx + bw + 0.12 * W)
    assert drag_x > bx + bw and drag_x < 0.9 * W                  # on the map, right of the box
    texts = screen.read_text(f, box)
    assert any(screen.name_score(t.text, NAME) >= 0.75 for t in texts)
    assert any(screen.parse_coords(t.text) == (None, 169, 389) or
               (screen.parse_coords(t.text) or (0, 0, 0))[1:] == (169, 389) for t in texts)
    assert screen.camera_coords(f) is None                        # profile covers the readout


def test_army_list_marks_threats_first():
    from rokwatch.hunter import ArmySeen, Tracker
    t = Tracker((162, 374), [("my city", (210, 422)), ("stone", (185, 398))])
    t.match([ArmySeen((170.0, 390.0), False, None, 0, kind="farm"),
             ArmySeen((176.0, 386.0), True, ((165.0, 377.0), (184.0, 397.0)), 0)], 0)
    for a in t.armies:
        a.owner = "him"
    t.armies[1].direction, t.armies[1].dest = "out", (184.0, 397.0)
    rows = t.summary()
    assert rows[0]["threat"] == "stone" and rows[0]["threat_how"] == "aimed"
    assert rows[1]["state"] == "gathering" and rows[1]["threat"] is None
    assert t.map_info()["city"] == [162, 374] and len(t.map_info()["mine"]) == 2


def test_badges_not_diamonds_and_found_in_your_snapshot():
    # red hostile diamonds have a white symbol too, but aren't farms
    for n in ("attack_red_diamond_simple", "scouted_toast_simple"):
        assert screen.find_gather_badges(cv2.imread(str(REAL / f"{n}.png"))) == [], n
    # his farm in the snapshot you sent (the bot's own window size)
    f = cv2.imread(str(G / "snap_stuck_box_and_profile.jpg"))
    assert len(screen.find_gather_badges(f)) == 1
    # no fake march lines along the quest panel on the left
    assert screen.find_march_lines(cv2.imread(str(G / "snap_side_panel_no_marches.jpg"))) == []


def test_his_farm_never_takes_a_neighbours_owner():
    from rokwatch.hunter import ArmySeen, Tracker
    t = Tracker((162, 374), [])
    t.match([ArmySeen((170.0, 390.0), False, None, 0, kind="farm")], 0)
    t.armies[0].owner, t.armies[0].checks = "other", 1
    # his farm 5 tiles away: a different farm, not the same one
    t.match([ArmySeen((170.0, 390.0), False, None, 60, kind="farm"),
             ArmySeen((175.0, 390.0), False, None, 60, kind="farm")], 60)
    owners = sorted((a.tile, a.owner) for a in t.armies)
    assert owners == [((170.0, 390.0), "other"), ((175.0, 390.0), None)]
    # checked without finding his name: not listed; not checked yet: listed
    assert [r["x"] for r in t.summary()] == [175]


def test_farm_details_and_arrival_time():
    from rokwatch.hunter import Army, Hunter, Tracker
    f = img("camp_popup_gatherer")
    texts = screen.read_text(f, screen.find_info_box(f))
    d = Hunter._farm_details(Hunter, texts)
    assert "Logging Camp" in d.get("what", "") and d.get("reserves") == 99863, d
    a = Army(1, (170.0, 390.0), 0, 60, moving=True)
    a.history = [(0, (160.0, 380.0)), (60, (170.0, 390.0))]
    a.dest = (184.0, 404.0)
    m = Tracker._motion(a)
    assert abs(m["speed"] - 14.1) < 0.2 and abs(m["eta_min"] - 1.4) < 0.1, m


def test_activity_log_and_report(tmp_path):
    from rokwatch import activity
    log = activity.ActivityLog(tmp_path / "a.csv", tmp_path / "w.csv")
    t0 = 1_700_000_000
    assert log.round([{"id": 1, "state": "gathering", "his": True, "x": 1, "y": 2}], t0) == []
    ch = log.round([{"id": 1, "state": "going home", "his": True, "x": 1, "y": 2},
                    {"id": 2, "state": "marching out", "his": True, "x": 5, "y": 6}], t0 + 60)
    assert (1, "gathering", "going home") in ch and (2, None, "marching out") in ch
    assert log.round([], t0 + 120) == [(1, "going home", None), (2, "marching out", None)]
    d = activity.data(actions=tmp_path / "a.csv", watch=tmp_path / "w.csv", now=t0 + 200)
    kinds = [(a[1], a[5]) for a in d["actions"]]
    # gathering -> going home happens by itself: not him being online; sending one out is
    assert kinds == [("changed", 0), ("sent_out", 1), ("army_gone", 0), ("army_gone", 0)], kinds
    assert sum(w[1] for w in d["watch"]) == 3 and d["watch"][0][2] >= 1     # 3 minutes, farms out


def test_army_on_a_line_to_his_city_is_his_without_zooming(tmp_path):
    from rokwatch.hunter import ArmySeen
    h = Hunter(Game(Clicky(), settle=0), {"hunt": {"player_name": NAME, "city": [162, 374]},
                                          "patrol": {"axes": [[0.05, 0], [0, 0.05]]}}, [], str(tmp_path / "h.csv"))
    called = []
    h.identify = lambda armies, limit=None: called.append(len(armies))
    home = ArmySeen((170.0, 385.0), True, ((163.0, 375.0), (180.0, 398.0)), 0)     # line from his city
    away = ArmySeen((200.0, 400.0), True, ((190.0, 390.0), (215.0, 420.0)), 0)     # nowhere near it
    h._check_new_here([home, away])
    assert home.owner == "him" and away.owner is None and called == [1]


def test_big_plain_diamond_is_an_army_small_one_a_scout():
    import numpy as np
    f = cv2.imread(str(REAL / "scouted_simple_4.png"))
    kinds = [k for _, k in screen.find_map_icons(f)]
    assert kinds.count("scout") == 2                       # real scouts stay scouts
    # the same scout drawn 1.6x bigger (army-sized, no symbol): an army
    (x, y), _ = screen.find_map_icons(f)[0]
    crop = f[y - 30:y + 30, x - 30:x + 30]
    big = cv2.resize(crop, None, fx=1.6, fy=1.6, interpolation=cv2.INTER_CUBIC)
    g = f.copy()
    g[700:700 + big.shape[0], 400:400 + big.shape[1]] = big
    found = {(int(c[0]) // 10, int(c[1]) // 10): k for c, k in screen.find_map_icons(g)}
    near = [k for (cx, cy), k in found.items() if abs(cx * 10 - 448) < 30 and abs(cy * 10 - 748) < 30]
    assert near == ["army"], found


def test_farm_out_of_view_is_not_gone(tmp_path):
    """Your snapshot: camera at 192,363, his farms at 170,389 / 172,375 - not on screen,
    so not 'pulled back'."""
    from rokwatch.hunter import ArmySeen
    h = Hunter(Game(Clicky(), settle=0), {"hunt": {"player_name": NAME, "city": [162, 374]},
                                          "patrol": {"axes": [[0.012, 0.004], [-0.004, 0.012]]}},
               [], str(tmp_path / "h.csv"))
    vis = h._on_screen((192, 363), (1042, 1852))
    assert vis((192, 363)) and not vis((170, 389)) and not vis((172, 375))
    t = h.tracker
    t.match([ArmySeen((170.0, 389.0), False, None, 0, kind="farm")], 0)
    t.armies[0].owner = "him"
    for k in range(3):
        t.match([], 60 * (k + 1), covered=[vis])
    assert t.armies and t.events() == []


def test_after_the_first_sweep_it_only_follows_his_armies(tmp_path):
    import time as _t
    from rokwatch.hunter import ArmySeen
    h = Hunter(Game(Clicky(), settle=0), {"hunt": {"player_name": NAME, "city": [162, 374]},
                                          "patrol": {"axes": [[0.012, 0.004], [-0.004, 0.012]]}},
               [], str(tmp_path / "h.csv"))
    h.game.camera_fast = lambda: (162, 374)
    now = _t.time()
    assert h.plan(now) == "sweep"                          # first: search the area
    h._last_full = now
    t = h.tracker
    t.match([ArmySeen((200.0, 410.0), False, None, now, kind="farm"),
             ArmySeen((150.0, 360.0), True, ((140.0, 350.0), (160.0, 372.0)), now),
             ArmySeen((180.0, 380.0), True, ((175.0, 375.0), (190.0, 390.0)), now, kind="scout")], now)
    for a in t.armies:
        a.owner = "him"
    assert h.plan(now + 600) == "track"                    # no new sweep after 10 minutes
    pts = h.track_points(now + 600, (1042, 1852))
    assert (162, 374) in pts and len(pts) == 3             # his city + farm + army, not the scout
    assert all(abs(p[0] - 180) > 3 for p in pts)
    h._need_sweep = True                                   # lost one of them away from home
    assert h.plan(now + 600) == "sweep"
    h._need_sweep = False
    assert h.plan(now + 31 * 60) == "sweep"                # safety check every 30 minutes
    # scouts aren't zoomed in on
    called = []
    h.zoom_close = lambda: called.append(1) or True
    assert h.identify([a for a in t.armies if a.kind == "scout"]) == 0 and called == []


def test_marching_army_followed_along_its_line(tmp_path):
    from rokwatch.hunter import ArmySeen, Tracker, predict
    t = Tracker((162, 374), [("my city", (210, 422))])
    line = ((165.0, 377.0), (205.0, 417.0))                    # from his city toward you
    t.match([ArmySeen((170.0, 382.0), True, line, 0)], 0)
    t.match([ArmySeen((173.0, 385.0), True, ((173.0, 385.0), (205.0, 417.0)), 30)], 30)
    a = t.armies[0]
    # 30 s later it should be ~3 tiles further along the line (6 tiles/min), toward its end
    px, py = predict(a, 60)
    assert 175 < px < 177.5 and 387 < py < 389.5, (px, py)
    far = predict(a, 10_000)                                        # never past the end of its line
    assert math.dist((173, 385), far) <= math.dist((173, 385), (205, 417)) + 0.5
    # seen again 2 minutes later, 12 tiles on: still the same army (wider tolerance)
    t.match([ArmySeen((186.0, 398.0), True, ((186.0, 398.0), (205.0, 417.0)), 150)], 150)
    assert len(t.armies) == 1 and t.armies[0].id == a.id
    # not where it should be: kept, and the next look also searches further along the line
    h = Hunter(Game(Clicky(), settle=0), {"hunt": {"player_name": NAME, "city": [162, 374]},
                                          "patrol": {"axes": [[0.012, 0.004], [-0.004, 0.012]]}},
               [], str(tmp_path / "h.csv"))
    h.game.camera_fast = lambda: (186, 398)
    h.tracker = t
    a.owner = "him"
    t.match([], 200, covered=[lambda p: True])
    assert t.armies and t.armies[0].missed == 1
    pts = h.track_points(200, (1042, 1852))
    assert len(pts) >= 3 and any(math.dist(p, (205, 417)) < 4 for p in pts), pts
    t.match([], 260, covered=[lambda p: True])
    assert t.armies == [] and [g.id for g in t.gone] == [a.id]   # given up: lost (a new sweep)


def test_marching_army_near_the_screen_edge_keeps_its_line():
    """Lines aren't drawn near the edge of the view: an army seen there without one is
    'unsure', and a march we already knew keeps its line and destination."""
    from rokwatch.hunter import ArmySeen, Tracker
    import numpy as np
    # edge icon with no line -> unsure (detect works in pixels: fake one icon each)
    found = [((60, 300), "army", "edge"), ((700, 330), "army", None)]
    h = Hunter(Game(Clicky(), settle=0), {"hunt": {"player_name": NAME, "city": [162, 374]},
                                          "patrol": {"axes": [[0.012, 0.004], [-0.004, 0.012]]}},
               [], str(G.parent / "unused.csv"))
    seen = h.to_map(found, (180, 390), (660, 1434))
    assert seen[0].unsure and not seen[0].moving and not seen[1].unsure
    t = Tracker((162, 374), [])
    line = ((165.0, 377.0), (205.0, 417.0))
    t.match([ArmySeen((170.0, 382.0), True, line, 0)], 0)
    t.match([ArmySeen((173.0, 385.0), True, ((173.0, 385.0), (205.0, 417.0)), 30)], 30)
    t.match([ArmySeen((176.0, 388.0), False, None, 60, unsure=True)], 60)    # near the edge now
    a = t.armies[0]
    assert len(t.armies) == 1 and a.moving and a.ends and a.tile == (176.0, 388.0)
    t2 = Tracker((162, 374), [])
    t2.match([ArmySeen((190.0, 400.0), False, None, 0, unsure=True)], 0)        # new, near the edge
    assert t2.summary()[0]["state"] == "moving? (near the edge)"


def test_farm_rate_time_left_and_his_load(tmp_path):
    from rokwatch import farms
    info = {"readings": [[0, 1_200_000], [300, 1_185_000]], "from_start": True}   # 3,000 a minute
    assert farms.rate_per_min(info["readings"]) == 3000
    assert abs(farms.minutes_left(info, 300) - 395) < 1                      # point runs out
    book = farms.FarmBook(tmp_path / "cap.json")
    book.learn({"what": "Level 6 Cropland", "reserves": 1_236_669})
    book.learn({"what": "Level 6 Cropland", "reserves": 1_100_000})
    assert book.capacity("Level 6 Cropland") == 1_260_000                    # from the table
    assert book.capacity("level 9 something") is None
    # one of his farms watched from the start ended after 20 min: his load ~60,000
    book.farm_ended({"readings": [[0, 900_000], [600, 870_000]]}, True, 1200)
    assert book.typical_load() == 60_000
    # a new farm: 15,000 gathered so far at 3,000/min -> full in ~15 min
    assert abs(farms.minutes_to_full(info, 300, 60_000) - 15) < 0.1
    assert farms.FarmBook(tmp_path / "cap.json").typical_load() == 60_000      # remembered


def test_zoom_kept_at_the_searching_scale():
    from test_zoom_measure import ZoomSim
    sim = ZoomSim(8)
    g = Game(sim, settle=0)
    g.camera_fast = lambda full=True: sim.camera()
    ss = sim.tpp()
    sim.level = sim.shown = 6                                # drifted 2 notches too far out
    g.keep_scale(ss)
    assert sim.level == 7                                    # one notch per check, no more
    g.keep_scale(ss)
    assert sim.level == 8
    r = g.keep_scale(ss)
    assert sim.level == 8 and 0.94 <= r <= 1.06, (sim.level, r)   # right: left alone
    # a misread coordinate (e.g. 4368 for 368) must not change the zoom
    real = g.camera_fast
    reads = iter([(200, 400), (230, 4368), (200, 400)] * 2)
    g.camera_fast = lambda full=True: next(reads)
    assert g.keep_scale(ss) is None and sim.level == 8
    g.camera_fast = real


def test_how_long_hes_been_gathering_from_the_full_amount():
    from rokwatch import farms
    # your Spanish screenshot: Level 6 Cropland with 1,236,669 left; 5 min later 1,221,669
    info = {"what": "Campo de Cultivo de Nivel 6", "readings": [[0, 1_236_669], [300, 1_221_669]]}
    assert farms.full_amount(info["what"]) == 1_260_000
    assert farms.gathered(info, 300) == 38_331                         # taken so far
    assert abs(farms.minutes_gathering(info, 300) - 12.8) < 0.1        # at 3,000 a minute
    assert abs(farms.minutes_to_full(info, 300, 60_000) - 7.2) < 0.1   # with a 60,000 load
    assert farms.parse_kind("Level 3 Stone Deposit") == ("stone", 3)
    assert farms.full_amount("Level 3 Stone Deposit") == 354_375
    assert farms.full_amount("Level 2 Gold Deposit") == 105_000


def _bare_hunter(tmp_path):
    h = Hunter(Game(Clicky(), settle=0), {"hunt": {"player_name": NAME, "city": [162, 374], "radius": 25},
                                          "patrol": {"axes": [[0.012, 0.004], [-0.004, 0.012]]}},
               [], str(tmp_path / "h.csv"))
    return h


def test_sweep_carries_on_after_a_bad_spot_and_resumes(tmp_path):
    from rokwatch.game import View
    import numpy as np
    h = _bare_hunter(tmp_path)
    frame = np.zeros((1042, 1852, 3), np.uint8)
    h.game.frame = lambda: frame
    visits = []

    def pan(x, y, axes, **k):
        visits.append((x, y))
        if len(visits) == 2:
            raise RuntimeError("something went wrong here")
        return True
    h.game.pan_to = pan
    h.game.close_popups = lambda: True
    h.zoom_out = lambda: True
    h.look_spot = lambda: (View(frame, [], (4183, 160, 370), True), [])
    calls = {"n": 0}
    stop = lambda: calls.__setitem__("n", calls["n"] + 1) or calls["n"] > 3   # stop after 3 spots
    seen, visited, covered, finished = h.sweep(stop)
    total = h._sweep_total
    assert not finished and visited == 2 and len(h._sweep_left) == total - 3   # bad spot skipped, not restarted
    assert h.plan(time.time()) == "sweep"                                      # carries on next round
    seen, visited, covered, finished = h.sweep()
    assert finished and len(visits) == total and h._sweep_left == []


def test_drifted_farm_estimate_is_still_the_same_farm_and_his_city_is_never_checked(tmp_path):
    from rokwatch.hunter import ArmySeen, Tracker
    t = Tracker((162, 374), [])
    t.match([ArmySeen((170.0, 390.0), False, None, 0, kind="farm")], 0)
    t.armies[0].owner, t.armies[0].info = "him", {"xy": [171, 391]}
    t.match([ArmySeen((174.5, 392.0), False, None, 60, kind="farm")], 60)    # estimate drifted ~4.5
    assert len(t.armies) == 1 and t.armies[0].owner == "him"
    h = _bare_hunter(tmp_path)
    at_city = ArmySeen((162.5, 374.5), False, None, 0, kind="army")
    farm = ArmySeen((180.0, 380.0), False, None, 0, kind="farm")
    assert h._unknown([at_city, farm]) == [farm]


def test_after_checking_it_looks_again_and_checks_what_else_is_there(tmp_path):
    from rokwatch.game import View
    from rokwatch.hunter import ArmySeen
    import numpy as np
    h = _bare_hunter(tmp_path)
    frame = np.zeros((1042, 1852, 3), np.uint8)
    checked = []

    def identify(armies, limit=None):
        for a in armies:
            a.owner = "him"
            checked.append(a.tile)
        return len(armies)
    h.identify = identify
    more = [ArmySeen((175.0, 360.0), False, None, 0, kind="farm"), ArmySeen((178.0, 356.0), False, None, 0, kind="farm")]
    looks = iter([(View(frame, [], (4183, 175, 365), True), more),
                  (View(frame, [], (4183, 175, 365), True), [])])
    h.look_spot = lambda: next(looks)
    first = ArmySeen((176.0, 372.0), False, None, 0, kind="farm")
    extra = h._check_new_here([first])
    assert checked == [(176.0, 372.0), (175.0, 360.0), (178.0, 356.0)]     # the two above too
    assert len(extra) == 2
