"""Checks against real Rise of Kingdoms screenshots (tests/real_screens/)."""

from pathlib import Path

import cv2
import numpy as np
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


ARMIES = {"march_simple_view": 1, "my_armies_simple": 1, "scouted_simple_2": 1,
          "scouted_simple_3": 2, "scouted_simple_4": 2, "scouted_simple_5": 1}


@pytest.mark.parametrize("name", ALL)
def test_army_icon(name):
    # Other players' armies are a grey diamond, only in the zoomed-out view.
    img = load(name)
    found = screen.find_army_icons(img)
    assert len(found) == ARMIES.get(name, 0), found
    if name == "march_simple_view":
        assert abs(found[0][0] - 1540) < 15 and abs(found[0][1] - 621) < 15
        assert len(screen.find_army_icons(cv2.resize(img, None, fx=0.5, fy=0.5))) == 1
        line = screen.find_march_lines(img)[0]
        assert line.distance_to(found[0]) < 15  # it sits on its march line


@pytest.mark.parametrize("name", [n for n in ALL if not n.startswith("scouted")])
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

STATES_NEW = {"battle_me_attacking", "my_armies_simple", "his_army_marching_close"} | {
    n for n in ALL if n.startswith("scouted")}

CAMERA = {"battle_me_attacking": (173, 375), "his_army_marching_close": (189, 384),
          "my_armies_simple": (176, 380), "scouted_his_city": (164, 375),
          "scouted_close_red_marker": (185, 395), "scouted_my_city": (210, 422),
          **{f"scouted_simple_{i}": (185, 398) for i in range(1, 6)},
          "search_bar_far_zoom": (187, 403), "search_bar_marching": (173, 378),
          "own_city_gathering": (209, 418), "enemy_city_close": (162, 374),
          "enemy_city_simple": (162, 375), "recall_panel": (205, 424),
          "march_simple_view": (417, 520), "march_close_view": (423, 521)}
SIMPLE = {"enemy_city_simple", "march_simple_view", "search_bar_far_zoom", "search_bar_marching",
          "my_armies_simple", *(f"scouted_simple_{i}" for i in range(1, 6))}
ZOOM = {"search_bar_far_zoom": "far", "search_bar_marching": "patrol", "enemy_city_simple": "patrol",
        "march_simple_view": "patrol", "my_armies_simple": "patrol",
        **{f"scouted_simple_{i}": "patrol" for i in range(1, 6)}}
MARCHING = {"search_bar_far_zoom", "search_bar_marching", "my_armies_simple"}


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


GATHER_OR_MARCH = [n for n in ALL if n not in STATES_NEW]


@pytest.mark.parametrize("name", GATHER_OR_MARCH)
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


@pytest.mark.parametrize("name", GATHER_OR_MARCH)
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
    k = next(x for x in t if x.text.strip().endswith("4183") and x.box[1] > 150)
    left = k.box[0] + k.box[2] - 173   # "#:" label starts ~173px left of the number's end
    merged = [x for x in t if x is not k and x.text.strip() != "#:"] + [
        screen.Text("#:4183", (left, k.box[1], k.box[0] + k.box[2] - left, k.box[3]), 0.8)]
    bar = screen.find_search_bar(load("search_bar_marching"), merged)
    assert bar and 1340 < bar.x_field[0] < 1520 and 1620 < bar.y_field[0] < 1820


@pytest.mark.parametrize("name", ALL)
def test_red_border_on_screen_edge_never_alerts(name):
    """Slide a window over the screenshot so the red alliance borders land on the
    screen edges (top, bottom, sides, corners) in every position."""
    img = load(name)
    H, W = img.shape[:2]
    h, w = int(H * 0.7), int(W * 0.7)
    for y in range(0, H - h + 1, 20):
        for x in range(0, W - w + 1, 60):
            crop = img[y:y + h, x:x + w]
            det = AttackAlertDetector()
            assert all(det.check(crop) is None for _ in range(3)), (name, x, y)


def test_border_hugging_two_edges_is_not_an_attack():
    # worst case: thick red territory lines along the top and left edges (a corner)
    img = load("search_bar_marching").copy()
    img[:60, :] = (40, 20, 220)
    img[:, :60] = (40, 20, 220)
    det = AttackAlertDetector()
    assert all(det.check(img) is None for _ in range(5))


def test_real_attack_glow_still_alerts():
    # red glow on all four edges over a real map
    img = load("search_bar_marching").copy()
    e = 60
    for sl in (np.s_[:e, :], np.s_[-e:, :], np.s_[:, :e], np.s_[:, -e:]):
        img[sl] = (img[sl] * 0.3 + np.array((30, 20, 230)) * 0.7).astype(np.uint8)
    det = AttackAlertDetector()
    assert [det.check(img) for _ in range(3)][-1] is not None


# ---------------------------------------------------------------- being scouted / attacked, battles

WARNING_STRONG = ["scouted_close_red_marker", "scouted_his_city", "scouted_simple_4"]
WARNING_FAINT = ["scouted_simple_1", "scouted_simple_2", "scouted_simple_5"]   # pulse dimmer
NO_WARNING = [n for n in ALL if not n.startswith("scouted")]


@pytest.mark.parametrize("name", WARNING_STRONG)
def test_warning_border_strong_alerts_at_once(name):
    assert AttackAlertDetector().check(load(name))


@pytest.mark.parametrize("name", WARNING_FAINT)
def test_warning_border_faint_alerts_on_second_look(name):
    det = AttackAlertDetector()
    assert det.check(load(name)) is None
    assert det.check(load(name))


def test_warning_pulse_with_a_dark_frame_between():
    det = AttackAlertDetector()
    seq = ["scouted_simple_1", "scouted_simple_3", "scouted_simple_2"]  # bright, off, bright
    assert [bool(det.check(load(n))) for n in seq] == [False, False, True]


@pytest.mark.parametrize("name", NO_WARNING)
def test_no_warning_on_normal_screens(name):
    det = AttackAlertDetector()
    assert all(det.check(load(name)) is None for _ in range(4))


STATES = {"battle_me_attacking": ["fighting", "fighting", "stopped"],
          "my_armies_simple": ["marching"] * 3}
STATES.update({n: ["returning"] * 3 for n in ALL if n.startswith("scouted")})


@pytest.mark.parametrize("name", sorted(STATES))
def test_troop_states_new(name, texts):
    _, _, slots = screen.troop_panel(load(name), texts[name])
    assert [s.state for s in slots][:3] == STATES[name]


def test_his_march_close_view_found():
    lines = screen.find_march_lines(load("his_army_marching_close"))
    assert any(ln.color == "white" and ln.dots > 30 for ln in lines)


def test_scout_lines_and_diamonds():
    for name in ("scouted_simple_3", "scouted_simple_4"):
        assert len(screen.find_army_icons(load(name))) == 2
        assert any(ln.color == "white" for ln in screen.find_march_lines(load(name)))


# ---------------------------------------------------------------- armies vs scouts on the map

ICONS = {"march_simple_view": ["army"], "my_armies_simple": ["army"],
         "scouted_simple_2": ["scout"], "scouted_simple_3": ["scout", "scout"],
         "scouted_simple_4": ["scout", "scout"], "scouted_simple_5": ["scout"]}
CROPS = {"army_cavalry": "army", "army_siege": "army", "army_flag": "army", "scout_plain": "scout"}


@pytest.mark.parametrize("name", ALL)
def test_map_icon_kinds(name):
    found = screen.find_map_icons(load(name))
    assert sorted(k for _, k in found) == sorted(ICONS.get(name, [])), found


@pytest.mark.parametrize("name", sorted(CROPS))
def test_map_icon_kinds_on_crops(name):
    # cropped screenshots, placed on a plain background at the original size
    crop = cv2.imread(str(DIR / "crops" / f"{name}.jpg"))
    canvas = np.full((1320, 2868, 3), (70, 150, 110), np.uint8)
    canvas[400:400 + crop.shape[0], 1000:1000 + crop.shape[1]] = crop
    found = screen.find_map_icons(canvas)
    assert [k for _, k in found] == [CROPS[name]], found


ICON_SPOTS = [("my_armies_simple.png", (933, 991), "army"),        # sword
              ("march_simple_view.png", (1538, 621), "army"),      # catapult
              ("crops/army_cavalry.jpg", (197, 163), "army"),      # horse
              ("crops/army_siege.jpg", (178, 155), "army"),
              ("crops/army_flag.jpg", (137, 148), "army"),
              ("scouted_simple_3.png", (1215, 933), "scout"),
              ("scouted_simple_4.png", (1341, 726), "scout"),
              ("scouted_simple_5.png", (1443, 727), "scout"),
              ("scouted_simple_2.png", (1378, 839), "scout"),
              ("scouted_simple_4.png", (1121, 929), "scout")]


def test_scouts_and_armies_never_swapped():
    """Each icon at several screen sizes and brightnesses, on the real day and night
    map. A scout must never come out as an army or the other way round."""
    day, night = load("march_simple_view"), load("search_bar_marching")
    swapped, missed, total = [], 0, 0
    for f, (x, y), kind in ICON_SPOTS:
        im = cv2.imread(str(DIR / f))
        r = 34 if kind == "scout" else (60 if f.startswith("crops") else 46)
        patch = im[max(0, y - r):y + r, max(0, x - r):x + r]
        for scale in (0.7, 1.0, 1.3):
            p = cv2.resize(patch, None, fx=scale, fy=scale,
                           interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_CUBIC)
            for bg_src in (day, night):
                for bright in (0.9, 1.0, 1.1):
                    pv = np.clip(p.astype(float) * bright, 0, 255).astype(np.uint8)
                    bg = cv2.resize(bg_src, None, fx=scale, fy=scale)
                    cy, cx = int(560 * scale), int(1700 * scale)
                    bg[cy:cy + pv.shape[0], cx:cx + pv.shape[1]] = pv
                    mid = (cx + pv.shape[1] // 2, cy + pv.shape[0] // 2)
                    got = [k for c, k in screen.find_map_icons(bg)
                           if abs(c[0] - mid[0]) < 60 * scale and abs(c[1] - mid[1]) < 60 * scale]
                    total += 1
                    if any(k != kind for k in got):
                        swapped.append((f, scale, bright, got))
                    elif not got:
                        missed += 1
    assert swapped == [], swapped
    assert missed <= total * 0.05, (missed, total)
