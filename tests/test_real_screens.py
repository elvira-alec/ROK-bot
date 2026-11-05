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


def test_your_marches_at_night_are_green():
    # 3 of your armies marching (night, blue-tinted map): all lines found are green
    for name in ("search_bar_marching", "search_bar_far_zoom"):
        lines = screen.find_march_lines(load(name))
        assert lines and all(ln.color == "green" for ln in lines), (name, lines)
        assert screen.find_army_icons(load(name)) == []  # your armies aren't grey diamonds


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
def test_army_icon(name):
    # Other players' armies are a grey diamond, only in the zoomed-out view.
    img = load(name)
    found = screen.find_army_icons(img)
    if name == "march_simple_view":
        assert len(found) == 1 and abs(found[0][0] - 1540) < 15 and abs(found[0][1] - 621) < 15
        small = screen.find_army_icons(cv2.resize(img, None, fx=0.5, fy=0.5))
        assert len(small) == 1
        line = screen.find_march_lines(img)[0]
        assert line.distance_to(found[0]) < 15  # it sits on its march line
    else:
        assert found == []


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

CAMERA = {"search_bar_far_zoom": (187, 403), "search_bar_marching": (173, 378),
          "own_city_gathering": (209, 418), "enemy_city_close": (162, 374),
          "enemy_city_simple": (162, 375), "recall_panel": (205, 424),
          "march_simple_view": (417, 520), "march_close_view": (423, 521)}
SIMPLE = {"enemy_city_simple", "march_simple_view", "search_bar_far_zoom", "search_bar_marching"}
ZOOM = {"search_bar_far_zoom": "far", "search_bar_marching": "patrol", "enemy_city_simple": "patrol",
        "march_simple_view": "patrol"}
MARCHING = {"search_bar_far_zoom", "search_bar_marching"}


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
    # the counter is a bonus (OCR can miss it); the troops themselves must be found
    assert (used, total) in ((3, 3), (None, None))
    states = [s.state for s in slots]
    mode = "marching" if name in MARCHING else "gathering"  # footprints vs pickaxe badge
    assert states[:3] == [mode] * 3 and states.count("scout") >= 2
    # click points sit on the portraits, one above the other
    xs = {s.portrait[0] for s in slots}
    assert max(xs) - min(xs) < 10


@pytest.mark.parametrize("name", ALL)
def test_troop_panel_without_counter(name, texts):
    # if OCR misses the "3/3" counter, the troops are still found
    no_counter = [t for t in texts[name] if not t.text.replace(" ", "").startswith("3/")]
    used, total, slots = screen.troop_panel(load(name), no_counter)
    assert used in (3, None)   # second, zoomed-in read of the counter may succeed
    mode = "marching" if name in MARCHING else "gathering"
    assert [s.state for s in slots][:3] == [mode] * 3


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


@pytest.mark.parametrize("name", ALL)
def test_zoom_level(name, texts):
    expect = ZOOM.get(name, "close")
    assert screen.zoom_level(load(name), texts[name]) == expect


@pytest.mark.parametrize("name", ["search_bar_far_zoom", "search_bar_marching"])
def test_search_bar_positions(name, texts):
    bar = screen.find_search_bar(load(name), texts[name])
    assert bar is not None
    # X box spans about x 1330-1530, Y box 1610-1830, button 1842-1926, row y 225-295
    assert 1340 < bar.x_field[0] < 1520 and 1620 < bar.y_field[0] < 1820
    assert 1850 < bar.button[0] < 1920
    assert all(225 < p[1] < 295 for p in (bar.x_field, bar.y_field, bar.button))


@pytest.mark.parametrize("name", ["recall_panel", "march_simple_view", "own_city_gathering"])
def test_no_search_bar_when_closed(name, texts):
    assert screen.find_search_bar(load(name), texts[name]) is None


def test_search_bar_when_label_and_number_merge(texts):
    # OCR sometimes reads "#:" and "4183" as one piece: "#:4183"
    t = texts["search_bar_marching"]
    k = next(x for x in t if x.text.strip() == "4183")
    h = next(x for x in t if x.text.strip() == "#:")
    merged = [x for x in t if x not in (k, h)] + [
        screen.Text("#:4183", (h.box[0], k.box[1], k.box[0] + k.box[2] - h.box[0], k.box[3]), 0.8)]
    bar = screen.find_search_bar(load("search_bar_marching"), merged)
    assert bar and 1340 < bar.x_field[0] < 1520 and 1620 < bar.y_field[0] < 1820
