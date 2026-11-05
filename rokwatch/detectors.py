"""Image checks that look at a game screenshot (a BGR numpy array)."""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

RED_RANGES = [((0, 120, 120), (10, 255, 255)), ((170, 120, 120), (180, 255, 255))]


def color_mask(frame: np.ndarray, ranges) -> np.ndarray:
    """Return a 0/255 mask of pixels whose HSV color falls in any range."""
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = np.zeros(frame.shape[:2], np.uint8)
    for lo, hi in ranges:
        mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    return mask


class AttackAlertDetector:
    """Spots the red flashing screen border (and optional warning-icon templates)."""

    def __init__(self, edge_width=0.04, red_fraction=0.30, confirm_frames=2,
                 template_dir=None, template_threshold=0.82):
        self.edge_width = edge_width
        self.red_fraction = red_fraction
        self.confirm_frames = confirm_frames
        self.template_threshold = template_threshold
        self.templates = []
        if template_dir and Path(template_dir).is_dir():
            for p in sorted(Path(template_dir).glob("*.png")):
                img = cv2.imread(str(p))
                if img is not None:
                    self.templates.append((p.name, img))
        self._streak = 0

    def edge_red_fraction(self, frame: np.ndarray) -> float:
        h, w = frame.shape[:2]
        e = max(2, int(min(h, w) * self.edge_width))
        mask = color_mask(frame, RED_RANGES)
        border = np.zeros_like(mask)
        border[:e, :] = border[-e:, :] = 255
        border[:, :e] = border[:, -e:] = 255
        total = cv2.countNonZero(border)
        return cv2.countNonZero(mask & border) / total if total else 0.0

    def match_template(self, frame: np.ndarray):
        for name, tpl in self.templates:
            if tpl.shape[0] > frame.shape[0] or tpl.shape[1] > frame.shape[1]:
                continue
            res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
            if res.max() >= self.template_threshold:
                return name
        return None

    def check(self, frame: np.ndarray):
        """Return a reason string when an attack is detected, else None."""
        frac = self.edge_red_fraction(frame)
        tpl = self.match_template(frame) if self.templates else None
        hit = frac >= self.red_fraction or tpl is not None
        self._streak = self._streak + 1 if hit else 0
        if self._streak >= self.confirm_frames:
            if tpl:
                return f"warning icon seen ({tpl})"
            return f"red warning on screen edges ({frac:.0%})"
        return None


@dataclass
class March:
    start: tuple
    end: tuple
    color: str
    toward_home: bool
    angle_off: float  # degrees between march direction and the direction to home


class EnemyMarchDetector:
    """Finds new march lines that leave the enemy city and says where they point.

    Things that are always on screen (flags, borders, his city art) are learned
    as background over the first few checks and ignored after that.
    """

    def __init__(self, enemy_city, home, line_colors: dict, start_radius=70,
                 angle_tolerance_degrees=35, min_line_length=35,
                 background_rate=0.05, background_level=0.6, warmup_frames=5):
        self.enemy = np.array(enemy_city, float)
        self.home = np.array(home, float)
        self.line_colors = line_colors
        self.start_radius = start_radius
        self.angle_tol = angle_tolerance_degrees
        self.min_len = min_line_length
        self.rate = background_rate
        self.level = background_level
        self.warmup = warmup_frames
        self._bg = {}  # color name -> float background map
        self._frames = 0

    def _new_pixels(self, name: str, mask: np.ndarray) -> np.ndarray:
        m = (mask > 0).astype(np.float32)
        bg = self._bg.get(name)
        if bg is None or bg.shape != m.shape:
            bg = m.copy()
        # During warmup learn fast; later learn slowly so a march that sits
        # still for a while doesn't vanish instantly.
        rate = 0.5 if self._frames < self.warmup else self.rate
        new = (m > 0) & (bg < self.level)
        self._bg[name] = bg * (1 - rate) + m * rate
        return new.astype(np.uint8) * 255

    def _angle_off(self, start, end) -> float:
        d = np.array(end, float) - np.array(start, float)
        t = self.home - self.enemy
        if np.linalg.norm(d) == 0 or np.linalg.norm(t) == 0:
            return 180.0
        cos = float(np.dot(d, t) / (np.linalg.norm(d) * np.linalg.norm(t)))
        return math.degrees(math.acos(max(-1.0, min(1.0, cos))))

    def check(self, frame: np.ndarray) -> list[March]:
        self._frames += 1
        marches = []
        for name, ranges in self.line_colors.items():
            new = self._new_pixels(name, color_mask(frame, ranges))
            if self._frames <= self.warmup:
                continue
            new = cv2.morphologyEx(new, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
            lines = cv2.HoughLinesP(new, 1, np.pi / 180, threshold=25,
                                    minLineLength=self.min_len, maxLineGap=12)
            if lines is None:
                continue
            for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4):
                a, b = np.array([x1, y1], float), np.array([x2, y2], float)
                da, db = np.linalg.norm(a - self.enemy), np.linalg.norm(b - self.enemy)
                if min(da, db) > self.start_radius:
                    continue  # not leaving his city
                start, end = (a, b) if da <= db else (b, a)
                off = self._angle_off(start, end)
                marches.append(March(tuple(start.astype(int)), tuple(end.astype(int)),
                                     name, off <= self.angle_tol, off))
        # Strongest signal first: heading at you, then most direct.
        marches.sort(key=lambda m: (not m.toward_home, m.angle_off))
        return marches


class FrozenDetector:
    """Notices when the picture stops changing (disconnected, logged out, crashed)."""

    def __init__(self, minutes: float):
        self.seconds = minutes * 60
        self._last = None
        self._since = None

    def check(self, frame: np.ndarray, now: float) -> bool:
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36))
        if self._last is None or np.abs(small.astype(int) - self._last).mean() > 0.3:
            self._last, self._since = small.astype(int), now
            return False
        return now - self._since >= self.seconds
