"""Checks against real Rise of Kingdoms screenshots (tests/real_screens/)."""

from pathlib import Path

import cv2
import pytest

from rokwatch import screen
from rokwatch.detectors import AttackAlertDetector
from rokwatch.patrol import Patrol, Site

DIR = Path(__file__).resolve().parent / "real_screens"
ALL = sorted(p.stem for p in DIR.glob("*.png"))


def load(name):
    return cv2.imread(str(DIR / f"{name}.png"))


# ---------------------------------------------------------------- march lines (no OCR needed)

def test_white_march_simple_view():
    lines = screen.find_march_lines(load("march_simple_view"))
    assert len(lines) == 1 and lines[0].color == "white"
    ln = lines[0]
    assert ln.length > 2500                      # crosses the whole screen
    assert ln.distance_to((1540, 620)) < 25      # passes the army icon (diamond)


def test_white_march_close_view():
    lines = screen.find_march_lines(load("march_close_view"))
    assert len(lines) == 1 and lines[0].color == "white"
    assert lines[0].distance_to((803, 700)) < 60  # the army (3D models stand just above it)


def test_green_alliance_march():
    lines = screen.find_march_lines(load("recall_panel"))
    assert [ln.color for ln in lines] == ["green"]


@pytest.mark.parametrize("name", ["own_city_gathering", "enemy_city_close", "enemy_city_simple"])
def test_no_false_march_lines(name):
    # trees, flowers, rocks, red territory borders, resource icons: none are marches
    assert screen.find_march_lines(load(name)) == []


def test_works_at_other_sizes():
    img = load("march_simple_view")
    for scale in (0.5, 0.67):
        small = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
        lines = screen.find_march_lines(small)
        assert len(lines) == 1 and lines[0].length > 2500 * scale * 0.9, scale


@pytest.mark.parametrize("name", ALL)
def test_red_territory_border_is_not_an_attack(name):
    det = AttackAlertDetector()
    img = load(name)
    assert all(det.check(img) is None for _ in range(4))


def test_patrol_ignores_march_just_passing_by():
    # The march in this view passes the middle of the screen but doesn't start or end there.
    p = Patrol.__new__(Patrol)
    p.radius, p.axes, p.sites = 0.09, None, []
    site = Site("watched", 417, 520, "enemy")
    assert p.sightings(site, [load("march_simple_view")]) == []


# ---------------------------------------------------------------- reading text (OCR)

ocr = pytest.importorskip("rapidocr_onnxruntime")

CAMERA = {"own_city_gathering": (209, 418), "enemy_city_close": (162, 374),
          "enemy_city_simple": (162, 375), "recall_panel": (205, 424),
          "march_simple_view": (417, 520), "march_close_view": (423, 521)}
SIMPLE = {"enemy_city_simple", "march_simple_view"}


@pytest.fixture(scope="module")
def texts():
    return {n: screen.read_text(load(n)) for n in ALL}


@pytest.mark.parametrize("name", ALL)
def test_camera_coordinates(name, texts):
    cam = screen.camera_coords(load(name), texts[name])
    assert cam and cam[0] == 4183 and cam[1:3] == CAMERA[name]


@pytest.mark.parametrize("name", ALL)
def test_simple_view_detected(name, texts):
    assert screen.is_simple_view(texts[name], load(name)) == (name in SIMPLE)


@pytest.mark.parametrize("name", ALL)
def test_troop_panel(name, texts):
    used, total, slots = screen.troop_panel(load(name), texts[name])
    assert (used, total) == (3, 3)
    states = [s.state for s in slots]
    assert states[:3] == ["gathering"] * 3 and states.count("scout") >= 2
    # click points sit on the portraits, one above the other
    xs = {s.portrait[0] for s in slots}
    assert max(xs) - min(xs) < 10


def test_recall_button_and_location(texts):
    t = texts["recall_panel"]
    btn = screen.find_text(t, r"^RECALL$")
    assert btn and abs(btn[0].center[0] - 2008) < 30 and abs(btn[0].center[1] - 891) < 30
    coords = [screen.parse_coords(x.text) for x in t if x.box[1] > 200]
    assert (None, 205, 424) in coords or (4183, 205, 424) in coords


def test_enemy_name_and_chat_coords(texts):
    names = [x.text for x in texts["enemy_city_close"]]
    assert any("JONH" in n and "WICK" in n.upper() for n in names), names
    from rokwatch.game import coords_in_chat
    assert (162, 374) in coords_in_chat(texts["recall_panel"])
