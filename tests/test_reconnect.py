"""The game lost its connection ('NETWORK DISCONNECTED ... click CONFIRM to reconnect') twice
during the user's live runs (laptop on a phone hotspot). The bot kept 'looking' at the dimmed
map for minutes (the coordinates can still be read through the dimming). Now it recognises
the dialog, presses CONFIRM, waits for the map and puts the zoom right again.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("rapidocr_onnxruntime")
from rokwatch import screen  # noqa: E402
from rokwatch.game import Game  # noqa: E402

DIR = Path(__file__).resolve().parent / "real_screens"


def _dialog(size=(1852, 1042)):
    f = cv2.imread(str(DIR / "disconnect" / "network_disconnected_960.jpg"))
    assert f is not None
    return cv2.resize(f, size, interpolation=cv2.INTER_CUBIC) if size else f


@pytest.mark.parametrize("size", [None, (1852, 1042), (1434, 807)])
def test_dialog_found_and_confirm_button_located(size):
    f = _dialog(size)
    assert screen.modal_dialog(f)
    b = screen.find_reconnect_button(f)
    assert b is not None
    H, W = f.shape[:2]
    # the CONFIRM button: middle of the screen, a bit below centre
    assert 0.44 * W < b[0] < 0.56 * W and 0.56 * H < b[1] < 0.66 * H, (b, W, H)


@pytest.mark.parametrize("name", [
    "disconnect/normal_map_after.png", "his_armies_near_night.png", "search_bar_marching.png",
    "search_bar_far_zoom.png", "march_simple_view.png", "his_army_marching_close.png",
    "recall_panel.png", "night/spot00_162_374.png",
])
def test_normal_screens_are_not_a_dialog(name):
    f = cv2.imread(str(DIR / name))
    assert f is not None, name
    assert not screen.modal_dialog(f)
    assert screen.find_reconnect_button(f) is None


def test_a_dialog_about_something_else_is_never_confirmed():
    # same look (blue panel, dimmed map) but the text isn't about the connection: e.g. a
    # teleport / spend-gems confirmation must never be pressed
    f = _dialog()
    H, W = f.shape[:2]
    x0, y0, x1, y1 = int(0.33 * W), int(0.30 * H), int(0.67 * W), int(0.55 * H)
    f[y0:y1, x0:x1] = f[int(0.45 * H), int(0.40 * W)]     # wipe the message, keep the panel
    cv2.putText(f, "Teleport to this location?", (x0 + 40, int(0.45 * H)),
                cv2.FONT_HERSHEY_SIMPLEX, 1.1, (255, 255, 255), 2)
    assert screen.modal_dialog(f)
    assert screen.find_reconnect_button(f) is None


class FakeWindow:
    """Shows the dialog until CONFIRM is clicked, then the map (after a moment)."""

    def __init__(self):
        self.dialog = _dialog()
        self.map = cv2.imread(str(DIR / "disconnect" / "normal_map_after.png"))
        self.clicks = []
        self.fixed_at = None

    def screenshot(self, area=None):
        import time
        if self.fixed_at and time.time() > self.fixed_at:
            return self.map.copy()
        return self.dialog.copy()

    def click(self, x, y):
        import time
        self.clicks.append((x, y))
        H, W = self.dialog.shape[:2]
        if 0.4 * W < x < 0.6 * W and 0.5 * H < y < 0.7 * H:
            self.fixed_at = time.time() + 1.5

    def drag(self, *a):
        pass

    def wheel(self, *a):
        pass

    def press(self, *a):
        raise AssertionError("Esc must not be pressed on the dialog")


def test_recover_connection_presses_confirm_and_waits_for_the_map():
    w = FakeWindow()
    g = Game(w, settle=0)
    assert g.recover_connection(timeout=20) is True
    assert len(w.clicks) == 1 and g.reconnected and g._at is None


def test_close_popups_reconnects_instead_of_pressing_escape():
    w = FakeWindow()
    g = Game(w, settle=0)
    assert g.close_popups() is True
    assert len(w.clicks) == 1


def test_nothing_happens_without_a_dialog():
    w = FakeWindow()
    w.fixed_at = 0.1          # already the map
    g = Game(w, settle=0)
    assert g.recover_connection(timeout=5) is False and w.clicks == []
