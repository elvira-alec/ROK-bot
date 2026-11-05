"""Reading the real Rise of Kingdoms screen.

Built from real screenshots (tests/real_screens/). Sizes are given as a
fraction of the screen height so it works at any window size.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import cv2
import numpy as np

# ---------------------------------------------------------------- text (OCR)

_ocr = None


def ocr_engine():
    global _ocr
    if _ocr is None:
        from rapidocr_onnxruntime import RapidOCR  # loaded on first use (slow import)
        _ocr = RapidOCR()
    return _ocr


@dataclass
class Text:
    text: str
    box: tuple  # x, y, w, h in frame pixels
    conf: float

    @property
    def center(self):
        x, y, w, h = self.box
        return (x + w // 2, y + h // 2)


def read_text(frame: np.ndarray, region=None) -> list[Text]:
    """All text on screen (or inside region = (x, y, w, h))."""
    ox = oy = 0
    img = frame
    if region:
        ox, oy, w, h = (int(v) for v in region)
        img = frame[oy:oy + h, ox:ox + w]
    res, _ = ocr_engine()(img)
    out = []
    for box, text, conf in res or []:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append(Text(text, (int(min(xs)) + ox, int(min(ys)) + oy,
                               int(max(xs) - min(xs)), int(max(ys) - min(ys))), float(conf)))
    return out


def find_text(texts: list[Text], pattern: str):
    rx = re.compile(pattern, re.I)
    return [t for t in texts if rx.search(t.text.replace(" ", ""))]


COORD_RX = re.compile(r"#?(\d{3,5})?X[:：]?(\d{1,4})Y[:：]?(\d{1,4})", re.I)


def parse_coords(s: str):
    m = COORD_RX.search(s.replace(" ", "").replace("（", "(").replace("）", ")"))
    if not m:
        return None
    k = int(m.group(1)) if m.group(1) else None
    return k, int(m.group(2)), int(m.group(3))


def camera_coords(frame: np.ndarray, texts: list[Text] | None = None):
    """Where the camera is: the '#4183 X:209 Y:418' readout at the top left.
    Returns (kingdom, x, y, text) or None."""
    h, w = frame.shape[:2]
    if texts is None:
        texts = read_text(frame, (0, 0, int(w * 0.4), int(h * 0.08)))
    for t in texts:
        if t.box[1] > h * 0.1:
            continue
        c = parse_coords(t.text)
        if c:
            return (*c, t)
    return None


def is_simple_view(texts: list[Text], frame) -> bool:
    """Zoomed-out map: the power / VIP block at the top left is hidden."""
    h = frame.shape[0]
    top = [t for t in texts if t.box[1] < h * 0.15]
    return not find_text(top, r"^VIP\d*$") and not find_text(top, r"^\d{1,3}(,\d{3})+$")


# ---------------------------------------------------------------- march lines

@dataclass
class MarchLine:
    p1: tuple
    p2: tuple
    color: str      # "white" (other players), "green" (you / alliance), "red" (hostile)
    dots: int
    spacing: float

    @property
    def length(self):
        return math.dist(self.p1, self.p2)

    def distance_to(self, p) -> float:
        """Shortest distance from point p to this line segment."""
        a, b, p = np.array(self.p1, float), np.array(self.p2, float), np.array(p, float)
        ab = b - a
        t = 0.0 if not ab.any() else float(np.clip(np.dot(p - a, ab) / np.dot(ab, ab), 0, 1))
        return float(np.linalg.norm(a + t * ab - p))

    def end_near(self, p, radius) -> bool:
        return min(math.dist(self.p1, p), math.dist(self.p2, p)) <= radius


DOT_COLORS = {
    # HSV ranges (OpenCV: H 0-180). Measured on real screenshots.
    "white": [((0, 0, 205), (180, 45, 255))],
    "green": [((50, 150, 110), (75, 255, 235))],
    "red": [((0, 150, 150), (8, 255, 255)), ((172, 150, 150), (180, 255, 255))],
}


def _dot_centers(frame, ranges, H):
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    mask = np.zeros(frame.shape[:2], np.uint8)
    for lo, hi in ranges:
        mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(mask, connectivity=8)
    s = H / 1320.0
    amin, amax, dmax = 5 * s * s, 110 * s * s, 14 * s
    keep = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if amin <= a <= amax and w <= dmax and h <= dmax and max(w, h) <= 2.6 * max(1, min(w, h)):
            keep.append(cents[i])
    return np.array(keep, float).reshape(-1, 2)


def _chains(pts, H, min_dots):
    """Find runs of evenly spaced, lined-up dots (march lines are dotted)."""
    s = H / 1320.0
    dmin, dmax = 7 * s, 30 * s
    if len(pts) < min_dots:
        return []
    cell = dmax
    grid = {}
    for i, (x, y) in enumerate(pts):
        grid.setdefault((int(x // cell), int(y // cell)), []).append(i)

    def near(p, r):
        cx, cy = int(p[0] // cell), int(p[1] // cell)
        best, bd = None, r
        for gx in (cx - 1, cx, cx + 1):
            for gy in (cy - 1, cy, cy + 1):
                for j in grid.get((gx, gy), ()):
                    d = math.dist(pts[j], p)
                    if d <= bd:
                        best, bd = j, d
        return best

    used = set()
    chains = []
    for i in range(len(pts)):
        if i in used:
            continue
        a = pts[i]
        cx, cy = int(a[0] // cell), int(a[1] // cell)
        cands = [j for gx in (cx - 1, cx, cx + 1) for gy in (cy - 1, cy, cy + 1)
                 for j in grid.get((gx, gy), ()) if j != i and dmin <= math.dist(pts[j], a) <= dmax]
        best = None
        for j in cands:
            v = pts[j] - a
            members = [i, j]
            for direction in (1, -1):  # walk forwards from j, backwards from i
                cur = pts[j] if direction == 1 else a
                step = v * direction
                misses = 0
                while misses <= 3:     # allow gaps (icons / troops sit on the line)
                    k = near(cur + step * (misses + 1), max(2.5 * s, 0.28 * np.linalg.norm(v)))
                    if k is None or k in members:
                        misses += 1
                        continue
                    # re-centre the step on what we found to follow the line exactly
                    step = (pts[k] - cur) / (misses + 1)
                    cur = pts[k]
                    members.append(k)
                    misses = 0
            if len(members) >= min_dots and (best is None or len(members) > len(best[0])):
                best = (members, float(np.linalg.norm(v)))
        if best:
            used.update(best[0])
            chains.append(best)
    return chains


def find_march_lines(frame: np.ndarray, min_dots=8, colors=None) -> list[MarchLine]:
    H = frame.shape[0]
    lines = []
    for name, ranges in (colors or DOT_COLORS).items():
        pts = _dot_centers(frame, ranges, H)
        for members, spacing in _chains(pts, H, min_dots):
            p = pts[members]
            # endpoints = extreme dots along the main direction
            d = p - p.mean(axis=0)
            u = np.linalg.svd(d, full_matrices=False)[2][0]
            t = d @ u
            # straightness check: march lines are straight
            off = np.abs(d @ np.array([-u[1], u[0]]))
            if off.max() > 6 * H / 1320.0:
                continue
            a, b = p[t.argmin()], p[t.argmax()]
            lines.append(MarchLine((int(a[0]), int(a[1])), (int(b[0]), int(b[1])), name,
                                   len(members), spacing))
    return _merge_lines(lines, H)


def _merge_lines(lines, H):
    """Join pieces of the same line (split by an icon or the troop panel)."""
    s = H / 1320.0
    lines = sorted(lines, key=lambda m: -m.length)
    out: list[MarchLine] = []
    for m in lines:
        mu = np.subtract(m.p2, m.p1) / max(m.length, 1)
        joined = False
        for k in out:
            if k.color != m.color:
                continue
            ku = np.subtract(k.p2, k.p1) / max(k.length, 1)
            if abs(float(np.dot(mu, ku))) < math.cos(math.radians(4)):
                continue
            if k.distance_to(m.p1) > 400 * s and k.distance_to(m.p2) > 400 * s:
                pass
            # perpendicular distance of m's points from k's infinite line
            nrm = np.array([-ku[1], ku[0]])
            if max(abs(np.dot(np.subtract(m.p1, k.p1), nrm)),
                   abs(np.dot(np.subtract(m.p2, k.p1), nrm))) > 8 * s:
                continue
            pts = np.array([k.p1, k.p2, m.p1, m.p2], float)
            t = (pts - pts[0]) @ ku
            k.p1 = tuple(int(v) for v in pts[t.argmin()])
            k.p2 = tuple(int(v) for v in pts[t.argmax()])
            k.dots += m.dots
            joined = True
            break
        if not joined:
            out.append(m)
    return out


# ---------------------------------------------------------------- army icons

def find_army_icons(frame: np.ndarray) -> list[tuple]:
    """Other players' armies on the zoomed-out map: a grey-lavender diamond with
    a white troop symbol. Returns centre points."""
    H = frame.shape[0]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    fill = cv2.inRange(hsv, np.array((95, 12, 120), np.uint8), np.array((130, 60, 215), np.uint8))
    glyph = cv2.inRange(hsv, np.array((0, 0, 215), np.uint8), np.array((180, 40, 255), np.uint8))
    m = cv2.morphologyEx(fill | glyph, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, labels, stats, cents = cv2.connectedComponentsWithStats(m)
    s = H / 1320.0
    lo, hi = 45 * s, 110 * s
    out = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (lo <= w <= hi and lo <= h <= hi and 0.75 < w / h < 1.33):
            continue
        comp = (labels[y:y + h, x:x + w] == i).astype(np.uint8)
        filled = cv2.morphologyEx(comp, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
        area = filled.sum() / float(w * h)
        if not 0.38 <= area <= 0.66:      # a diamond fills about half its box
            continue
        # the four corners of the box are empty for a diamond
        k = max(2, int(0.2 * min(w, h)))
        corners = [filled[:k, :k], filled[:k, -k:], filled[-k:, :k], filled[-k:, -k:]]
        if max(c.mean() for c in corners) > 0.25:
            continue
        # needs both the grey fill and the white symbol
        f = fill[y:y + h, x:x + w][comp > 0].mean() / 255
        g = glyph[y:y + h, x:x + w][comp > 0].mean() / 255
        if f > 0.35 and g > 0.05:
            out.append((int(cents[i][0]), int(cents[i][1])))
    return out


# ---------------------------------------------------------------- your troops

@dataclass
class TroopSlot:
    index: int
    badge: tuple      # centre of the small status badge
    portrait: tuple   # where to click to select this troop
    state: str        # "gathering", "scout", "other"


def troop_panel(frame: np.ndarray, texts: list[Text]):
    """Your armies in the column on the right (under the 'N/M' counter).
    Returns (used, total, [TroopSlot]) or None if the panel isn't visible.
    used/total are None when the 'N/M' counter couldn't be read."""
    h, w = frame.shape[:2]
    anchor = None
    for t in texts:
        # "3/3"; OCR sometimes reads the slash as 1, l, I or |
        m = re.fullmatch(r"(\d)[/|lI1／](\d)", t.text.replace(" ", ""))
        if m and t.box[0] > w * 0.8 and t.box[1] < h * 0.4:
            anchor = (t, int(m.group(1)), int(m.group(2)))
            break
    if anchor:
        t, used, total = anchor
        cx = t.center[0]
        x0, x1 = int(cx - 0.07 * h), int(min(w, cx + 0.07 * h))
        y0, y1 = t.box[1] + t.box[3], int(h * 0.82)
    else:
        # counter not read: look for the badge column at the right edge anyway
        used = total = None
        x0, x1 = int(w - 0.3 * h), w
        y0, y1 = int(h * 0.15), int(h * 0.82)
    hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    r_lo, r_hi = 0.009 * h, 0.019 * h
    slots = []
    for name, ranges in (("gathering", [((45, 140, 120), (75, 255, 255))]),
                         ("scout", [((135, 120, 120), (160, 255, 255))])):
        mask = np.zeros(hsv.shape[:2], np.uint8)
        for lo, hi in ranges:
            mask |= cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        n, _, stats, cents = cv2.connectedComponentsWithStats(mask)
        for i in range(1, n):
            bx, by, bw, bh, a = stats[i]
            r = (bw + bh) / 4
            fill = a / max(1, bw * bh)
            if r_lo <= r <= r_hi and 0.75 < bw / max(1, bh) < 1.33 and fill > 0.45:
                c = (int(cents[i][0]) + x0, int(cents[i][1]) + y0)
                slots.append((c, name))
    # Real badges all sit in one column at the portraits' bottom-right corner;
    # drop look-alikes (e.g. a purple dress) that are out of line.
    if slots:
        xs = np.array([c[0] for c, _ in slots], float)
        col = max(xs, key=lambda x: (np.abs(xs - x) <= 0.012 * h).sum())
        slots = [(c, st) for c, st in slots if abs(c[0] - col) <= 0.012 * h]
    slots.sort(key=lambda s: s[0][1])
    dedup = []
    for c, st in slots:
        if not dedup or c[1] - dedup[-1][0][1] > 0.02 * h:
            dedup.append((c, st))
    slots = dedup
    out = [TroopSlot(i, c, (int(c[0] - 0.027 * h), int(c[1] - 0.034 * h)), st)
           for i, (c, st) in enumerate(slots)]
    if not anchor and len(out) < 2:
        return None  # not convincing without the counter
    return used, total, out
