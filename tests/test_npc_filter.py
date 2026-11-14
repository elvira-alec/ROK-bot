"""Resource points, cities and barbarians (NPCs) show a level plate right under their icon;
player armies and scouts never do. At night white resource icons passed for army diamonds
and red barbarian icons for hostile armies - each cost a zoom in for nothing (8 of 9 "army"
checks in the 2026-11-14 live run). find_map_icons now drops anything with its own plate.

night/spot00_162_374.png: the real searching zoom at his city (captured live 2026-11-14).
"""

from pathlib import Path

import cv2
import pytest

pytest.importorskip("rapidocr_onnxruntime")
from rokwatch import screen  # noqa: E402

DIR = Path(__file__).resolve().parent / "real_screens"
K = 1852 / 1200        # points below were read off a 1200-wide copy


def _live():
    f = cv2.imread(str(DIR / "night" / "spot00_162_374.png"))
    assert f is not None
    return f


@pytest.mark.parametrize("name,pt", [
    ("resource lv4", (497, 248)), ("resource lv6", (882, 138)),
    ("barbarian 21", (651, 122)), ("barbarian 7", (692, 84)),
    ("barbarian fort 3", (624, 238)), ("barbarian 18", (731, 212)),
])
def test_npcs_and_resource_points_have_their_plate(name, pt):
    f = _live()
    assert screen.has_level_tag_below(f, (pt[0] * K, pt[1] * K)), name


@pytest.mark.parametrize("name,pt", [("his city", (566, 343)), ("empty ground", (300, 420))])
def test_no_plate_where_there_is_none(name, pt):
    f = _live()
    assert not screen.has_level_tag_below(f, (pt[0] * K, pt[1] * K)), name


@pytest.mark.parametrize("f,pts", [
    ("his_armies_near_night.png", [(1523, 552), (1070, 743)]),
    ("march_simple_view.png", [(1538, 621)]),
    ("my_armies_simple.png", [(933, 991)]),
    ("attack_red_diamond_simple.png", [(1258, 805), (1046, 1031)]),
])
def test_real_armies_scouts_hostiles_never_filtered(f, pts):
    im = cv2.imread(str(DIR / f))
    for p in pts:
        assert not screen.has_level_tag_below(im, p), (f, p)


def test_live_frame_finds_no_fake_armies():
    # at this moment he had nothing out: nothing on screen is an army
    assert [k for _, k in screen.find_map_icons(_live())] == []


# ---------------------------------------------------------------- night

@pytest.mark.parametrize("name", ["spot00_158_367.png", "spot01_158_361.png", "spot02_166_361.png"])
def test_night_resource_icons_are_not_armies(name):
    """At night a white resource icon's tinted outline passed the 'dark ring' test and it
    came out as an army (every one cost a zoom in). These spots had no armies."""
    f = cv2.imread(str(DIR / "night2" / name))
    assert screen.find_map_icons(f) == []


# ---------------------------------------------------------------- icons drawn yet?

@pytest.mark.parametrize("name,drawn", [
    ("unloaded/bare_map_after_drag.jpg", False), ("unloaded/bare_map_after_jump.jpg", False),
    ("unloaded/same_spot_drawn.jpg", True), ("night2/spot00_158_367.png", True),
    ("night2/spot02_166_361.png", True), ("night/spot00_162_374.png", True),
])
def test_icons_drawn(name, drawn):
    """Right after a jump the ground shows before the icons: a look then saw his farms
    'gone' and he 'stopped gathering' (two false alerts, 2026-11-14). Night included: the
    plain plate count reads 0-2 at night with every icon on screen."""
    f = cv2.imread(str(DIR / name))
    assert f is not None, name
    assert screen.icons_drawn(f) is drawn


def test_resource_icon_under_his_gathering_badge_is_not_an_army(monkeypatch):
    """2026-11-14 night: each of his farms came with a fake 'army' diamond under it - the
    resource point's own white icon (live: farm badge at 919,501, 'army' at 920,522).
    The 2 'rhombuses' the user saw on the panel map; 20 close-ups for nothing."""
    import numpy as np
    from rokwatch.hunter import Hunter
    H = 1042
    monkeypatch.setattr(screen, "find_march_lines", lambda f: [])
    monkeypatch.setattr(screen, "find_gather_badges", lambda f: [(919, 465)])
    monkeypatch.setattr(screen, "find_map_icons",
                        lambda f: [((920, 522), "army"), ((1400, 300), "army"), ((921, 520), "scout")])
    found = Hunter.detect(np.zeros((H, 1852, 3), np.uint8))
    kinds = [(k, tuple(int(v) for v in d)) for d, k, _ in found]
    assert ("army", (1400, 300)) in kinds              # a real one elsewhere stays
    assert not [k for k, p in kinds if k in ("army", "scout") and abs(p[0] - 920) < 5]
    assert [k for k, _ in kinds].count("farm") == 1
