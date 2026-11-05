"""Setup step, with the on-screen window replaced by scripted clicks."""

import numpy as np
import yaml

from rokwatch import calibrate as cal


class FakeBrowser:
    def screenshot(self, area=None):
        return np.full((900, 1600, 3), 90, np.uint8)


def test_calibrate_saves_points(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text("# keep me\nenemy_watch:\n  enabled: true\n")
    # user actions: (click x, y) or a key code; 13 = Enter, 8 = Backspace
    script = [(300, 200),                       # his city
              (700, 500), (999, 999), 8,        # your city, a mis-click, undo
              (400, 600), 13]                   # a gathering spot, done
    state = {"cb": None}

    def set_cb(win, cb):
        state["cb"] = cb

    idle = {"n": 0}

    def wait_key(ms):
        if not script:
            idle["n"] += 1
            assert idle["n"] < 5, "setup kept waiting for more input"
            return -1
        step = script.pop(0)
        if isinstance(step, tuple):
            state["cb"](cal.cv2.EVENT_LBUTTONDOWN, step[0], step[1], 0, None)
            return -1
        return step

    monkeypatch.setattr("builtins.input", lambda *_: "")
    monkeypatch.setattr(cal.cv2, "selectROI", lambda *a, **k: (0, 40, 1600, 860))
    monkeypatch.setattr(cal.cv2, "imshow", lambda *a: None)
    monkeypatch.setattr(cal.cv2, "setMouseCallback", set_cb)
    monkeypatch.setattr(cal.cv2, "waitKey", wait_key)
    monkeypatch.setattr(cal.cv2, "destroyAllWindows", lambda: None)
    monkeypatch.chdir(tmp_path)

    cfg = cal.calibrate(FakeBrowser(), str(cfg_path))
    assert script == []
    e = cfg["enemy_watch"]
    assert e["enemy_city"] == [300, 200]
    assert e["protect_points"] == [{"name": "your city", "pos": [700, 500]},
                                   {"name": "spot 1", "pos": [400, 600]}]
    assert cfg["game_area"] == [0, 40, 1600, 860]
    assert "# keep me" in cfg_path.read_text()
    assert (tmp_path / "calibration.png").exists()
    assert yaml.safe_load((tmp_path / "setup.yaml").read_text())["game_area"] == [0, 40, 1600, 860]
