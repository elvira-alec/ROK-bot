"""The user (2026-11-14): 'imagine one of his armies slipped past you' - every couple of
minutes look at each of your gathering troops at the searching zoom (no zooming in) and alert
on any army marching at it (their choice: whoever it is, fastest). And: an army known to be his
is never zoomed in on again - except after it crossed another moving army (the tracker can
swap the two), then both are read again."""

import time

import numpy as np

from rokwatch.hunter import Hunter, Army, ArmySeen

NAME = "[P83L]JONH WICK"
H, W = 1042, 1852


class FakeGame:
    def __init__(self, looks):
        self.looks, self.panned = looks, []
        self.zoom_cfg = {}
        self._cam = None

    def check_stop(self):
        pass

    def frame(self):
        return np.zeros((H, W, 3), np.uint8)

    def pan_to(self, x, y, axes, **k):
        self.panned.append((x, y))
        self._cam = (x, y)
        return True

    def recall_all(self, near=None):
        return ["recalled"]


class View:
    def __init__(self, cam):
        self.camera = (4183, *cam)
        self.frame = np.zeros((H, W, 3), np.uint8)


def _hunter(looks, recall=False):
    g = FakeGame(looks)
    h = Hunter(g, {"hunt": {"player_name": NAME, "city": [162, 374]},
                   "patrol": {"axes": [[0.0085, 0], [0, -0.012]], "auto_recall": recall}}, [], "nul")
    h.method = "zoomed_out"
    h.mode = "track"

    def look_spot():
        return View(g._cam), looks.get(g._cam, [])
    h.look_spot = look_spot
    h.my_troops = {0: {"index": 0, "state": "gathering", "x": 200, "y": 410},
                   1: {"index": 1, "state": "marching", "x": 190, "y": 400},
                   2: {"index": 2, "state": "gathering", "x": 220, "y": 430}}
    return h, g


def seen(tile, ends=None, kind="army"):
    return ArmySeen(tile, ends is not None, ends, time.time(), kind=kind)


def test_army_marching_at_a_gathering_troop_alerts():
    h, g = _hunter({(200, 410): [seen((206, 404), ((212, 398), (200.8, 410.6)))]})
    al = h.guard_my_troops()
    assert g.panned == [(200, 410), (220, 430)]          # gathering ones only, not the marching one
    assert len(al) == 1 and al[0].urgent and "YOUR TROOP 1" in al[0].title and "HIS" not in al[0].title


def test_known_his_when_the_line_comes_from_his_city():
    h, _ = _hunter({(220, 430): [seen((200, 410), ((163, 375), (219, 429)))]})
    al = h.guard_my_troops()
    assert len(al) == 1 and al[0].title.startswith("HIS ARMY") and "TROOP 3" in al[0].title


def test_marches_going_elsewhere_or_standing_still_dont_alert():
    h, _ = _hunter({(200, 410): [seen((206, 404), ((212, 398), (196, 420))),   # passing by, ends 10 away
                                 seen((203, 412)),                            # parked / gathering
                                 seen((200.5, 410.3), ((215, 400), (200.4, 410.2)))]})  # arrived
    assert h.guard_my_troops() == []


def test_red_diamond_near_a_troop_alerts():
    h, _ = _hunter({(220, 430): [seen((224, 427), kind="hostile")]})
    al = h.guard_my_troops()
    assert len(al) == 1 and "MARCHING AGAINST YOUR TROOP 3" in al[0].title


def test_auto_recall_that_troop_once():
    h, _ = _hunter({(200, 410): [seen((206, 404), ((212, 398), (200, 411)))]}, recall=True)
    al = h.guard_my_troops()
    assert any(a.title == "Auto-recall" for a in al)
    al2 = h.guard_my_troops()
    assert not any(a.title == "Auto-recall" for a in al2)


def test_guard_timing():
    h, _ = _hunter({})
    now = time.time()
    assert h._guard_due(now)
    h._guard_at = now
    assert not h._guard_due(now + 60) and h._guard_due(now + 121)
    h.mode = "sweep"
    assert not h._guard_due(now + 500)
    h.mode = "track"
    h.my_troops = {1: {"index": 1, "state": "marching", "x": 1, "y": 1}}
    assert not h._guard_due(now + 500)                    # nothing gathering: nothing to guard


def _army(i, tile, owner):
    a = Army(i, tile, 0.0, 0.0, moving=True)
    a.owner = owner
    return a


def test_crossing_rechecks_both_after_they_separate():
    h, _ = _hunter({})
    his, other = _army(1, (180, 390), "him"), _army(2, (182, 391), "other")
    h.tracker.armies = [his, other]
    h._check_crossings()
    assert his.owner == "him"                              # still close: nothing yet
    his.tile, other.tile = (176, 386), (187, 396)
    h._check_crossings()
    assert his.owner is None and other.owner is None and his.checks == 0


def test_no_recheck_for_armies_that_never_came_close():
    h, _ = _hunter({})
    his, other = _army(1, (180, 390), "him"), _army(2, (190, 400), "other")
    h.tracker.armies = [his, other]
    h._check_crossings()
    other.tile = (200, 410)
    h._check_crossings()
    assert his.owner == "him" and other.owner == "other"


# ---------------------------------------------------------------- guard-only mode

def _guard_hunter(looks):
    g = FakeGame(looks)
    h = Hunter(g, {"hunt": {"guard_only": True, "city": None},
                   "patrol": {"axes": [[0.0085, 0], [0, -0.012]]}}, [("my city", (210, 422))], "nul")
    h.method = "zoomed_out"

    def look_spot():
        v = View(g._cam)
        return v, [ArmySeen(**{**s.__dict__}) for s in (looks.get(g._cam) or [])] \
            if not callable(looks.get(g._cam)) else looks[g._cam]()
    h.look_spot = look_spot
    return h, g


def test_guard_mode_needs_no_target_and_watches_the_city():
    h, g = _guard_hunter({(210, 422): [seen((216, 416), ((222, 410), (210.5, 421.5)))]})
    assert h.ready() == [] and h.city == (210, 422) and h.guard_every == 60
    h.mode = "guard"
    al = h.guard_my_troops()
    assert len(al) == 1 and al[0].title == "AN ARMY IS MARCHING AT YOUR CITY"
    assert len(h.attackers) == 1                      # and it's being followed


def test_attacker_followed_home_and_base_read(monkeypatch):
    h, g = _guard_hunter({})
    h.mode = "guard"
    t0 = time.time()
    h._add_attacker(seen((216, 416), ((222, 410), (210, 422))), (210, 422), "YOUR CITY")
    a = h.attackers[0]
    # it hit and turned round: now walking away along a line ending at its home 240,390
    s_home = seen((214, 418), ((240, 390), (214, 418)))
    h._update_attacker(a, [s_home], t0 + 30)
    assert a.info["phase"] == "home" and a.dest == (240, 390)
    # next look: it's gone (into its city); the second miss = home
    h.look_spot = lambda: (View(g._cam), [])
    monkeypatch.setattr(h, "_read_base", lambda tile: ("[XYZ]Bully", None))
    assert h.follow_attackers() == []                 # first miss: give it another look
    al = h.follow_attackers()
    assert len(al) == 1 and "[XYZ]Bully" in al[0].title and "240,390" in al[0].message
    assert h.attackers == [] and h.attacker_bases[-1]["name"] == "[XYZ]Bully"


def test_attacker_still_coming_keeps_being_followed():
    h, g = _guard_hunter({})
    h._add_attacker(seen((216, 416), ((222, 410), (210, 422))), (210, 422), "YOUR CITY")
    a = h.attackers[0]
    h._update_attacker(a, [seen((213, 419), ((222, 410), (210, 422)))], time.time() + 20)
    assert a.info["phase"] == "coming" and h.attackers


# ---------------------------------------------------------------- he moved his city

def test_city_present_on_real_picture():
    import cv2
    from pathlib import Path
    f = cv2.imread(str(Path(__file__).parent / "real_screens" / "moved" / "his_old_spot_empty.png"))
    h, _ = _hunter({})
    H, W = f.shape[:2]
    assert not h.city_present(f, (W / 2, H / 2), NAME)                     # his spot: empty now
    assert h.city_present(f, (849, 830), "[Qeov]Governador237844718")    # a city that is there


def test_three_misses_means_he_moved(monkeypatch):
    h, g = _hunter({})
    monkeypatch.setattr(h, "city_present", lambda f, px, name: False)
    now = time.time()
    out = []
    for i in range(3):
        out += h._check_his_city(now + i * 121)
    assert len(out) == 1 and out[0].title == "HE MOVED HIS CITY"
    assert h.city_moved and h.guard_only


def test_city_seen_again_resets_the_count(monkeypatch):
    h, g = _hunter({})
    seq = iter([False, False, True, False, False])
    monkeypatch.setattr(h, "city_present", lambda f, px, name: next(seq))
    now = time.time()
    out = []
    for i in range(5):
        out += h._check_his_city(now + i * 121)
    assert out == [] and not h.city_moved
