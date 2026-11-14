"""Regression: the sweep must not zoom in to re-check a farm the tracker already
knows, just because its zoomed-out estimate drifted a few tiles.

Hunter._unknown decides which sightings still need an owner check. It has to use
the SAME farm tolerance as Tracker.match (5 tiles, and the farm's exact X:Y when
known); a tighter gate here made the bot "check the same resource point twice".
This test fails on the old 2.5-tile gate and passes on the aligned one.
"""

import time

from rokwatch.hunter import Hunter, Army, ArmySeen

NAME = "[P83L]JONH WICK"
CITY = (162, 374)
SITES = [("my city", (209, 418))]


class StubGame:
    """Hunter._unknown never touches the game; __init__ only sets zoom_cfg (skipped
    when cfg has no hunt.zoom)."""
    pass


def _hunter(tmp_path):
    cfg = {"hunt": {"player_name": NAME, "city": list(CITY), "radius": 25}}
    return Hunter(StubGame(), cfg, SITES, str(tmp_path / "his.csv"))


def _known_farm(tile=(165, 377), owner="him"):
    now = time.time()
    a = Army(1, tile, now, now, kind="farm", owner=owner)
    a.info = {"xy": [tile[0], tile[1]]}
    return a


def _seen_farm(tile):
    return ArmySeen(tile=tile, moving=False, ends=None, seen=time.time(), kind="farm")


def test_wobbled_farm_within_5_tiles_is_not_rechecked(tmp_path):
    h = _hunter(tmp_path)
    h.tracker.armies = [_known_farm((165, 377))]
    seen = _seen_farm((169, 377))          # 4 tiles off: estimate drift, same farm
    new = h._unknown([seen])
    assert new == []                       # not queued for another owner check
    assert seen.owner == "him"             # owner carried over from the known farm


def test_genuinely_different_farm_is_still_checked(tmp_path):
    h = _hunter(tmp_path)
    h.tracker.armies = [_known_farm((165, 377))]
    seen = _seen_farm((173, 377))          # 8 tiles off: a different point
    new = h._unknown([seen])
    assert new == [seen]
    assert seen.owner is None


def test_exact_xy_used_when_estimate_is_far_but_xy_is_close(tmp_path):
    h = _hunter(tmp_path)
    a = Army(1, (165, 377), time.time(), time.time(), kind="farm", owner="him")
    a.info = {"xy": [170, 377]}            # exact spot differs from the drifted tile
    h.tracker.armies = [a]
    seen = _seen_farm((171, 377))          # 1 tile from the exact xy, 6 from the tile
    new = h._unknown([seen])
    assert new == []
    assert seen.owner == "him"
