"""Zoom against a simulated game made from real screenshots: every notch really
rescales the map, zoom changes animate, and the view type changes like the game
(close 3D view / simplified map / too far)."""

import time
from pathlib import Path

import cv2

from rokwatch.game import Game

REAL = Path(__file__).resolve().parent / "real_screens"


class ZoomSim:
    MAX = 20                                     # 0 = fully out ... 20 = fully in

    def __init__(self, level):
        self.close = cv2.imread(str(REAL / "own_city_gathering.png"))      # 3D view
        self.simple = cv2.imread(str(REAL / "march_simple_view.png"))      # simplified map
        self.far = cv2.imread(str(REAL / "search_bar_far_zoom.png"))       # too far out
        self.level, self.shown, self.t = level, float(level), time.time()
        self.wheels = 0

    def wheel(self, x, y, dy):
        self.shown, self.t = self._current(), time.time()
        self.level = max(0, min(self.MAX, self.level + (-1 if dy > 0 else 1)))
        self.wheels += 1

    def _current(self):
        k = min(1.0, (time.time() - self.t) / 0.25)
        return self.shown + (self.level - self.shown) * k

    def screenshot(self, area=None):
        lv = self._current()
        base = self.close if lv >= 13 else self.simple if lv >= 4 else self.far
        s = 1.0 + 0.06 * (lv - round(lv) + (lv % 3))        # map rescales with every notch
        h, w = base.shape[:2]
        big = cv2.resize(base, None, fx=s, fy=s)
        y0, x0 = (big.shape[0] - h) // 2, (big.shape[1] - w) // 2
        out = big[y0:y0 + h, x0:x0 + w].copy()
        out[:int(0.1 * h), :int(0.45 * w)] = base[:int(0.1 * h), :int(0.45 * w)]   # the top bar stays
        out[int(0.75 * h):, int(0.8 * w):] = base[int(0.75 * h):, int(0.8 * w):]    # so do the buttons
        return out


def test_two_zooms_without_ever_zooming_far_out():
    sim = ZoomSim(8)                             # the searching zoom you picked
    g = Game(sim, settle=0)
    gap, ok = g.calibrate_zoom()
    assert ok and sim.level == 8 and gap == 13 - 8 + 1
    for _ in range(3):
        assert g.to_close() and sim.level >= 13
        assert g.to_sweep() and sim.level == 8


def test_lost_notch_is_corrected():
    sim = ZoomSim(8)
    g = Game(sim, settle=0)
    g.calibrate_zoom()
    g.to_close()
    sim.level = 2                                # e.g. you scrolled out by hand: too far
    sim.shown = 2.0
    g._at = None
    assert g.to_sweep() and 4 <= sim.level < 13  # back on the simplified map, never further out


def test_stop_interrupts_at_once():
    import pytest
    from rokwatch.game import Stopped
    sim = ZoomSim(8)
    g = Game(sim, settle=0)
    pressed = {"stop": False}
    g.stop_check = lambda: pressed["stop"]
    g.calibrate_zoom()
    pressed["stop"] = True
    t0 = time.time()
    with pytest.raises(Stopped):
        g.to_close()
    with pytest.raises(Stopped):
        g.goto(100, 100)
    assert time.time() - t0 < 1.0
