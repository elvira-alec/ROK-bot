"""Zoom measuring against a simulated game made from a real screenshot: every notch
really rescales the map, zoom changes animate over ~0.3 s, and zooming out stops
at a limit (extra notches do nothing)."""

import time
from pathlib import Path

import cv2
import numpy as np

from rokwatch.game import Game

REAL = Path(__file__).resolve().parent / "real_screens" / "own_city_gathering.png"


class ZoomSim:
    MAX_OUT = 14                                 # notches from fully in to fully out

    def __init__(self, level):
        big = cv2.imread(str(REAL))
        self.world = cv2.resize(big, None, fx=2, fy=2, interpolation=cv2.INTER_LINEAR)
        self.level = level                       # 0 = fully out ... MAX_OUT = fully in
        self.shown, self.t = float(level), time.time()

    def _scale(self, lv):
        return 0.35 * (1.09 ** lv)

    def wheel(self, x, y, dy):
        self.shown = self._current()
        self.t = time.time()
        self.level = max(0, min(self.MAX_OUT, self.level + (-1 if dy > 0 else 1)))

    def _current(self):                          # animation toward the target level
        k = min(1.0, (time.time() - self.t) / 0.3)
        return self.shown + (self.level - self.shown) * k

    def screenshot(self, area=None):
        s = self._scale(self._current())
        h, w = 660, 1434
        Hb, Wb = self.world.shape[:2]
        cw, ch = int(w / s / 2), int(h / s / 2)
        cx, cy = Wb // 2, Hb // 2
        crop = self.world[max(0, cy - ch // 2):cy + ch // 2, max(0, cx - cw // 2):cx + cw // 2]
        return cv2.resize(crop, (w, h), interpolation=cv2.INTER_AREA)


def test_measure_and_return_to_a_hand_set_zoom():
    for start in (3, 9):
        sim = ZoomSim(start)
        g = Game(sim, settle=0)
        n, ok = g.measure_zoom()
        assert ok and n == start, (start, n)
        assert sim.level == start
        sim.level = sim.shown = 12                 # you zoomed somewhere else by hand
        g.set_zoom(start, anchor=True)
        assert sim.level == start
        g.set_zoom(start + 2)                      # relative steps (to read names) and back
        g.set_zoom(start)
        assert sim.level == start
