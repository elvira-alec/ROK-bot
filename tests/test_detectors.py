import cv2
import numpy as np
import yaml

from rokwatch.detectors import AttackAlertDetector, EnemyMarchDetector, FrozenDetector

CFG = yaml.safe_load(open("config.example.yaml"))
COLORS = CFG["enemy_watch"]["line_colors"]
ENEMY, HOME = (200, 200), (600, 400)


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
    det = warmed(EnemyMarchDetector(ENEMY, HOME, COLORS))
    marches = det.check(march_frame((440, 320)))
    assert marches and marches[0].toward_home
    assert marches[0].angle_off < 15


def test_march_elsewhere_not_toward_home():
    det = warmed(EnemyMarchDetector(ENEMY, HOME, COLORS))
    marches = det.check(march_frame((60, 60)))
    assert marches and not any(m.toward_home for m in marches)


def test_white_march_detected():
    det = warmed(EnemyMarchDetector(ENEMY, HOME, COLORS))
    marches = det.check(march_frame((440, 320), (245, 245, 245)))
    assert any(m.toward_home and m.color == "white" for m in marches)


def test_line_not_from_his_city_ignored():
    det = warmed(EnemyMarchDetector(ENEMY, HOME, COLORS))
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
