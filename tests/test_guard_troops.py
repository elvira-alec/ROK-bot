"""The user (2026-11-14): 'imagine one of his armies slipped past you' - every couple of
minutes look at each of your gathering troops at the searching zoom (no zooming in) and alert
on any army marching at it (their choice: whoever it is, fastest). And: an army known to be his
is never zoomed in on again - except after it crossed another moving army (the tracker can
swap the two), then both are read again."""

import time

import pytest

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



def _prime(h, ends, before):
    """The march was seen at `before` at the previous look (~10 s ago): its direction is
    known (one picture can't tell which end of the route it's heading to)."""
    import time as _t
    key = tuple(sorted(((round(ends[0][0]), round(ends[0][1])), (round(ends[1][0]), round(ends[1][1])))))
    h.__dict__.setdefault("_marches", {})[key] = (tuple(before), _t.time() - 10, None)


def seen(tile, ends=None, kind="army"):
    return ArmySeen(tile, ends is not None, ends, time.time(), kind=kind)


def test_army_marching_at_a_gathering_troop_alerts():
    h, g = _hunter({(200, 410): [seen((206, 404), ((212, 398), (200.8, 410.6)))]})
    _prime(h, ((212, 398), (200.8, 410.6)), (208, 402))
    al = h.guard_my_troops()
    assert g.panned == [(200, 410), (220, 430)]          # gathering ones only, not the marching one
    assert len(al) == 1 and al[0].urgent and "YOUR TROOP 1" in al[0].title and "HIS" not in al[0].title


def test_known_his_when_the_line_comes_from_his_city():
    h, _ = _hunter({(220, 430): [seen((200, 410), ((163, 375), (219, 429)))]})
    _prime(h, ((163, 375), (219, 429)), (196, 406))
    al = h.guard_my_troops()
    assert len(al) == 1 and al[0].title.startswith("HIS ARMY") and "TROOP 3" in al[0].title


def test_marches_going_elsewhere_or_standing_still_dont_alert():
    h, _ = _hunter({(200, 410): [seen((206, 404), ((212, 398), (196, 420))),   # passing by, ends 10 away
                                 seen((203, 412)),                            # parked / gathering
                                 seen((200.5, 410.3), ((215, 400), (200.4, 410.2)))]})  # arrived
    assert h.guard_my_troops() == []


def test_red_diamond_alerts_only_on_a_line_ending_at_the_troop():
    # a red diamond alone 6 tiles off raised a false alarm (live 2026-11-15): only one on a
    # march line that ends at your troop counts
    h, _ = _hunter({(220, 430): [seen((224, 427), kind="hostile")]})
    assert h.guard_my_troops() == []
    h, _ = _hunter({(220, 430): [seen((226, 424), ((232, 418), (220.5, 429.6)), kind="hostile")]})
    al = h.guard_my_troops()
    assert len(al) == 1 and "MARCHING AGAINST YOUR TROOP 3" in al[0].title


def test_scouts_never_alert_from_the_guard():
    # scouting alerts only come from the game's red eye / border (the user's rule)
    h, _ = _hunter({(200, 410): [seen((206, 404), ((212, 398), (200.8, 410.6)), kind="scout")]})
    assert h.guard_my_troops() == []

def test_auto_recall_that_troop_once():
    import types
    h, g = _hunter({(200, 410): [seen((206, 404), ((212, 398), (200, 411)))]}, recall=True)
    _prime(h, ((212, 398), (200, 411)), (208, 402))
    g.troops = lambda: (3, 4, [types.SimpleNamespace(index=i, state="gathering") for i in range(3)])
    g.recall = lambda slot: True
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
    _prime(h, ((222, 410), (210.5, 421.5)), (218, 414))
    assert h.ready() == [] and h.city == (210, 422) and h.guard_every == 1
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
    assert h.new_targets == [{"name": "[XYZ]Bully", "x": 240, "y": 390}]
    assert "Saved as a target" in al[0].message


def test_attacker_saved_as_target_keeps_the_watched_one_first(tmp_path):
    from rokwatch import config
    from rokwatch.worker import Worker
    p = tmp_path / "config.yaml"
    p.write_text("{}\n")
    config.save_fields({"targets": [{"name": "[P83L]JONH WICK", "x": 162, "y": 374}]}, str(p))
    w = Worker.__new__(Worker)
    w.cfg_path = str(p)
    w._save_targets([{"name": "[XYZ]Bully", "x": 240, "y": 390}])
    w._save_targets([{"name": "[XYZ]Bully", "x": 241, "y": 391}])       # found again: updated
    t = config.load(str(p))["targets"]
    assert [x["name"] for x in t] == ["[P83L]JONH WICK", "[XYZ]Bully"] and t[1]["x"] == 241


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


def test_spots_checked_by_the_troop_list_taps_are_not_visited_again():
    # the user saw each troop visited twice at the start: tapped in the troop list (camera on
    # it), then again by typed coordinates. A spot checked in the last minute is skipped.
    h, g = _hunter({})
    h._checked_at["troop-1"] = time.time()
    h.guard_my_troops()
    assert g.panned == [(220, 430)]                 # troop 3 only; troop 1 was just checked


def test_map_scale_remembered_between_starts(tmp_path, monkeypatch):
    # 'it moves right and back, twice, every time I start it': a scale measured in the last
    # few hours with the same zoom setup is reused - no drags
    import json as _j
    from rokwatch import hunter as hm
    monkeypatch.setattr(hm, "SCALE_CACHE", tmp_path / "scale.json")
    h, g = _hunter({})
    drags = []
    g.measure_scale = lambda *a: drags.append(1) or 0.0085
    h._first_scale()
    assert len(drags) == 2                                  # measured (twice, must agree)
    h2, g2 = _hunter({})
    g2.measure_scale = lambda *a: drags.append(1) or 0.0085
    h2._first_scale()
    assert len(drags) == 2 and h2._measure_ref == pytest.approx(0.0085)   # reused: no drags
    c = _j.loads((tmp_path / "scale.json").read_text())
    c["t"] -= 7 * 3600                                       # too old: measured again
    (tmp_path / "scale.json").write_text(_j.dumps(c))
    h3, g3 = _hunter({})
    g3.measure_scale = lambda *a: drags.append(1) or 0.0085
    h3._first_scale()
    assert len(drags) == 4


def test_zoom_slip_noticed_from_moves_not_by_dragging():
    # no more right-and-back drags every 5 min (the user saw them at his base every cycle):
    # the scale from the bot's own moves says when the zoom slipped about a notch
    import numpy as np
    h, _ = _hunter({})
    cal = np.array(h._axes_cal, float)
    rng = np.random.default_rng(1)
    for f, slipped in ((0.88, False), (1.42, True)):
        h._scale_samples, h._scale_new, h._zoom_drift = [], 0, None
        for _ in range(20):
            d = rng.uniform(-400, 400, 2)
            h._learn_scale(d, -(cal * f) @ d)
        assert bool(h._zoom_drift) is slipped, (f, h._zoom_drift)


def test_guard_round_includes_your_base():
    # 'is guard not checking my base anymore? It should do it as part of the rounds'
    h, g = _guard_hunter({})
    h.mode = "guard"
    h.guard_sites()
    assert g.panned == [(210, 422)]


def test_recall_goes_to_the_threatened_troop():
    # 'no troops recalled because none were under threat' - it searched near the spot and found
    # nothing; now the threatened troop itself is selected and recalled
    import types
    h, g = _hunter({(200, 410): [seen((206, 404), ((212, 398), (200.8, 410.6)))]}, recall=True)
    _prime(h, ((212, 398), (200.8, 410.6)), (208, 402))
    recalled = []
    slots = [types.SimpleNamespace(index=i, state="gathering") for i in range(3)]
    g.troops = lambda: (3, 4, slots)
    g.recall = lambda slot: recalled.append(slot.index) or True
    al = h.guard_my_troops()
    assert recalled == [0] and any(a.title == "Auto-recall" for a in al)


def _two_looks(h, monkeypatch, first, second, pos):
    """_coming_at on two looks ~10 s apart (the direction comes from the movement)."""
    from rokwatch import hunter as hm
    t = [1000.0]
    monkeypatch.setattr(hm.time, "time", lambda: t[0])
    r1 = h._coming_at(first, pos)
    t[0] += 10
    return r1, h._coming_at(second, pos)


def test_march_passing_close_alerts_after_its_direction_is_known(monkeypatch):
    # 'see in what direction it's moving and whether it's going to get close' (user)
    h, _ = _hunter({})
    ends = ((180, 400), (220, 420))                         # passes ~2 tiles from 200,412
    r1, r2 = _two_looks(h, monkeypatch, seen((184, 402), ends), seen((188, 404), ends), (200, 412))
    assert r1 is None and r2 == "close"


def test_parallel_or_wide_march_does_not_alert(monkeypatch):
    h, _ = _hunter({})
    ends = ((180, 390), (220, 390))                         # parallel, 10 tiles off
    assert _two_looks(h, monkeypatch, seen((184, 390), ends), seen((190, 390), ends), (200, 400)) == (None, None)


def test_march_leaving_your_troop_does_not_alert(monkeypatch):
    h, _ = _hunter({})
    ends = ((200, 410), (230, 430))                         # its line starts at your troop
    r1, r2 = _two_looks(h, monkeypatch, seen((206, 414), ends), seen((212, 418), ends), (200, 410))
    assert r1 is None and r2 is None                        # first look waits; then: it's leaving


def test_a_line_the_army_isnt_on_is_not_trusted():
    h, _ = _hunter({})
    assert h._coming_at(seen((195, 420), ((180, 400), (200, 411))), (200, 410)) is None


def test_badges_over_daytime_grass():
    """Daytime grass behind the troop list (hue 37-40) merged with the green badges: 3 of 4
    gathering troops read as "other" and the guard watched nothing (2026-11-15)."""
    import cv2
    import numpy as np
    from pathlib import Path
    from rokwatch import screen
    part = cv2.imread(str(Path(__file__).parent / "real_screens" / "troop_lists" / "troops_day_grass_4of4.png"))
    f = np.zeros((1024, 1821, 3), np.uint8)
    f[:, 1821 - part.shape[1]:] = part
    H, W = f.shape[:2]
    x0 = int(0.72 * W)
    used, total, slots = screen.troop_panel(f, screen.read_text(f, (x0, 0, W - x0, int(0.5 * H)), strip=True))
    assert (used, total) == (4, 4)
    assert [s.state for s in slots] == ["gathering"] * 4
    ys = [s.badge[1] for s in slots]
    assert all(60 < b - a < 90 for a, b in zip(ys, ys[1:]))


def test_counter_behind_a_barbarian_at_night():
    """'1/4' with a red barbarian icon behind the '4' at night: the counter wasn't read, "can't
    see your troop list", nothing sent with 3 troops free (2026-11-15)."""
    import cv2
    import numpy as np
    from pathlib import Path
    from rokwatch import screen
    part = cv2.imread(str(Path(__file__).parent / "real_screens" / "troop_lists" / "troops_night_1of4_barbarian.png"))
    f = np.zeros((1024, 1821, 3), np.uint8)
    f[:, 1821 - part.shape[1]:] = part
    H, W = f.shape[:2]
    x0 = int(0.72 * W)
    used, total, slots = screen.troop_panel(f, screen.read_text(f, (x0, 0, W - x0, int(0.5 * H)), strip=True))
    assert (used, total) == (1, 4) and [s.state for s in slots] == ["gathering"]
