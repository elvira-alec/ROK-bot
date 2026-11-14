"""Whose farm is it, without zooming in? Hunter._unknown may copy the owner of a farm it
already knows ONLY when the sighting is the very same resource point (handoff rule: his farms
were hidden as 'other' by copying a neighbour's owner). Resource points by his base are 3-6
tiles apart.

2026-11-14 (v1.17 live run): a 5-tile gate here made his croplands at 179,386 and 180,390 take
the owner of [SRTR]TAAK's farm at 180,382 - two of his gathering spots never shown.
"""

import time

from rokwatch.hunter import Hunter, Army, ArmySeen, Tracker

NAME = "[P83L]JONH WICK"
CITY = (162, 374)
SITES = [("my city", (209, 418))]


class StubGame:
    pass


def _hunter(tmp_path):
    cfg = {"hunt": {"player_name": NAME, "city": list(CITY), "radius": 25}}
    return Hunter(StubGame(), cfg, SITES, str(tmp_path / "his.csv"))


def _known_farm(tile, owner, xy=None, id=1):
    now = time.time()
    a = Army(id, tile, now, now, kind="farm", owner=owner)
    a.info = {"xy": list(xy or tile)}
    return a


def _seen_farm(tile):
    return ArmySeen(tile=tile, moving=False, ends=None, seen=time.time(), kind="farm")


def test_neighbours_farm_owner_never_copied_to_his_farms_nearby(tmp_path):
    h = _hunter(tmp_path)
    h.tracker.armies = [_known_farm((179, 382), "other", xy=(180, 382))]     # [SRTR]TAAK
    for tile in [(179, 386), (180, 390), (179, 379)]:                         # his, 3-8 tiles away
        s = _seen_farm(tile)
        assert h._unknown([s]) == [s], tile          # checked, not assumed
        assert s.owner is None


def test_the_same_resource_point_is_not_checked_again(tmp_path):
    h = _hunter(tmp_path)
    h.tracker.armies = [_known_farm((165, 377), "him", xy=(167, 379))]
    s = _seen_farm((166, 378))                       # 1.4 tiles from its exact X:Y
    assert h._unknown([s]) == []
    assert s.owner == "him"


def test_exact_xy_used_when_the_estimate_is_off(tmp_path):
    h = _hunter(tmp_path)
    h.tracker.armies = [_known_farm((163, 375), "him", xy=(167, 379))]     # tile drifted
    s = _seen_farm((168, 379))                       # 1 tile from the exact spot
    assert h._unknown([s]) == []
    assert s.owner == "him"


def test_new_farm_next_to_a_known_one_is_its_own_farm_in_the_tracker():
    tr = Tracker(CITY, SITES)
    t = 1000.0
    tr.update([_seen_farm((180, 382))], t)
    taak = tr.armies[0]
    taak.owner, taak.info = "other", {"xy": [180, 382]}
    # his farm 4 tiles off shows up while TAAK's isn't in this look: not paired with it
    tr.match([_seen_farm((180, 386))], t + 15)
    assert len(tr.armies) == 2
    new = [a for a in tr.armies if a is not taak][0]
    assert new.owner is None


def test_someone_elses_farm_by_his_city_is_checked_again_later(tmp_path):
    h = _hunter(tmp_path)
    a = _known_farm((170, 380), "other")
    a.other_since = time.time() - 11 * 60
    h.tracker.armies = [a]
    # the round's re-open step, as round() does it
    now = time.time()
    for x in h.tracker.armies:
        if (x.kind == "farm" and x.owner == "other" and x.other_since
                and now - x.other_since > h.recheck_other):
            x.owner, x.checks, x.other_since = None, 0, None
    assert a.owner is None and a.checks == 0
