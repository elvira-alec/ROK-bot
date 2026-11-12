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
    chat_top = 0.86 * H                       # dotted text underlines in the chat box
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
            if a[1] > chat_top and b[1] > chat_top:
                continue
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
    # grey-lavender by day, mauve at night (hue ~115 by day, ~150 at night)
    fill = cv2.inRange(hsv, np.array((95, 8, 120), np.uint8), np.array((165, 60, 215), np.uint8))
    glyph = cv2.inRange(hsv, np.array((0, 0, 215), np.uint8), np.array((180, 40, 255), np.uint8))
    # drop small white specks (the dots of a march line running into the icon), so the
    # line doesn't get glued onto the diamond; symbols and a scout's lit face are bigger
    # (at night the dots are greyish and fall in the fill colours too, so filter both)
    both = fill | glyph
    gn, glab, gst, _ = cv2.connectedComponentsWithStats(both)
    small = np.isin(glab, [j for j in range(1, gn) if gst[j][4] < 90 * (H / 1320.0) ** 2])
    both[small] = 0
    fill[small] = 0
    glyph[small] = 0
    m = cv2.morphologyEx(both, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
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
        if max(c.mean() for c in corners) > 0.25 or not _diamond_not_round(filled > 0):
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


def _badge_column(frame, top, h):
    """Status badge circles (green / orange / red / purple) in the column below `top`.
    Returns [(centre, hue)] from top to bottom."""
    x, y = top
    x0, x1 = max(0, int(x - 0.025 * h)), int(x + 0.025 * h)
    y0, y1 = int(y + 0.04 * h), int(0.86 * h)
    hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    # each status colour on its own, so a badge doesn't merge with an orange
    # background or a gold portrait frame next to it
    ranges = [((40, 120, 90), (80, 255, 255)),     # green: gathering / marching
              ((9, 150, 120), (25, 255, 255)),     # orange: returning
              ((0, 150, 90), (8, 255, 255)),       # red: fighting / stopped
              ((170, 150, 90), (180, 255, 255)),
              ((125, 100, 90), (165, 255, 255))]   # purple: scouts
    out = []
    for lo, hi in ranges:
        mask = cv2.inRange(hsv, np.array(lo, np.uint8), np.array(hi, np.uint8))
        mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))
        n, lab, st, ce = cv2.connectedComponentsWithStats(mask)
        for i in range(1, n):
            bx, by, bw, bh, a = st[i]
            if not (0.016 * h <= max(bw, bh) <= 0.04 * h and 0.7 < bw / max(1, bh) < 1.4):
                continue
            if a < 0.4 * bw * bh:
                continue
            ring = lab[by:by + bh, bx:bx + bw] == i
            # a badge: round, with a white symbol in the middle
            mid = hsv[by + bh // 3:by + 2 * bh // 3, bx + bw // 3:bx + 2 * bw // 3]
            if mid.size and ((mid[..., 1] < 70) & (mid[..., 2] > 180)).mean() < 0.1:
                continue
            hue = float(np.median(hsv[by:by + bh, bx:bx + bw][ring][:, 0]))
            out.append(((int(ce[i][0]) + x0, int(ce[i][1]) + y0), hue))
    out.sort(key=lambda o: o[0][1])
    dedup = []
    for c, hue in out:
        if not dedup or abs(c[1] - dedup[-1][0][1]) >= 0.03 * h:
            dedup.append((c, hue))
    out = dedup
    return out


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
    # The spacing changes with how many rows the panel has, so look for the badge
    # circles in the column under the counter rather than assuming positions.
    found = _badge_column(frame, (int(cx + _BADGE_DX * h), cy), h)
    bx = int(np.median([c[0] for c, _ in found])) if found else int(cx + _BADGE_DX * h)
    # first commander badge: near the usual spot under the counter
    y_first = cy + _BADGE_DY * h
    near_first = [c for c, _ in found if abs(c[1] - y_first) < 0.035 * h]
    if near_first:
        y_first = near_first[0][1]
    # spacing: measured from badges that follow each other, else the usual one
    ys = [c[1] for c, _ in found if c[1] >= y_first - 2]
    gaps = [b - a for a, b in zip(ys, ys[1:]) if 0.08 * h <= b - a <= 0.12 * h]
    step = float(np.median(gaps)) if gaps else _BADGE_STEP * h
    commanders = []
    for i in range(used):
        want = y_first + i * step
        hit = [c for c, _ in found if abs(c[1] - want) < 0.03 * h]
        commanders.append(hit[0] if hit else (bx, int(want)))
    for i, badge in enumerate(commanders):
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



# ---------------------------------------------------------------- map icons: armies vs scouts

def _dark_outline_ratio(frame, centre, size):  # (kept for reference; not used)
    """How much of a diamond's border (just inside its outline) is dark."""
    x, y = centre
    r = size / 2
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    H, W = frame.shape[:2]
    pts = []
    for t in np.linspace(0, 1, 40, endpoint=False):
        # walk round the diamond's four edges, a little inside the tip-to-tip size
        k = 0.88 * r
        corners = [(x, y - k), (x + k, y), (x, y + k), (x - k, y), (x, y - k)]
        seg = int(t * 4)
        f = t * 4 - seg
        (ax, ay), (bx, by) = corners[seg], corners[seg + 1]
        pts.append((int(ax + (bx - ax) * f), int(ay + (by - ay) * f)))
    dark = 0
    for px, py in pts:
        if 0 <= px < W and 0 <= py < H:
            win = hsv[max(0, py - 2):py + 3, max(0, px - 2):px + 3]
            if (win[..., 2] < 95).any():
                dark += 1
    return dark / len(pts)


def icon_kind(frame, c) -> str:
    """'scout' or 'army' for a diamond at c.
    A scout is a plain 3D pyramid: split down its ridge, one face is clearly darker
    than the other and each face is flat (real screens: ~100 apart, spread under 10).
    An army is a flat diamond with a white troop symbol across the middle, so no
    split gives two flat faces with one much darker. The ridge is searched a few pixels either side,
    because the detected centre can be a little off."""
    H = frame.shape[0]
    x, y = c
    r = max(4, int(0.012 * H))
    k = max(2, int(0.004 * H))               # how far off the centre may be
    x0, y0 = max(0, x - r - k), max(0, y - r - k)
    hsv = cv2.cvtColor(frame[y0:y + r + k + 1, x0:x + r + k + 1], cv2.COLOR_BGR2HSV)
    v = hsv[..., 2].astype(float)
    hh, ww = v.shape
    gy, gx = np.mgrid[:hh, :ww]
    for dy in range(-k, k + 1, max(1, k // 2)):
        for dx in range(-k, k + 1):
            cx, cy = x - x0 + dx, y - y0 + dy
            xx, yy = gx - cx, gy - cy
            inside = (np.abs(xx) + np.abs(yy)) < r
            left, right = v[inside & (xx < -1)], v[inside & (xx > 1)]
            if len(left) < 5 or len(right) < 5:
                continue
            ml, mr = float(np.median(left)), float(np.median(right))
            diff = abs(ml - mr)
            ratio = min(ml, mr) / max(ml, mr, 1.0)
            spread = max(float(left.std()), float(right.std()))
            # scout: dark face ~0.57 of the light one; an army's grey fill next to its
            # white symbol is 0.7 or more
            if diff >= 50 and spread <= 20 and ratio <= 0.66:
                return "scout"
    return "army"


def find_map_icons(frame: np.ndarray):
    """Other players' marches on the zoomed-out map: [(centre, kind)], kind is
    "army" (diamond with a troop symbol: horse, catapult, flag...) or "scout"."""
    H = frame.shape[0]
    s = H / 1320.0
    found = [(c, icon_kind(frame, c)) for c in find_army_icons(frame)]
    # armies whose symbol sticks out of the diamond (e.g. a flag) fail the diamond
    # shape test; find those by their outline: a dark ring with light grey inside
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    W = frame.shape[1]
    dark = cv2.inRange(hsv, np.array((0, 0, 0), np.uint8), np.array((180, 110, 150), np.uint8))
    n, lab, st, ce = cv2.connectedComponentsWithStats(dark)
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (40 * s <= w <= 120 * s and 40 * s <= h <= 120 * s and 0.75 < w / h < 1.33):
            continue
        if y < 0.08 * H or y + h > 0.88 * H or x > 0.9 * W:
            continue                          # HUD: resource bar, troop panel, chat
        if not (0.1 <= a / (w * h) <= 0.45):
            continue
        cx, cy = x + w // 2, y + h // 2
        inner = hsv[cy - h // 5:cy + h // 5, cx - w // 5:cx + w // 5]
        if inner.size == 0 or np.median(inner[..., 2]) < 140 or np.median(inner[..., 1]) > 60:
            continue
        if ((inner[..., 2] > 235) & (inner[..., 1] < 30)).mean() < 0.08:
            continue                          # needs a white symbol inside
        if all(np.hypot(cx - f[0][0], cy - f[0][1]) > 0.5 * max(w, h) for f in found):
            if icon_kind(frame, (int(cx), int(cy))) == "army":
                found.append(((int(cx), int(cy)), "army"))
    for c in find_hostile_icons(frame):
        if all(np.hypot(c[0] - f[0][0], c[1] - f[0][1]) > 0.03 * H for f in found):
            found.append((c, "hostile"))
    return found


def _diamond_not_round(comp) -> bool:
    """A diamond is empty at its diagonals (|dx|+|dy| > half the size); a round button
    or a pile of rocks isn't."""
    h, w = comp.shape
    pts = [(0.5 + dx * 0.33, 0.5 + dy * 0.33) for dx in (-1, 1) for dy in (-1, 1)]
    filled = 0
    for fx, fy in pts:
        x, y = int(fx * w), int(fy * h)
        win = comp[max(0, y - 2):y + 3, max(0, x - 2):x + 3]
        filled += win.mean() > 0.5
    return filled <= 1


def find_hostile_icons(frame: np.ndarray) -> list[tuple]:
    """Armies marching against you / your alliance on the zoomed-out map: a crimson
    diamond with a white troop symbol (measured: hue ~177, saturation ~180)."""
    H, W = frame.shape[:2]
    s = H / 1320.0
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    red = (cv2.inRange(hsv, np.array((165, 120, 90), np.uint8), np.array((180, 255, 240), np.uint8))
           | cv2.inRange(hsv, np.array((0, 120, 90), np.uint8), np.array((6, 255, 240), np.uint8)))
    white = cv2.inRange(hsv, np.array((0, 0, 210), np.uint8), np.array((180, 50, 255), np.uint8))
    blob = cv2.morphologyEx(red | white, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, st, ce = cv2.connectedComponentsWithStats(blob)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (40 * s <= w <= 120 * s and 40 * s <= h <= 120 * s and 0.75 < w / h < 1.33):
            continue
        if y < 0.08 * H or y + h > 0.88 * H or x > 0.9 * W:
            continue                                      # HUD
        comp = (lab[y:y + h, x:x + w] == i)
        if not 0.38 <= comp.mean() <= 0.7:
            continue                                      # a diamond fills about half its box
        k = max(2, int(0.2 * min(w, h)))
        corners = [comp[:k, :k], comp[:k, -k:], comp[-k:, :k], comp[-k:, -k:]]
        if max(c.mean() for c in corners) > 0.3 or not _diamond_not_round(comp):
            continue
        r_in = red[y:y + h, x:x + w][comp].mean() / 255
        w_in = white[y:y + h, x:x + w][comp].mean() / 255
        if r_in >= 0.4 and 0.08 <= w_in <= 0.5:           # mostly red, with a white symbol
            out.append((int(ce[i][0]), int(ce[i][1])))
    return out


def _icon_size(frame, c):
    """Tip-to-tip size of the diamond at c (light pixels along the horizontal axis)."""
    H = frame.shape[0]
    x, y = c
    hsv = cv2.cvtColor(frame[max(0, y - 2):y + 3, max(0, x - int(0.06 * H)):x + int(0.06 * H)],
                       cv2.COLOR_BGR2HSV)
    light = ((hsv[..., 2] > 120) & (hsv[..., 1] < 70)).any(axis=0)
    idx = np.nonzero(light)[0]
    return float(idx.max() - idx.min() + 1) if len(idx) else 0.05 * H


# ---------------------------------------------------------------- the game's own notifications

TOASTS = [
    # (kind, words that must all appear, letters only, upper case)
    ("scouted", ("SCOUTED",)),
    ("attacked", ("ATTACK",)),
    ("rallied", ("RALLY",)),
]


def read_toasts(frame: np.ndarray, texts: list[Text]) -> list[tuple[str, str]]:
    """Pop-up notifications the game shows at the bottom right, e.g.
    'You have been scouted - Enemy scouted Troops.' Returns [(kind, text)]."""
    H, W = frame.shape[:2]
    box = [t for t in texts if t.box[0] > 0.55 * W and t.box[1] > 0.75 * H
           and len(re.sub(r"[^A-Za-z]", "", t.text)) >= 4]
    if not box:
        return []
    joined = " ".join(t.text for t in sorted(box, key=lambda t: (t.box[1], t.box[0])))
    letters = re.sub(r"[^A-Z]", "", joined.upper())
    out = []
    for kind, words in TOASTS:
        if all(w in letters for w in words):
            out.append((kind, joined))
    # "You have been s..." cut off by the screen edge: the second line says what
    if not out and "YOUHAVEBEEN" in letters and "SCOUT" in letters:
        out.append(("scouted", joined))
    return out


# ---------------------------------------------------------------- the search (magnifier) button

# The magnifier ("search") icon after the '#4183 X:… Y:…' readout, cut from a real
# screenshot (1320 px tall game), stored as the darkest channel so the white icon
# still stands out under the red under-attack tint or on autumn maps.
_MAGNIFIER_PNG = (
    "iVBORw0KGgoAAAANSUhEUgAAACgAAAAnCAAAAABYw1VkAAAEc0lEQVQ4ESXB7W9UZR7H4e/nd05nGNrOtJTCPMTS"
    "aksZSywDbNhgFsFk1bgsqJvVxBfGZF/tC/4m479gSGTjAsaFVgtsKVoKlBZaxDItfZoyw8ycOfftEK+LjAUWGG2i"
    "TbwiQCABEhJGj1kIBgIBwhAChAAhISATBAEYAgSINiFACBASiExoZpgIwsSuVDJhUitqNJothJAJIRC91obJwp59"
    "A0OFvoSizfKT5WdbXki0SSDoNTOMjq7+gcHBXF93qOjF1urTJ0vPq7GXAIEQvYa19RZGRocKqUTgvVzU2HjyYH6l"
    "0nICAUL0WFuYPHR4fDifrteqDZ/oTKWi9cWZuUfVyAkECAaaLcKu3Mnjo91BbXV1/aV279nbn7H6/M83V7abQggQ"
    "xZ26duVKp8ezL8qLC083G0rtyQ68XujbuPe/uac7QgIExUqdzOGPj+SZn56cLTtBsu/AiaPjqfLNibsragMBh3bq"
    "DL79Rd4v/3Tj9kYdkyzZOXLszEji2ZWpe40YCQSHao34T+9/nlyZ/OH2EhaAvHN7iu++M6jL1/+/1UAgRNE3ds6f"
    "Pd2Yuzj1uB4EBpJ3iX0H/3k08+Pk1LOqBAiKiqPPPiw9u3Xxl7IwA+G9dfV/dvLAzM0fl7clAaIo7778cOT+5KWH"
    "GwFgoLaOzn+cHnswPbmw6QQIimrVL5wvTF+7slQxXjEJHyTPnSk9vjMxv+4EEgx5H184X7h97erylvEHSWHq7OnS"
    "o5mJh+tOIC8KEv8+N3h38vLiJuIVIRJd504dnp+eeLThBN6LgsSXfx9bvvWf2TVz3oQJ2e49n7w9fOfGxK/bDog9"
    "5BEf/e3k9t1Lt39tOo8hRLJv8KNj+65P/FSuygsvkRf6y3tnWf5h6sGaEyYJywyW/joc/vfarUoTLzkv8hLD7/wr"
    "vXPv+o1ZBxLewnzpg7HujW8nZp0QqsciL9E7/uloZmf2zkx5syqps3f/wbdO9EfLl6YWBBKxh7xEx8Cpk6Pp1cdz"
    "i0/XY9GXHzo4/EZi5/HVWwtNh5Aw8hK++8C7fx4NauXl357Xfbgv91p2b5rq2o3p2bWXEiZBXsKHnUdKbxUywfZ2"
    "peE7MunujtA6qT+6P/twZRuQIIfw8oU33iwO7Zdrxj5ImKvWW7lMcntl4Zf5pSj2AnICuThMF8aKI+ndSXBRbatc"
    "icaGssHLtbmfZ1ZrToKcBN75MLV3f76vN5Og9WLr+UbNjx8p9YRReXH2zpOKBDkJ4Z3z4a6unp50wlovdrZqLXv9"
    "6JnB/l0vny9NzdyXICch5L2LYx90dIDi2Fvb8PsnDvarWb37/TcS5CSE5D04YUiCwOTSA6eOv9kdNFa++0oSWYQQ"
    "KMB7tYFZgGKFx44fe63bla98LYmsQCIMEGrzMpkBLo76h0vj2eDh5HeSyAoFhoGQSULIQHKxpdKZlNUqFUlkMQWG"
    "QLRJCBDI+2bUct6CVLckskGAQCDaJGRIMsmiKGp5glSXJAoGAoFokxAgmcC3mi2njuRuSb8D6ePKjHFLLjoAAAAA"
    "SUVORK5CYII="
)
_magnifier = None


def find_magnifier(frame: np.ndarray, near: Text | None = None):
    """(x, y, score) of the magnifier's centre in the top bar, or None.
    near: the coordinates text; the search then stays in its row."""
    global _magnifier
    if _magnifier is None:
        import base64
        raw = np.frombuffer(base64.b64decode(_MAGNIFIER_PNG), np.uint8)
        _magnifier = cv2.imdecode(raw, cv2.IMREAD_GRAYSCALE).astype(np.float32)
    H, W = frame.shape[:2]
    s = H / 1320
    best = None
    x0, x1, y0, y1 = 0, int(0.5 * W), 0, int(0.09 * H)
    if near is not None and near.box[2] < 0.45 * W:
        tx, ty, tw, th = near.box
        cy = ty + th // 2
        x0, x1 = max(0, tx + tw // 3), min(W, tx + tw + int(0.07 * H))
        y0, y1 = max(0, cy - int(0.05 * H)), min(H, cy + int(0.05 * H))
    g = frame[y0:y1, x0:x1].min(axis=2).astype(np.float32)
    # UI size isn't always proportional to the window height: try a range of sizes
    for k in (1.0, 0.9, 1.1, 0.8, 1.25, 0.7, 1.4, 0.6, 1.6):
        T = cv2.resize(_magnifier, None, fx=s * k, fy=s * k, interpolation=cv2.INTER_AREA)
        if T.shape[0] < 8 or g.shape[0] <= T.shape[0] or g.shape[1] <= T.shape[1]:
            continue
        r = cv2.matchTemplate(g, T, cv2.TM_CCOEFF_NORMED)
        _, mv, _, ml = cv2.minMaxLoc(r)
        if best is None or mv > best[2]:
            best = (x0 + ml[0] + T.shape[1] // 2, y0 + ml[1] + T.shape[0] // 2, float(mv))
        if mv > 0.9:
            break
    return best if best and best[2] >= 0.7 else None


def search_button(frame: np.ndarray, coords_text: Text | None = None) -> tuple:
    """Where to tap to open the coordinate search: the centre of the magnifier right
    after the '#4183 X:… Y:…' readout (NOT the orange star next to it)."""
    m = find_magnifier(frame, coords_text)
    if m:
        return m[0], m[1]
    # not found (unusual skin?): just after the readout, measured on real screens
    H = frame.shape[0]
    x, y, w, h = coords_text.box
    txt = coords_text.text
    m = re.search(r"\d\W*([QqOoGg9])\W*$", txt.replace(" ", ""))
    if m:          # OCR read the magnifier as a 'Q': tap that letter's share of the box
        i = txt.rfind(m.group(1))
        return int(x + w * (i + 0.5) / len(txt)), y + h // 2
    return int(x + w + 0.025 * H), y + h // 2
