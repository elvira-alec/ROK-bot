"""Regression: his troops shown as "moved" when they hadn't (and the alerts that came
with it). Each case is a pattern from the user's real his_armies.csv / his_activity.csv
(2026-11-13):

  A. two of his farms side by side (175,366 and 180,365): their ids swapped between
     rounds, so on the panel both "moved" 4-5 tiles;
  B. an army parked at 179,360 shown "marching" for 2+ minutes: someone else's march
     line ran past it;
  C. an army by his city flipped marching -> going home -> marching every few seconds
     (zoomed-out estimate wobble), logging false "recalled" / "sent out" actions.
"""

import math

from rokwatch.hunter import Tracker, ArmySeen

CITY = (162, 374)
SITES = [("my city", (210, 422))]


def farm(x, y, t):
    return ArmySeen(tile=(x, y), moving=False, ends=None, seen=t, kind="farm")


def army(x, y, t, ends=None):
    return ArmySeen(tile=(x, y), moving=ends is not None, ends=ends, seen=t, kind="army")


def _by_id(tr):
    return {a.id: a for a in tr.armies}


# ---------------------------------------------------------------- A. farm id swaps

def test_two_farms_side_by_side_keep_their_ids():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    tr.update([farm(175, 366, t), farm(180, 365, t)], t)
    ids = {round(a.tile[0]): a.id for a in tr.armies}
    # wobbly estimates: the left one read a bit right, the right one a bit left (the old
    # greedy pairing gave the left sighting to whichever was processed first)
    # (greedy pairing took them left to right: 178 grabbed the right farm, then 179.5 -
    # the right farm itself - was left with the left one, 4.5 tiles away: swapped)
    for k, (l, r) in enumerate([(178, 179.5), (178.5, 180), (177.5, 179.5), (178, 180)]):
        t += 15
        tr.update([farm(r, 365, t), farm(l, 365, t)], t)
        by = _by_id(tr)
        assert len(by) == 2
        # no swap: the left one's id stays on the left farm, the right one's on the right
        assert by[ids[175]].tile[0] < by[ids[180]].tile[0], (k, by)
        assert by[ids[180]].tile[0] >= 179.5, (k, by[ids[180]].tile)


def test_farm_position_does_not_wobble_on_the_map():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    for x in (165, 167, 164, 166, 168, 165, 164):
        tr.update([farm(x, 377, t)], t)
        t += 15
    (a,) = tr.armies
    assert a.kind == "farm" and not a.moving
    assert abs(a.tile[0] - 165.5) <= 1.0       # median, not the last wobbly read (164)


def test_same_farm_tracked_twice_is_merged_once_its_xy_is_known():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    tr.update([farm(165, 377, t), farm(171, 377, t)], t)
    a, b = sorted(tr.armies, key=lambda a: a.id)
    a.info = {"xy": [168, 377]}
    b.info = {"xy": [168, 377]}             # both turned out to be the same resource point
    b.owner = "him"
    tr.update([farm(165, 377, t + 15), farm(171, 377, t + 15)], t + 15)
    farms = [x for x in tr.armies if x.kind == "farm"]
    assert len(farms) == 1
    assert farms[0].id == a.id and farms[0].owner == "him"


def test_his_farm_not_gone_while_its_reappearance_is_unchecked():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    tr.update([farm(175, 366, t)], t)
    (f,) = tr.armies
    f.owner = "him"
    everywhere = [lambda p: True]
    # next looks: the farm comes up 6 tiles off (too far to pair), as a new unchecked one
    for k in range(3):
        t += 15
        tr.match([farm(181, 366, t)], t, covered=everywhere)
        assert not [g for g in tr.gone if g.id == f.id], "he didn't stop gathering"
    assert any(a.id == f.id for a in tr.armies)


# ---------------------------------------------------------------- B. someone else's line

def test_parked_army_with_a_line_running_past_stops_counting_as_marching():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    line = ((150, 340), (200, 380))          # passes right by 179,360
    tr.update([army(179, 361, t, line)], t)
    for k in range(10):                      # 2+ minutes, never really moving
        t += 15
        tr.update([army(179 + (k % 2) * 0.6, 360 + (k % 3) * 0.4, t, line)], t)
    (a,) = tr.armies
    assert not a.moving and a.direction is None and a.dest is None
    assert tr.summary(lambda x: True)[0]["state"] != "marching"


def test_army_really_marching_is_still_marching():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    line = ((162, 374), (210, 422))
    for k in range(10):                      # 4 tiles a minute toward my city
        x = 166 + k * 0.7
        tr.update([army(x, 374 + (x - 162), t, line)], t)   # along the line to my city
        t += 15
    (a,) = tr.armies
    assert a.moving


def test_fresh_march_alerts_at_once():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    tr.update([army(163, 375, t)], t)                     # at his city, no line
    ev = tr.update([army(167, 379, t + 10, ((162, 374), (210, 422)))], t + 10)
    assert any(e[0] == "army_out" and e[2].get("target") == "my city" for e in ev)


# ---------------------------------------------------------------- C. direction flip-flop

def test_direction_does_not_flip_on_wobble_near_his_city():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    line = ((162, 374), (190, 395))
    tr.update([army(163, 374, t)], t)                    # parked at his city
    path = [(163, 370), (163, 372), (165, 374), (165, 375), (167, 376), (167, 377),
            (165, 374), (167, 377), (166, 376)]           # the real 19:16 wobble
    dirs = []
    for x, y in path:
        t += 3
        tr.update([army(x, y, t, line)], t)
        dirs.append(tr.armies[0].direction)
    flips = sum(1 for i in range(1, len(dirs)) if dirs[i] != dirs[i - 1])
    assert flips == 0, dirs
    assert dirs[0] == "out"


def test_real_return_home_is_still_seen():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    line = ((162, 374), (190, 395))
    tr.update([army(188, 393, t)], t)                    # out in the field
    for k in range(1, 8):                                # walking back, 3 tiles a look
        t += 20
        x, y = 188 - 2.4 * k, 393 - 1.8 * k
        tr.update([army(x, y, t, line)], t)
    assert tr.armies[0].direction == "home"


# ---------------------------------------------------------------- D. looks that prove nothing

def test_two_looks_a_moment_apart_are_one_miss():
    """2026-11-14 run: the farm spot looked at twice 0.7 s apart (icons not drawn yet) =
    two misses = 'He stopped gathering' (false)."""
    tr = Tracker(CITY, SITES)
    t = 1000.0
    tr.update([farm(179, 386, t)], t)
    (f,) = tr.armies
    f.owner = "him"
    everywhere = [lambda p: True]
    tr.match([], t + 10.0, covered=everywhere)
    tr.match([], t + 10.7, covered=everywhere)
    assert not tr.gone and tr.armies and tr.armies[0].id == f.id
    tr.match([], t + 20.0, covered=everywhere)      # a real second look, later: now it's gone
    assert [g.id for g in tr.gone] == [f.id]


def test_look_with_no_icons_drawn_vouches_for_nothing():
    """Hunter._cover: a look with no level plates at all (icons not drawn yet) covers no
    tile, so nothing can be missed from it."""
    import numpy as np
    from rokwatch.game import View
    from rokwatch.hunter import Hunter

    class G:
        pass
    h = Hunter(G(), {"hunt": {"player_name": "x", "city": list(CITY)},
                     "patrol": {"axes": [[0.0076, 0], [0, -0.0106]]}}, SITES,
               str(__import__("tempfile").mkdtemp()) + "/h.csv")
    v = View(np.zeros((1042, 1852, 3), np.uint8), [], (4183, 179, 386), True)
    v.icons_loaded = False
    assert not bool(h._cover(v)((179, 386)))
    v.icons_loaded = True
    assert bool(h._cover(v)((179, 386)))


# ---------------------------------------------------------------- E. map scale from real moves

def _scale_hunter():
    import tempfile
    from rokwatch.hunter import Hunter

    class G:
        pass
    return Hunter(G(), {"hunt": {"player_name": "x", "city": list(CITY)},
                        "patrol": {"axes": [[0.0076, 0.0], [0.0, -0.0106]]}}, SITES,
                  tempfile.mkdtemp() + "/h.csv")


def test_scale_learned_from_real_moves():
    """The fitted scale differed +-10-15% between runs (the zoom doesn't land on exactly
    the same scale): the hunt corrects it from its own drags + camera readings."""
    import numpy as np
    h = _scale_hunter()
    true = np.array([[0.0076 * 1.12, 0.0], [0.0, -0.0106 * 1.12]])
    rng = np.random.default_rng(1)
    for k in range(40):
        px = rng.uniform(-900, 900, 2)
        dcam = np.round(true @ (-px))                       # the readout is whole tiles
        if k in (5, 17):
            dcam = dcam + (40, -30)                          # a misread / a drag that didn't take
        h._learn_scale(px, dcam)
    got = np.array(h.axes_out)
    assert abs(got[0, 0] / true[0, 0] - 1) < 0.04 and abs(got[1, 1] / true[1, 1] - 1) < 0.04, got
    assert h.axes is h.axes_out
    # the close-zoom scale stays tied to the calibration (absolute), not to the refined one
    h.game.zoom_cfg = {"close_ratio": 0.7}
    assert abs(h._axes_close()[0][0] - 0.0076 * 0.7) < 1e-9


def test_crazy_scale_from_moves_is_ignored():
    import numpy as np
    h = _scale_hunter()
    true = np.array([[0.0076 * 2.0, 0.0], [0.0, -0.0106 * 2.0]])    # can't be right
    rng = np.random.default_rng(2)
    for _ in range(30):
        px = rng.uniform(-900, 900, 2)
        h._learn_scale(px, np.round(true @ (-px)))
    assert h.axes_out == [[0.0076, 0.0], [0.0, -0.0106]]


def test_someone_elses_farm_is_not_dropped_on_one_miss():
    """A neighbour's farm dropped on one miss was zoomed in on again a minute later (4x in
    one run, ~4 s each). Their farms now need two misses, like his."""
    tr = Tracker(CITY, SITES)
    t = 1000.0
    tr.update([farm(160, 371, t)], t)
    tr.armies[0].owner = "other"
    tr.match([], t + 15, covered=[lambda p: True])
    assert tr.armies and tr.armies[0].owner == "other"
