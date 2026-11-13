"""Zoom against a simulated game made from real screenshots: every notch rescales the
map, zoom changes animate, the view type changes like the game (3D close view /
simplified map / too far), and a notch can be lost."""

import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from rokwatch.game import Game, Stopped

REAL = Path(__file__).resolve().parent / "real_screens"


class ZoomSim:
    MAX = 20                                     # 0 = fully out ... 20 = fully in

    def __init__(self, level):
        self.close = cv2.imread(str(REAL / "own_city_gathering.png"))      # 3D view (13+)
        self.simple = cv2.imread(str(REAL / "march_simple_view.png"))      # simplified map (4-12)
        self.far = cv2.imread(str(REAL / "search_bar_far_zoom.png"))       # too far out (0-3)
        self.level, self.shown, self.t = level, float(level), time.time()
        self.drop = 0                                                      # notches to lose

    def wheel(self, x, y, dy):
        if self.drop:
            self.drop -= 1
            return
        self.shown, self.t = self._current(), time.time()
        self.level = max(0, min(self.MAX, self.level + (-1 if dy > 0 else 1)))

    def _current(self):
        k = min(1.0, (time.time() - self.t) / 0.25)
        return self.shown + (self.level - self.shown) * k

    def screenshot(self, area=None):
        lv = self._current()
        base = self.close if lv >= 12.5 else self.simple if lv >= 3.5 else self.far
        s = 1.08 ** (lv - 8)                                # every notch: a different scale
        h, w = base.shape[:2]
        big = cv2.resize(base, None, fx=s, fy=s)
        out = np.zeros_like(base)
        bh, bw = big.shape[:2]
        if s >= 1:
            y0, x0 = (bh - h) // 2, (bw - w) // 2
            out = big[y0:y0 + h, x0:x0 + w].copy()
        else:
            y0, x0 = (h - bh) // 2, (w - bw) // 2
            out[y0:y0 + bh, x0:x0 + bw] = big
        out[:int(0.1 * h), :int(0.45 * w)] = base[:int(0.1 * h), :int(0.45 * w)]   # top bar
        out[int(0.75 * h):, int(0.8 * w):] = base[int(0.75 * h):, int(0.8 * w):]    # buttons
        return out


def setup(search=8, close=15):
    sim = ZoomSim(search)
    g = Game(sim, settle=0)
    ref = g.zoom_reference()                     # "This is my searching zoom"
    sim.level = sim.shown = close                # you zoom in by hand
    cfg, score = g.calibrate_close(ref)          # "This is my close zoom"
    return sim, g, ref, cfg, score


def test_two_presses_measure_exactly():
    sim, g, ref, cfg, score = setup(8, 15)
    assert cfg == {"gap": 7, "t_out": 3, "t_in": 5} and score > 0.9, (cfg, score)
    assert sim.level == 15                       # ends at your close zoom
    for _ in range(2):
        assert g.to_sweep() and sim.level == 8 and g.match_score(ref) > 0.9
        assert g.to_close() and sim.level == 15


def test_lost_notch_is_corrected():
    sim, g, ref, cfg, _ = setup(8, 15)
    assert g.to_sweep() and sim.level == 8
    sim.drop = 1                                 # the game swallows one notch
    assert g.to_close() and sim.level == 15
    sim.drop = 1
    assert g.to_sweep() and sim.level == 8
    sim.level = sim.shown = 2                    # someone scrolled far out by hand
    g._at = None
    assert g.to_sweep() and 4 <= sim.level <= 12  # back on the simplified map, not further out


def test_stop_interrupts_at_once():
    sim, g, ref, cfg, _ = setup(8, 15)
    pressed = {"stop": True}
    g.stop_check = lambda: pressed["stop"]
    t0 = time.time()
    with pytest.raises(Stopped):
        g.to_sweep()
    with pytest.raises(Stopped):
        g.goto(100, 100)
    assert time.time() - t0 < 2.0
