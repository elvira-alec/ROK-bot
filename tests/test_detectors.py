import cv2
import numpy as np
import yaml

from rokwatch.detectors import (AttackAlertDetector, EnemyMarchDetector, FrozenDetector,
                                MapShiftTracker, March, MarchMemory, merge_marches)

CFG = yaml.safe_load(open("config.example.yaml"))
COLORS = CFG["enemy_watch"]["line_colors"]
ENEMY, HOME = (200, 200), (600, 400)
POINTS = [("your city", HOME)]


def fake_map(seed=0):
    """Greenish map with a few static red/white things that should be ignored."""
    rng = np.random.default_rng(seed)
    img = np.full((500, 800, 3), (60, 140, 70), np.uint8)
    img = cv2.add(img, rng.integers(0, 6, img.shape, dtype=np.uint8))
    cv2.rectangle(img, (180, 180), (220, 220), (0, 0, 220), -1)  # his red city art
    cv2.line(img, (100, 450), (300, 450), (250, 250, 250), 3)     # static white road
    return img


def warmed(det):
    for i in range(6):
        assert det.check(fake_map(i)) == []
    return det


def march_frame(end, color=(0, 0, 255)):
    img = fake_map(99)
    cv2.line(img, ENEMY, end, color, 3)
    return img


def test_march_toward_home_detected():
    det = warmed(EnemyMarchDetector(ENEMY, POINTS, COLORS))
    marches = det.check(march_frame((440, 320)))
    assert marches and marches[0].toward_home
    assert marches[0].angle_off < 15


def test_march_elsewhere_not_toward_home():
    det = warmed(EnemyMarchDetector(ENEMY, POINTS, COLORS))
    marches = det.check(march_frame((60, 60)))
    assert marches and not any(m.toward_home for m in marches)


def test_white_march_detected():
    det = warmed(EnemyMarchDetector(ENEMY, POINTS, COLORS))
    marches = det.check(march_frame((440, 320), (245, 245, 245)))
    assert any(m.toward_home and m.color == "white" for m in marches)


def test_line_not_from_his_city_ignored():
    det = warmed(EnemyMarchDetector(ENEMY, POINTS, COLORS))
    img = fake_map(99)
    cv2.line(img, (500, 100), (700, 200), (0, 0, 255), 3)
    assert det.check(img) == []


def test_attack_border_needs_confirm():
    det = AttackAlertDetector(confirm_frames=2)
    alert = fake_map()
    e = 20
    for sl in [np.s_[:e, :], np.s_[-e:, :], np.s_[:, :e], np.s_[:, -e:]]:
        alert[sl] = (20, 20, 230)
    assert det.check(fake_map()) is None
    assert det.check(alert) is None       # first sighting
    assert det.check(alert) is not None   # confirmed
    assert det.check(fake_map()) is None


def test_attack_template(tmp_path):
    frame = fake_map()
    icon = np.zeros((40, 40, 3), np.uint8)
    cv2.circle(icon, (20, 20), 15, (0, 0, 255), -1)
    cv2.putText(icon, "!", (14, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (255, 255, 255), 2)
    cv2.imwrite(str(tmp_path / "warn.png"), icon)
    frame[50:90, 400:440] = icon
    det = AttackAlertDetector(confirm_frames=1, template_dir=str(tmp_path))
    assert "warn.png" in det.check(frame)


def test_frozen():
    det = FrozenDetector(minutes=1)
    img = fake_map()
    assert not det.check(img, 0)
    assert not det.check(img, 30)
    assert det.check(img, 61)
    assert not det.check(fake_map(5) // 2, 62)


def test_change_mode_catches_any_color():
    det = warmed(EnemyMarchDetector(ENEMY, POINTS, None, detect_changes=True))
    marches = det.check(march_frame((440, 320), (200, 60, 200)))  # purple: not in any range
    assert marches and marches[0].toward_home and marches[0].color == "new"


def test_dashed_line_still_one_march():
    det = warmed(EnemyMarchDetector(ENEMY, POINTS, COLORS))
    img = fake_map(99)
    for i in range(0, 300, 20):  # dashes along the direction to home
        t0, t1 = i / 300, (i + 13) / 300
        p0 = (int(200 + 400 * t0 * 0.75), int(200 + 200 * t0 * 0.75))
        p1 = (int(200 + 400 * t1 * 0.75), int(200 + 200 * t1 * 0.75))
        cv2.line(img, p0, p1, (0, 0, 255), 3)
    marches = det.check(img)
    assert len(marches) == 1 and marches[0].toward_home


def test_arrival_counts_even_if_angle_wide():
    # Line ends right on a second spot that's off to the side.
    det = warmed(EnemyMarchDetector(ENEMY, POINTS + [("farm", (200, 450))], COLORS))
    marches = det.check(march_frame((205, 440)))
    assert marches and marches[0].target == "farm"


def test_attack_ignores_always_red_edges():
    det = AttackAlertDetector(confirm_frames=2)
    reddish = fake_map()
    reddish[:30, :] = (20, 20, 230)  # e.g. red territory along the top, always there
    results = [det.check(reddish) for _ in range(30)]
    assert not any(results[10:])  # learned as part of the map after a short while


def test_attack_survives_pulse_gap():
    det = AttackAlertDetector(confirm_frames=2)
    alert = fake_map()
    alert[:25, :] = alert[-25:, :] = alert[:, :25] = alert[:, -25:] = (20, 20, 230)
    det.check(alert)
    assert det.check(alert)
    assert det.check(fake_map()) is None   # flash dimmed for one frame
    assert det.check(alert)                # still counted as the same attack


def test_merge_keeps_longest_per_direction():
    a = March((0, 0), (100, 0), "new", None, 90, 100)
    b = March((0, 0), (60, 2), "red", None, 90, 60)
    c = March((0, 0), (0, 80), "red", "x", 3, 80)
    out = merge_marches([b, a, c])
    assert len(out) == 2 and out[0].target == "x"
    assert out[1].length == 100 and out[1].color == "red"


def test_march_memory():
    mem = MarchMemory(repeat_minutes=1)
    m = March((0, 0), (100, 0), "red", "x", 0, 100)
    m2 = March((0, 0), (100, 5), "red", "x", 0, 100)
    other = March((0, 0), (0, 100), "red", "x", 0, 100)
    assert mem.is_new(m, 0)
    assert not mem.is_new(m2, 10)
    assert mem.is_new(other, 10)
    assert mem.is_new(m, 61)


def test_map_shift_tracker():
    base = cv2.resize(fake_map(3), (1000, 700))
    rng = np.random.default_rng(1)
    for _ in range(40):
        x, y = rng.integers(0, 980), rng.integers(0, 680)
        cv2.circle(base, (int(x), int(y)), int(rng.integers(4, 20)), tuple(int(v) for v in rng.integers(0, 255, 3)), -1)
    ref = base[100:600, 100:900]
    tr = MapShiftTracker(ref)
    dx, dy, conf = tr.measure(ref)
    assert abs(dx) < 1 and abs(dy) < 1 and conf > 0.5
    # map slid: what was at x now appears at x+30, y-20
    moved = base[120:620, 70:870]
    dx, dy, conf = tr.measure(moved)
    assert abs(dx - 30) < 2 and abs(dy + 20) < 2, (dx, dy)
