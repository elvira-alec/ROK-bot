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
    lo, hi = 26 * s, 110 * s
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

BADGE_DIR = __import__("pathlib").Path(__file__).with_name("badges")
_BADGE_SIZE = 32


def badge_glyph(frame, centre, h):
    """The white symbol inside a status badge, as a 32x32 0/1 mask."""
    r = int(0.017 * h)
    x, y = centre
    crop = frame[max(0, y - r):y + r, max(0, x - r):x + r]
    if crop.size == 0:
        return None
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    white = cv2.inRange(hsv, np.array((0, 0, 190), np.uint8), np.array((180, 70, 255), np.uint8))
    white = cv2.resize(white, (_BADGE_SIZE, _BADGE_SIZE), interpolation=cv2.INTER_AREA)
    # keep only the inside of the circle (the ring around it is white-ish too)
    yy, xx = np.mgrid[:_BADGE_SIZE, :_BADGE_SIZE]
    inside = (xx - 15.5) ** 2 + (yy - 15.5) ** 2 < (0.72 * 16) ** 2
    return ((white > 100) & inside).astype(np.float32)


_glyphs = None


def _badge_templates():
    global _glyphs
    if _glyphs is None:
        _glyphs = {}
        for p in sorted(BADGE_DIR.glob("*.png")):
            m = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            _glyphs.setdefault(p.stem.split("-")[0], []).append((m > 127).astype(np.float32))
    return _glyphs


def classify_badge(glyph, among=None) -> str | None:
    """Which symbol a badge shows (pickaxe = gathering, footprints = marching, ...).
    `among` limits the choice, e.g. to the symbols a green badge can have."""
    if glyph is None or glyph.sum() < 8:
        return None
    best, score = None, 0.0
    for name, temps in _badge_templates().items():
        if among and name not in among:
            continue
        for t in temps:
            inter = float((glyph * t).sum())
            union = float(np.maximum(glyph, t).sum())
            iou = inter / union if union else 0.0
            if iou > score:
                best, score = name, iou
    return best if score >= 0.4 else None

@dataclass
class TroopSlot:
    index: int
    badge: tuple      # centre of the small status badge
    portrait: tuple   # where to click to select this troop
    state: str        # gathering, marching, returning, fighting, stopped, idle, scout, other


def _troop_panel_by_colour(frame: np.ndarray, texts: list[Text]):
    """Fallback: find badges by colour (green / purple circles) in the right column.
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
    out = []
    for i, (c, st) in enumerate(slots):
        if st == "gathering":  # any green badge: tell the symbols apart
            st = classify_badge(badge_glyph(frame, c, h)) or "other"
        out.append(TroopSlot(i, c, (int(c[0] - 0.027 * h), int(c[1] - 0.034 * h)), st))
    if not anchor and len(out) < 2:
        return None  # not convincing without the counter
    if not anchor and out:
        # second try at the counter: zoom in on the strip just above the first badge
        bx, by = out[0].badge
        x0c, y0c = max(0, int(bx - 0.08 * h)), max(0, int(by - 0.12 * h))
        crop = frame[y0c:int(by - 0.06 * h), x0c:min(w, int(bx + 0.03 * h))]
        if crop.size:
            big = cv2.resize(crop, None, fx=2.5, fy=2.5, interpolation=cv2.INTER_CUBIC)
            for t in read_text(big):
                m = re.fullmatch(r"(\d)[/|lI1／](\d)", t.text.replace(" ", ""))
                if m:
                    used, total = int(m.group(1)), int(m.group(2))
                    break
    return used, total, out


# ---------------------------------------------------------------- coordinate search bar

@dataclass
class SearchBar:
    x_field: tuple
    y_field: tuple
    button: tuple


def find_search_bar(frame: np.ndarray, texts: list[Text]) -> SearchBar | None:
    """The bar that opens when you tap the coordinates: '#: 4183  X: [ ]  Y: [ ]  (search)'.
    The X and Y boxes start empty, so they're placed between the labels / anchors:
    the kingdom number on the left and the blue search button on the right."""
    H, W = frame.shape[:2]
    body = [t for t in texts if H * 0.1 < t.box[1] < H * 0.45]
    # the kingdom number, read either alone ("4183") or merged with its label ("#:4183")
    kingdom = []
    for t in body:
        m = re.fullmatch(r"(#\s*[:：]?\s*)?(\d{3,5})", t.text.strip())
        if m and t.box[0] > W * 0.2:
            frac = len(m.group(2)) / len(t.text.strip())   # share of the box that is digits
            kingdom.append((t, int(t.box[0] + t.box[2] * (1 - frac / 2))))
    if not kingdom:
        return None
    k, k_centre = kingdom[0]
    row = k.center[1]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, np.array((93, 170, 190), np.uint8), np.array((110, 255, 255), np.uint8))
    n, _, stats, cents = cv2.connectedComponentsWithStats(blue)
    button = None
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if (0.04 * H <= w <= 0.09 * H and 0.04 * H <= h <= 0.09 * H and a > 0.6 * w * h
                and abs(cents[i][1] - row) < 0.04 * H and x > k.box[0]):
            if button is None or x < button[0]:
                button = (int(x), int(cents[i][0]), int(cents[i][1]))
    if not button:
        return None
    left = k_centre
    span = button[1] - left
    # positions measured on the real bar, as a share of kingdom->button distance
    xf = left + 0.454 * span
    yf = left + 0.797 * span
    labels = {t.text.strip().upper().rstrip(":："): t for t in body if abs(t.center[1] - row) < 0.04 * H}
    if "X" in labels and "Y" in labels:  # cross-check with the labels when OCR sees them
        lx, ly = labels["X"], labels["Y"]
        xf = (lx.box[0] + lx.box[2] + ly.box[0]) / 2
        yf = (ly.box[0] + ly.box[2] + button[0]) / 2
    return SearchBar((int(xf), row), (int(yf), row), (button[1], button[2]))


# ---------------------------------------------------------------- zoom level

def count_level_tags(frame: np.ndarray) -> int:
    """Small light tags with a dark number under resource points / cities / barbarians.
    Lots of them = the zoom level you want; none = zoomed out too far."""
    H, W = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    light = cv2.inRange(hsv, np.array((0, 0, 165), np.uint8), np.array((180, 60, 245), np.uint8))
    dark = cv2.inRange(hsv, np.array((0, 0, 0), np.uint8), np.array((180, 255, 95), np.uint8))
    # close the digit holes so a tag is one blob
    blob = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blob)
    count = 0
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (0.008 * H <= h <= 0.03 * H and 0.016 * H <= w <= 0.05 * H):
            continue
        if not (1.1 <= w / h <= 3.2) or a < 0.5 * w * h:
            continue
        if not (0.05 * W < x < 0.88 * W and 0.08 * H < y < 0.86 * H):
            continue  # skip the HUD around the edges
        inner = dark[y:y + h, x:x + w]
        if 0.06 <= inner.mean() / 255 <= 0.5:
            count += 1
    return count


_globe = None


def has_world_button(frame: np.ndarray) -> float:
    """The globe button (bottom right) only shows when zoomed out too far.
    Returns the best match score (0-1)."""
    global _globe
    if _globe is None:
        _globe = cv2.imread(str(__import__("pathlib").Path(__file__).with_name("hud") / "globe.png"))
    H, W = frame.shape[:2]
    region = frame[int(0.75 * H):, int(0.75 * W):]
    best = 0.0
    for k in (0.9, 1.0, 1.1):  # small size differences between screens
        sc = H / 1320.0 * k
        t = cv2.resize(_globe, None, fx=sc, fy=sc, interpolation=cv2.INTER_AREA)
        if t.shape[0] >= region.shape[0] or t.shape[1] >= region.shape[1]:
            continue
        best = max(best, float(cv2.matchTemplate(region, t, cv2.TM_CCOEFF_NORMED).max()))
    return best


def zoom_level(frame, texts) -> str:
    """'close' (3D view), 'patrol' (simplified map with resource points), or 'far'
    (zoomed out so far that resource points and armies are hidden)."""
    if not is_simple_view(texts, frame):
        return "close"
    return "far" if has_world_button(frame) > 0.8 else "patrol"



# ---------------------------------------------------------------- troop panel by layout

# Measured on real screenshots: badge of commander i sits at
# (counter_x + 0.023H, counter_y + 0.086H + i * 0.0913H).
_BADGE_DX, _BADGE_DY, _BADGE_STEP = 0.023, 0.086, 0.0913


def _best_glyph(frame, centre, h, among):
    """Read the symbol, allowing the badge to be a pixel or two off from the
    layout (small screens blur the symbol)."""
    x, y = centre
    step = max(1, int(round(h / 660)))
    best, score = None, 0.0
    temps = _badge_templates()
    for dx in (-2 * step, -step, 0, step, 2 * step):
        for dy in (-2 * step, -step, 0, step, 2 * step):
            g = badge_glyph(frame, (x + dx, y + dy), h)
            if g is None or g.sum() < 8:
                continue
            for name in among:
                for t in temps.get(name, []):
                    union = float(np.maximum(g, t).sum())
                    iou = float((g * t).sum()) / union if union else 0.0
                    if iou > score:
                        best, score = name, iou
    return best if score >= 0.4 else None


def badge_state(frame, centre, h) -> str:
    """What a commander's status badge says, from its colour and symbol."""
    x, y = centre
    R = max(4, int(0.016 * h))             # badge radius
    crop = frame[max(0, y - R):y + R + 1, max(0, x - R):x + R + 1]
    hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
    yy, xx = np.mgrid[:crop.shape[0], :crop.shape[1]]
    d = np.hypot(xx - (x - max(0, x - R)), yy - (y - max(0, y - R)))
    p = hsv[(d > 0.45 * R) & (d < 0.85 * R)]   # the coloured ring around the white symbol
    sat = p[(p[:, 1] > 120) & (p[:, 2] > 90)]
    if len(sat) < 0.3 * len(p):
        return "idle"                      # no coloured badge
    hue = float(np.median(sat[:, 0]))
    if 40 <= hue <= 80:                    # green
        return _best_glyph(frame, centre, h, ("gathering", "marching")) or "other"
    if 9 <= hue <= 25:                     # orange
        return "returning"
    if hue <= 8 or hue >= 170:             # red
        return _best_glyph(frame, centre, h, ("fighting", "stopped")) or "fighting"
    if 125 <= hue <= 165:
        return "scout"
    return "other"


def troop_panel(frame: np.ndarray, texts: list[Text]):
    """Your armies in the column on the right (under the 'N/M' counter).
    Returns (used, total, [TroopSlot]) or None if the panel isn't visible.
    used/total are None when the 'N/M' counter couldn't be read."""
    h, w = frame.shape[:2]
    anchor = None
    for t in texts:
        m = re.fullmatch(r"(\d)[/|lI1／](\d)", t.text.replace(" ", ""))
        if m and t.box[0] > w * 0.8 and t.box[1] < h * 0.4:
            anchor = (t, int(m.group(1)), int(m.group(2)))
            break
    if not anchor:
        return _troop_panel_by_colour(frame, texts)
    t, used, total = anchor
    cx, cy = t.center
    slots = []
    for i in range(used):
        badge = (int(cx + _BADGE_DX * h), int(cy + (_BADGE_DY + i * _BADGE_STEP) * h))
        if badge[1] >= h:
            break
        slots.append(TroopSlot(i, badge, (int(badge[0] - 0.027 * h), int(badge[1] - 0.034 * h)),
                               badge_state(frame, badge, h)))
    # scouts sit below the commanders (purple badges); keep the colour finder for those
    fallback = _troop_panel_by_colour(frame, texts)
    last = slots[-1].badge[1] if slots else cy
    if fallback:
        for s2 in fallback[2]:
            if s2.state == "scout" and s2.badge[1] > last + 0.05 * h:
                slots.append(TroopSlot(len(slots), s2.badge, s2.portrait, "scout"))
    return used, total, slots


# ---------------------------------------------------------------- player names on the map

def _norm(s: str) -> str:
    """Letters and digits only, upper case; common OCR mix-ups folded together."""
    s = s.upper()
    s = s.translate(str.maketrans({"0": "O", "1": "I", "L": "I", "|": "I", "5": "S", "8": "B"}))
    return re.sub(r"[^A-Z0-9]", "", s)


def name_score(text: str, name: str) -> float:
    """How well an OCR'd label matches a player name like '[P83L]JONH WICK' (0-1).
    The alliance tag is optional; OCR often garbles the brackets around it."""
    from difflib import SequenceMatcher
    t, full = _norm(text), _norm(name)
    bare = _norm(re.sub(r"^\[[^\]]*\]", "", name))
    best = 0.0
    for target in {full, bare}:
        if not target or len(t) < max(3, len(target) // 2):
            continue
        best = max(best, SequenceMatcher(None, t, target).ratio())
        if target in t:                      # e.g. tag read wrong, name read right
            best = max(best, 0.9)
        # the name at the end of the label (after a garbled tag)
        tail = t[-len(target):]
        best = max(best, SequenceMatcher(None, tail, target).ratio())
    return best


@dataclass
class NameLabel:
    text: str
    pos: tuple        # centre of the label in frame pixels
    kind: str         # "city" (label on a dark plate) or "army" (floating over the map)
    score: float


def find_player(frame: np.ndarray, texts: list[Text], name: str, min_score=0.75):
    """Labels on screen that carry this player's name: his city and his armies."""
    H, W = frame.shape[:2]
    out = []
    for t in texts:
        if t.box[1] > 0.88 * H or t.box[1] < 0.06 * H:   # chat at the bottom, HUD at the top
            continue
        if ":" in t.text or "#" in t.text:                # chat lines / coordinates
            continue
        sc = name_score(t.text, name)
        if sc < min_score:
            continue
        out.append(NameLabel(t.text, t.center, label_kind(frame, t), sc))
    return out


def label_kind(frame, t: Text) -> str:
    """A city's name has its level tag (small light hexagon with a dark number)
    right underneath; an army's name doesn't."""
    H = frame.shape[0]
    x, y, w, h = t.box
    cx = x + w // 2
    x0, x1 = max(0, int(cx - 0.035 * H)), int(cx + 0.035 * H)
    y0, y1 = y + h - int(0.004 * H), int(y + h + 0.035 * H)
    region = frame[max(0, y0):y1, x0:x1]
    if region.size == 0:
        return "army"
    hsv = cv2.cvtColor(region, cv2.COLOR_BGR2HSV)
    light = cv2.inRange(hsv, np.array((0, 0, 165), np.uint8), np.array((180, 60, 255), np.uint8))
    dark = cv2.inRange(hsv, np.array((0, 0, 0), np.uint8), np.array((180, 255, 80), np.uint8))
    blob = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(blob)
    for i in range(1, n):
        bx, by, bw, bh, a = st[i]
        if (0.008 * H <= bh <= 0.03 * H and 0.012 * H <= bw <= 0.05 * H and 1.0 <= bw / bh <= 3.5
                and a > 0.45 * bw * bh and 0.05 <= dark[by:by + bh, bx:bx + bw].mean() / 255 <= 0.55):
            return "city"
    return "army"
