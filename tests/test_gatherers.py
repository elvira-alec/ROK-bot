"""His farmers: red pickaxe badge at the sweeping zoom; name only in the resource
point's box (tap it at the close zoom). Real screenshots in real_screens/gather."""

from pathlib import Path

import cv2

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
    assert a.owner == "him" and a.tile == (164.0, 380.0)
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
    # looked there again (that area was on screen) and the farm is gone
    t.match([], 60, covered=[(170, 390, 10, 8)])
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
