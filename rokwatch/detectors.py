"""Image checks that look at a game screenshot (a BGR numpy array)."""

from __future__ import annotations

import math
from dataclasses import dataclass, field
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


# ---------------------------------------------------------------- attack alert

class AttackAlertDetector:
    """Spots the red flashing screen border (and optional warning-icon templates).

    The border has to be clearly redder than normal for this spot on the map,
    so a map that's always a bit red at the edges doesn't keep alerting.
    """

    def __init__(self, edge_width=0.04, red_fraction=0.30, confirm_frames=2,
                 template_dir=None, template_threshold=0.82, min_jump=0.15,
                 steady_frames=10):
        self.edge_width = edge_width
        self.red_fraction = red_fraction
        self.confirm_frames = confirm_frames
        self.template_threshold = template_threshold
        self.min_jump = min_jump
        self.templates = []
        if template_dir and Path(template_dir).is_dir():
            for p in sorted(Path(template_dir).glob("*.png")):
                img = cv2.imread(str(p))
                if img is not None:
                    self.templates.append((p.name, img))
        self.steady_frames = steady_frames
        self._streak = 0
        self._normal = None  # usual red level at the edges
        self._recent = []    # red levels during the current hit streak

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
        normal = self._normal if self._normal is not None else 0.0
        edge_hit = frac >= self.red_fraction and frac - normal >= self.min_jump
        tpl = self.match_template(frame) if self.templates else None
        hit = edge_hit or tpl is not None
        # Red that sits perfectly still for a while is part of the map (e.g.
        # red territory), not the pulsing warning: learn it as normal.
        if edge_hit and tpl is None:
            self._recent = (self._recent + [frac])[-self.steady_frames:]
            if len(self._recent) == self.steady_frames and \
                    max(self._recent) - min(self._recent) < 0.02:
                self._normal, self._recent, self._streak = frac, [], 0
                return None
        else:
            self._recent = []
        if not hit:
            self._normal = frac if self._normal is None else self._normal * 0.9 + frac * 0.1
        # The warning pulses, so allow one missed frame inside a streak.
        if hit:
            self._streak += 1
        else:
            self._streak = max(0, self._streak - 1) if self._streak >= self.confirm_frames else 0
        if hit and self._streak >= self.confirm_frames:
            if tpl:
                return f"warning icon seen ({tpl})"
            return f"red warning on screen edges ({frac:.0%})"
        return None


# ---------------------------------------------------------------- enemy marches

@dataclass
class March:
    start: tuple
    end: tuple
    color: str
    target: str | None     # name of the protected spot it points at, or None
    angle_off: float       # degrees off from pointing straight at that spot
    length: float = 0.0

    @property
    def toward_home(self) -> bool:
        return self.target is not None

    @property
    def heading(self) -> float:
        dx, dy = self.end[0] - self.start[0], self.end[1] - self.start[1]
        return math.degrees(math.atan2(dy, dx)) % 360


def _angle_between(v1, v2) -> float:
    n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
    if n1 == 0 or n2 == 0:
        return 180.0
    cos = float(np.dot(v1, v2) / (n1 * n2))
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


def _heading_diff(a, b) -> float:
    return abs((a - b + 180) % 360 - 180)


class EnemyMarchDetector:
    """Finds new march lines that leave the enemy city and says where they point.

    Two ways of seeing a line, used together:
      * by color (line_colors), and
      * "anything new": pixels that changed a lot from the usual picture.
    Things that are always on screen are learned as background and ignored.
    """

    def __init__(self, enemy_city, protect_points, line_colors: dict | None = None,
                 detect_changes=True, change_threshold=45, start_radius=70,
                 angle_tolerance_degrees=35, arrive_radius=60, min_line_length=35,
                 background_rate=0.05, background_level=0.6, warmup_frames=5):
        self.enemy = np.array(enemy_city, float)
        self.points = [(str(n), np.array(p, float)) for n, p in protect_points]
        self.line_colors = line_colors or {}
        self.detect_changes = detect_changes
        self.change_threshold = change_threshold
        self.start_radius = start_radius
        self.angle_tol = angle_tolerance_degrees
        self.arrive_radius = arrive_radius
        self.min_len = min_line_length
        self.rate = background_rate
        self.level = background_level
        self.warmup = warmup_frames
        self._bg = {}
        self._frames = 0

    def reset(self, enemy_city=None, protect_points=None):
        """Start learning the background again (e.g. after the map moved)."""
        if enemy_city is not None:
            self.enemy = np.array(enemy_city, float)
        if protect_points is not None:
            self.points = [(str(n), np.array(p, float)) for n, p in protect_points]
        self._bg.clear()
        self._frames = 0

    @property
    def learning(self) -> bool:
        return self._frames <= self.warmup

    def _rate(self):
        # Learn fast at first; later learn slowly so a march stays "new" a while.
        return 0.5 if self._frames <= self.warmup else self.rate

    def _new_by_color(self, name, frame, ranges):
        m = (color_mask(frame, ranges) > 0).astype(np.float32)
        bg = self._bg.get(name)
        if bg is None or bg.shape != m.shape:
            bg = m.copy()
        new = (m > 0) & (bg < self.level)
        self._bg[name] = bg * (1 - self._rate()) + m * self._rate()
        return new.astype(np.uint8) * 255

    def _new_by_change(self, frame):
        f = frame.astype(np.float32)
        bg = self._bg.get("_change")
        if bg is None or bg.shape != f.shape:
            bg = f.copy()
        diff = np.abs(f - bg).max(axis=2)
        new = diff > self.change_threshold
        r = self._rate()
        # Don't soak new stuff into the background as fast as normal pixels.
        upd = np.where(new[..., None], r * 0.3, r).astype(np.float32)
        self._bg["_change"] = bg * (1 - upd) + f * upd
        return new.astype(np.uint8) * 255

    def _classify(self, start, end):
        d = end - start
        best_name, best_off = None, 180.0
        closest = 180.0
        for name, p in self.points:
            off = _angle_between(d, p - self.enemy)
            closest = min(closest, off)
            if np.linalg.norm(end - p) <= self.arrive_radius:
                off = min(off, 5.0)  # line ends right on this spot
            if off <= self.angle_tol and off < best_off:
                best_name, best_off = name, off
        return (best_name, best_off) if best_name else (None, closest)

    def _passes_through(self, mask, start, end) -> bool:
        """True if the line carries on past his city on the other side, i.e.
        it's someone else's march passing by rather than one leaving his city."""
        d = end - start
        n = np.linalg.norm(d)
        if n == 0:
            return False
        u = d / n
        # look behind the city: from the city outwards, away from the march
        back = self.enemy - u * np.arange(self.start_radius * 0.8, self.start_radius * 2.0, 2.0)[:, None]
        h, w = mask.shape
        hits = total = 0
        for x, y in back.astype(int):
            if 0 <= x < w and 0 <= y < h:
                total += 1
                if mask[max(0, y - 2):y + 3, max(0, x - 2):x + 3].any():
                    hits += 1
        return total > 5 and hits / total > 0.5

    def _lines(self, mask):
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))
        lines = cv2.HoughLinesP(mask, 1, np.pi / 180, threshold=25,
                                minLineLength=self.min_len, maxLineGap=12)
        return [] if lines is None else np.asarray(lines).reshape(-1, 4)

    def check(self, frame: np.ndarray) -> list[March]:
        self._frames += 1
        masks = {name: self._new_by_color(name, frame, r) for name, r in self.line_colors.items()}
        if self.detect_changes:
            masks["new"] = self._new_by_change(frame)
        if self.learning:
            return []
        marches = []
        for name, mask in masks.items():
            for x1, y1, x2, y2 in self._lines(mask):
                a, b = np.array([x1, y1], float), np.array([x2, y2], float)
                da, db = np.linalg.norm(a - self.enemy), np.linalg.norm(b - self.enemy)
                if min(da, db) > self.start_radius:
                    continue  # not leaving his city
                start, end = (a, b) if da <= db else (b, a)
                if self._passes_through(mask, start, end):
                    continue
                target, off = self._classify(start, end)
                marches.append(March(tuple(int(v) for v in start), tuple(int(v) for v in end),
                                     name, target, off, float(np.linalg.norm(end - start))))
        return merge_marches(marches)


def merge_marches(marches: list[March], same_heading=12.0) -> list[March]:
    """One march often shows up as several pieces (and in several masks).
    Keep the longest piece for each direction, preferring a real color name."""
    marches = sorted(marches, key=lambda m: -m.length)
    kept: list[March] = []
    for m in marches:
        twin = next((k for k in kept if _heading_diff(m.heading, k.heading) < same_heading), None)
        if twin is None:
            kept.append(m)
        elif twin.color == "new" and m.color != "new":
            twin.color = m.color
    kept.sort(key=lambda m: (not m.toward_home, m.angle_off))
    return kept


@dataclass
class MarchMemory:
    """Remembers marches already reported, so one march = one alert."""
    repeat_minutes: float = 10.0
    same_heading: float = 15.0
    _seen: list = field(default_factory=list)  # (heading, time)

    def is_new(self, march: March, now: float) -> bool:
        keep = self.repeat_minutes * 60
        self._seen = [(h, t) for h, t in self._seen if now - t < keep]
        if any(_heading_diff(march.heading, h) < self.same_heading for h, _ in self._seen):
            return False
        self._seen.append((march.heading, now))
        return True

    def clear(self):
        self._seen.clear()


# ---------------------------------------------------------------- map position

class MapShiftTracker:
    """Measures how far the map has slid compared to the calibration picture,
    so positions can follow it. Uses the middle of the screen (HUD buttons
    around the edges don't move with the map)."""

    def __init__(self, reference: np.ndarray, margin=0.15, scale=0.5):
        self.margin = margin
        self.scale = scale
        self.shape = reference.shape[:2]
        self._ref = self._prep(reference)
        self._win = cv2.createHanningWindow(self._ref.shape[::-1], cv2.CV_32F)

    def _prep(self, frame):
        h, w = frame.shape[:2]
        my, mx = int(h * self.margin), int(w * self.margin)
        g = cv2.cvtColor(frame[my:h - my, mx:w - mx], cv2.COLOR_BGR2GRAY)
        g = cv2.resize(g, None, fx=self.scale, fy=self.scale, interpolation=cv2.INTER_AREA)
        return g.astype(np.float32)

    def measure(self, frame: np.ndarray):
        """Return (dx, dy, confidence). Positions should move by (dx, dy)."""
        if frame.shape[:2] != self.shape:
            return 0.0, 0.0, 0.0
        (dx, dy), conf = cv2.phaseCorrelate(self._ref, self._prep(frame), self._win)
        return dx / self.scale, dy / self.scale, float(conf)


# ---------------------------------------------------------------- frozen picture

class FrozenDetector:
    """Notices when the picture stops changing (disconnected, logged out, crashed)."""

    def __init__(self, minutes: float):
        self.seconds = minutes * 60
        self._last = None
        self._since = None

    def check(self, frame: np.ndarray, now: float) -> bool:
        small = cv2.resize(cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), (64, 36),
                           interpolation=cv2.INTER_AREA).astype(int)
        if self._last is None or np.abs(small - self._last).mean() > 0.3:
            self._last, self._since = small, now
            return False
        return now - self._since >= self.seconds
