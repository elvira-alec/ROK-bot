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


_ocr_strip = None


def ocr_strip_engine():
    """For small strips (the coordinates readout): the reader normally blows a strip up
    until its short side is 736 px before finding the text (~5x bigger, ~1 s). This one
    reads it at its own size (~0.1-0.2 s) - the text there is big enough."""
    global _ocr_strip
    if _ocr_strip is None:
        from rapidocr_onnxruntime import RapidOCR
        eng = RapidOCR()
        for op in eng.text_detector.preprocess_op:
            if hasattr(op, "limit_type"):
                op.limit_type, op.limit_side_len = "max", 1280
        eng.width_height_ratio = -1          # a long strip is still looked through for text
        _ocr_strip = eng
    return _ocr_strip


@dataclass
class Text:
    text: str
    box: tuple  # x, y, w, h in frame pixels
    conf: float

    @property
    def center(self):
        x, y, w, h = self.box
        return (x + w // 2, y + h // 2)


def read_text(frame: np.ndarray, region=None, strip=False) -> list[Text]:
    """All text on screen (or inside region = (x, y, w, h)). strip: a small strip, read
    at its own size (much quicker)."""
    ox = oy = 0
    img = frame
    if region:
        ox, oy, w, h = (int(v) for v in region)
        img = frame[oy:oy + h, ox:ox + w]
    res, _ = (ocr_strip_engine() if strip else ocr_engine())(img)
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


COORD_RX = re.compile(r"#?(\d{3,5})?X[:：;.\-]?(\d{1,4})[.,;:]?Y[:：;.\-]?(\d{1,4})", re.I)


def parse_coords(s: str):
    s = s.replace(" ", "").replace("（", "(").replace("）", ")")
    s = re.sub(r"(?<=X[:：;.\-])(\d*)[OoQD](?=\d*Y)", r"\g<1>0", s, flags=re.I)  # 'X:18QY' = X:180
    m = COORD_RX.search(s)
    if not m:
        return None
    k = int(m.group(1)) if m.group(1) else None
    x, y = int(m.group(2)), int(m.group(3))
    # the map is 0-1199: a 4th digit is the magnifier next to it read as a number
    if x > 1199 and len(m.group(2)) == 4:
        x //= 10
    if y > 1199 and len(m.group(3)) == 4:
        y //= 10
    return k, x, y


def camera_coords(frame: np.ndarray, texts: list[Text] | None = None):
    """Where the camera is: the '#4183 X:209 Y:418' readout at the top left.
    Returns (kingdom, x, y, text) or None."""
    h, w = frame.shape[:2]
    if texts is None:
        texts = top_texts(frame)
    for t in texts:
        if t.box[1] > h * 0.1:
            continue
        c = parse_coords(t.text)
        if c:
            return (*c, t)
    # read in two pieces ('#4183 X:211' and ':422', the 'Y' lost): join a piece that ends
    # with the X number to the number just right of it on the same line
    for t in texts:
        m = re.search(r"X[:：;.\-]?(\d{1,4})$", t.text.replace(" ", ""), re.I)
        if t.box[1] > h * 0.1 or not m:
            continue
        right = [u for u in texts if u is not t and abs(u.center[1] - t.center[1]) < 0.012 * h
                 and 0 <= u.box[0] - (t.box[0] + t.box[2]) < 0.03 * h]
        for u in sorted(right, key=lambda u: u.box[0]):
            n = re.fullmatch(r"Y?[:：;.\-]?(\d{1,4})", u.text.replace(" ", ""), re.I)
            if n:
                c = parse_coords(f"{t.text.replace(' ', '')}Y:{n.group(1)}")
                if c:
                    return (*c, t)
    return None


_top_cache = {"key": None, "texts": []}


def top_texts(frame: np.ndarray) -> list:
    """Text in the top-left strip only (coordinates, power): quick to read. The last
    result is reused for the same picture."""
    key = (id(frame), frame.shape, int(frame[::97, ::89].sum()))
    if _top_cache["key"] == key:
        return _top_cache["texts"]
    h, w = frame.shape[:2]
    if True:
        # just the top-left strip; padded, because the text reader misreads a thin crop
        pad = 30
        up = 2.0 if h < 1000 else 1.0             # small windows: enlarge the digits
        x_end = int(w * 0.4)
        m = find_magnifier(frame)                 # stop at the magnifier: it reads as a 'Q' / '9'
        if m and 0.1 * w < m[0] < 0.5 * w:
            x_end = int(m[0] - 0.004 * h)
        crop = frame[0:int(h * 0.08), 0:x_end]
        if up != 1.0:
            crop = cv2.resize(crop, None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
        crop = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(0, 0, 0))
        texts = [Text(t.text, (int((t.box[0] - pad) / up), int((t.box[1] - pad) / up),
                               int(t.box[2] / up), int(t.box[3] / up)), t.conf)
                 for t in read_text(crop, strip=True)]
    _top_cache.update(key=key, texts=texts)
    return texts


def view_level(frame: np.ndarray) -> str:
    """'close' / 'patrol' / 'far' from the top strip only (quick)."""
    if not is_simple_view(top_texts(frame), frame):
        return "close"
    return "far" if has_world_button(frame) > 0.8 else "patrol"


def city_view(frame: np.ndarray) -> bool:
    """Inside a city ("Viewing the city of [P83L]JONH WICK."): zooming in over a city
    with the wheel enters it, and the wheel never leaves it again - only the map button at
    the bottom right does. No coordinate readout there, and a round blue "Share" button at
    the bottom left (0.29 of that corner blue vs <= 0.10 on the map). Quick colour check
    first; then the banner text, or failing that no top-left text at all."""
    H, W = frame.shape[:2]
    r = frame[int(0.885 * H):int(0.975 * H), int(0.008 * W):int(0.052 * W)]
    if r.size == 0:
        return False
    hsv = cv2.cvtColor(r, cv2.COLOR_BGR2HSV)
    blue = ((hsv[..., 0] > 95) & (hsv[..., 0] < 125) & (hsv[..., 1] > 120) & (hsv[..., 2] > 120)).mean()
    if blue < 0.2:
        return False
    texts = read_text(frame, (int(0.15 * W), int(0.14 * H), int(0.7 * W), int(0.11 * H)), strip=True)
    if find_text(texts, r"viewing|city|ciudad|viendo"):
        return True
    return not top_texts(frame)


def home_view(frame: np.ndarray, texts=None) -> bool:
    """Inside your own city (the game starts there after a restart / reconnect): your
    power and VIP show at the top left, but no '#4183 X: Y:' readout. The map button at
    the bottom right (city_view_exit) goes back to the map."""
    texts = top_texts(frame) if texts is None else texts
    return bool(texts) and not camera_coords(frame, texts) and not is_simple_view(texts, frame)


def city_view_exit(frame: np.ndarray):
    """Where the map button is in a city view (bottom right)."""
    H, W = frame.shape[:2]
    return int(W * 0.961), int(H * 0.948)


def find_info_box(frame: np.ndarray):
    """The light info box the game opens when you tap something on the map (e.g. a
    resource point: name, reserves, gatherer, Scout / Attack). Opens to the left or
    the right. Returns (x, y, w, h) or None. Doesn't depend on the game's language."""
    H, W = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    light = cv2.inRange(hsv, np.array((0, 0, 200), np.uint8), np.array((180, 60, 255), np.uint8))
    k = max(3, int(min(H, W) * 0.011)) | 1
    light = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(light)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if (a > 0.025 * W * H and 0.12 * W < w < 0.65 * W and 0.18 * H < h < 0.65 * H
                and (best is None or a > best[4])):
            best = (int(x), int(y), int(w), int(h), a)
    return best[:4] if best else None


def is_simple_view(texts: list[Text], frame) -> bool:
    """Zoomed-out map: the power / VIP block at the top left is hidden."""
    h = frame.shape[0]
    top = [t for t in texts if t.box[1] < h * 0.15]
    # power: "2,015,180" (English), "2 032 680" / "2.032.680" (Spanish...), or read without separators
    return (not find_text(top, r"^VIP\w?\d*$")
            and not find_text(top, r"^\D{0,2}\d{1,3}([ ,.\u00a0\u202f]\d{3})+$")
            and not [t for t in top if re.fullmatch(r"\D{0,2}\d{6,10}", re.sub(r"[ ,.\u00a0\u202f]", "", t.text))
                     and "X" not in t.text.upper()])


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
            W = frame.shape[1]
            if a[0] < 0.13 * W and b[0] < 0.13 * W:       # the quest / side panel on the left
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

def modal_dialog(frame: np.ndarray) -> bool:
    """A game dialog over a dimmed map (e.g. NETWORK DISCONNECTED): a big blue panel in
    the middle and everything around it darkened. Quick (no text reading). Measured: the
    dialog 0.98 blue / surroundings brightness 25; normal maps (day, night, water) at
    most 0.25 / at least 115."""
    H, W = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    c = hsv[int(0.40 * H):int(0.66 * H), int(0.36 * W):int(0.64 * W)]
    blue = ((c[..., 0] >= 95) & (c[..., 0] <= 115) & (c[..., 1] > 120) & (c[..., 2] > 80)).mean()
    if blue < 0.6:
        return False
    out = np.ones(hsv.shape[:2], bool)
    out[int(0.30 * H):int(0.72 * H), int(0.32 * W):int(0.68 * W)] = False
    return float(np.median(hsv[..., 2][out])) < 80


RECONNECT_RX = re.compile(r"network|disconnect|reconnect|connection|conexi|desconect|reconect|red\b", re.I)
CONFIRM_RX = re.compile(r"^(confirm|confirmar|ok|aceptar|reconnect|reconectar|retry|reintentar)$", re.I)


# 'You are already logged in on another device', and - when the phone takes the account while
# the PC tries to log in again - 'Login failed, please click CONFIRM to restart the game. Error
# Code: REGISTER_AND_LOGING' (seen live 2026-11-15: pressing that CONFIRM 30 times fought the
# user's phone for the account). Both: pause, never press.
ELSEWHERE_RX = re.compile(r"another\s*device|otro\s*dispositivo|logged\s*in\s*on|sesi[oó]n\s*en|"
                          r"login\s*failed|register_?and_?log|restart\s*the\s*game|reiniciar\s*el\s*juego",
                          re.I)


def logged_in_elsewhere(frame: np.ndarray) -> bool:
    """'TIP: You are already logged in on another device.' (the user opened the game on
    their phone): the game on the PC closes after this. Never press its CONFIRM."""
    if not modal_dialog(frame):
        return False
    H, W = frame.shape[:2]
    texts = read_text(frame, (int(0.30 * W), int(0.28 * H), int(0.40 * W), int(0.46 * H)))
    return bool(ELSEWHERE_RX.search(" ".join(t.text for t in texts)))


def find_reconnect_button(frame: np.ndarray):
    """(x, y) of the CONFIRM button on a network-disconnected dialog, else None. Only a
    dialog that talks about the network / reconnecting counts: a random CONFIRM (teleport,
    spend gems...) must never be pressed."""
    if not modal_dialog(frame):
        return None
    H, W = frame.shape[:2]
    reg = (int(0.30 * W), int(0.28 * H), int(0.40 * W), int(0.46 * H))
    texts = read_text(frame, reg)
    words = " ".join(t.text for t in texts)
    if not RECONNECT_RX.search(words):
        return None
    btn = [t for t in texts if CONFIRM_RX.match(re.sub(r"[^A-Za-z]", "", t.text))]
    if btn:
        return btn[-1].center
    # the button's text unread: the bright cyan button under the message
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    y0, y1, x0, x1 = int(0.54 * H), int(0.68 * H), int(0.40 * W), int(0.60 * W)
    m = ((hsv[y0:y1, x0:x1, 0] >= 82) & (hsv[y0:y1, x0:x1, 0] <= 100) &
         (hsv[y0:y1, x0:x1, 1] > 130) & (hsv[y0:y1, x0:x1, 2] > 200))
    if m.mean() < 0.1:
        return None
    ys, xs = np.nonzero(m)
    return int(x0 + xs.mean()), int(y0 + ys.mean())


def level_tags(frame: np.ndarray, hud=True) -> list:
    """Centres of the small light plates with a dark number under resource points,
    cities and barbarians (their level). Player armies and scouts never have one."""
    H, W = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    light = cv2.inRange(hsv, np.array((0, 0, 165), np.uint8), np.array((180, 60, 245), np.uint8))
    dark = cv2.inRange(hsv, np.array((0, 0, 0), np.uint8), np.array((180, 255, 95), np.uint8))
    # close the digit holes so a tag is one blob
    blob = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, stats, _ = cv2.connectedComponentsWithStats(blob)
    out = []
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        if not (0.008 * H <= h <= 0.03 * H and 0.016 * H <= w <= 0.05 * H):
            continue
        if not (1.1 <= w / h <= 3.2) or a < 0.5 * w * h:
            continue
        if hud and not (0.05 * W < x < 0.88 * W and 0.08 * H < y < 0.86 * H):
            continue  # skip the HUD around the edges
        inner = dark[y:y + h, x:x + w]
        if 0.06 <= inner.mean() / 255 <= 0.5:
            out.append((x + w / 2, y + h / 2))
    return out


def level_marks(frame: np.ndarray, hud=True) -> list:
    """Level plates found the way that also works at night - where a plate touches the
    white icon above it (one blob) and its digits are tinted lighter. Only for telling
    whether the map's icons have been drawn: it also takes the bottom of a bare icon for a
    plate, so NOT for the zoom check (count_level_tags) or the NPC test."""
    H, W = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    V = hsv[..., 2]
    light = cv2.inRange(hsv, np.array((0, 0, 165), np.uint8), np.array((180, 60, 245), np.uint8))
    blob = cv2.morphologyEx(light, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, lab, stats, _ = cv2.connectedComponentsWithStats(blob)
    out = []
    sh = max(4, int(0.016 * H))          # a plate's height (a thicker slice took in the icon too)
    for i in range(1, n):
        x, y, w, h, a = stats[i]
        cands = [(x, y, w, h, 0.05)]
        if 0.03 * H < h <= 0.075 * H and w <= 0.07 * H:
            sub = lab[y + h - sh:y + h, x:x + w] == i         # its bottom slice
            cols = np.nonzero(sub.any(axis=0))[0]
            if len(cols):
                # the slice may still take in a bit of the icon beside the plate (a stone
                # deposit's left rock: 0.051 H live), so a little wider is allowed here
                cands.append((x + int(cols.min()), y + h - sh, int(cols.max() - cols.min() + 1), sh, 0.06))
        for cx, cy, cw, ch, wmax in cands:
            if not (0.008 * H <= ch <= 0.03 * H and 0.016 * H <= cw <= wmax * H):
                continue
            if not (1.1 <= cw / ch <= 3.2) or (lab[cy:cy + ch, cx:cx + cw] == i).mean() < 0.5:
                continue
            if hud and not (0.05 * W < cx < 0.88 * W and 0.08 * H < cy < 0.86 * H):
                continue
            part = V[cy:cy + ch, cx:cx + cw].astype(int)
            dark = ((part <= 95) | (part < np.percentile(part, 80) - 75)).mean()
            if 0.06 <= dark <= 0.5:
                out.append((cx + cw / 2, cy + ch / 2))
                break
    return out


def white_plates(frame: np.ndarray) -> list:
    """Level plates (white) that level_marks misses: on a stone deposit the plate merges
    with the pale rocks (live 2026-11-15: a level 6 stone never found, the bot tapped round it
    for 25 s). Only plates with a readable level count. (Being gathered shows as a pickaxe
    badge, not by the plate.)"""
    H, W = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    S, V = hsv[..., 1], hsv[..., 2]
    m = ((V >= 190) & (S <= 50)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    n, lab, st, _ = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (0.008 * H <= h <= 0.024 * H and 0.016 * H <= w <= 0.05 * H and 1.3 <= w / h <= 3.6):
            continue
        if a < 0.45 * w * h:
            continue
        dark = (V[y:y + h, x:x + w] <= 110).mean()
        if not 0.06 <= dark <= 0.45:
            continue
        c = (x + w / 2, y + h / 2)
        if plate_level(frame, c):
            out.append(c)
    return out


def icons_drawn(frame: np.ndarray) -> bool:
    """Have the map's icons been drawn yet? Right after a jump the ground shows first and
    the icons a moment later. Measured: bare map 0-1 marks, drawn 4-17 (day and night)."""
    return len(level_marks(frame)) >= 3


def count_level_tags(frame: np.ndarray) -> int:
    """Small light tags with a dark number under resource points / cities / barbarians.
    Lots of them = the zoom level you want; none = zoomed out too far."""
    return len(level_tags(frame))


def has_level_tag_below(frame: np.ndarray, point, tags=None, reach=0.032) -> bool:
    """Is there a level plate just under this icon? Then it's a resource point, a city or
    a barbarian (NPC) - not a player's army or scout (those never show a level). Night
    colours made white resource icons pass for army diamonds, and red barbarian icons
    for hostile armies: every one cost a zoom in for nothing."""
    H = frame.shape[0]
    tags = level_tags(frame, hud=False) if tags is None else tags
    # measured on the real map: an icon's own plate sits 0.020-0.026 H under its centre and
    # within 0.010 H sideways. Tight on purpose: an army passing just above a resource
    # point has that point's plate ~0.04 H below it, and must still count.
    return any(abs(tx - point[0]) <= 0.013 * H and 0.012 * H < ty - point[1] <= reach * H for tx, ty in tags)


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
    # (green from hue 45: daytime grass behind the panel is 37-40 and merged with the badges -
    # 3 of 4 gathering troops read as "other", 2026-11-15; the badge green is ~57)
    ranges = [((45, 130, 90), (80, 255, 255)),     # green: gathering / marching
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
    # (4 troops: the game packs the list tighter, 0.074 H apart - the old 0.08 floor threw
    # those gaps away and placed troops 3 and 4 a badge too low, read as "other")
    gaps = [b - a for a, b in zip(ys, ys[1:]) if 0.06 * h <= b - a <= 0.12 * h]
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


def find_troop_clusters(frame: np.ndarray):
    """Marching armies at the close (3D) view: a cluster of small red-armoured troops
    (other players' armies are red), with the owner's name in small white letters just
    above it. Returns boxes (x, y, w, h), nearest the middle of the screen first.
    Tuned on the user's army recording (tests/real_screens/army_close/)."""
    H, W = frame.shape[:2]
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
    red = cv2.inRange(hsv, np.array((0, 140, 110), np.uint8), np.array((7, 255, 255), np.uint8)) | \
        cv2.inRange(hsv, np.array((172, 140, 110), np.uint8), np.array((180, 255, 255), np.uint8))
    # not the HUD: top bar, quest panel on the left, chat, buttons on the right
    red[:int(0.1 * H)] = 0
    red[int(0.86 * H):] = 0
    red[:, :int(0.13 * W)] = 0
    red[:, int(0.93 * W):] = 0
    k = int(0.025 * H) | 1
    m = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, _, st, _ = cv2.connectedComponentsWithStats(m)
    out = []
    for i in range(1, n):
        x, y, w, h, _a = st[i]
        if not (0.05 * H < w < 0.4 * H and 0.03 * H < h < 0.3 * H):
            continue
        part = red[y:y + h, x:x + w]
        fill = part.mean() / 255
        bits = cv2.connectedComponents(part)[0] - 1        # many little soldiers, not one roof
        if bits >= 10 and 0.06 <= fill <= 0.45:
            out.append((int(x), int(y), int(w), int(h)))
    mid = (W / 2, H / 2)
    return sorted(out, key=lambda b: math.dist((b[0] + b[2] / 2, b[1] + b[3] / 2), mid))


def cluster_is_npc(frame: np.ndarray, box, tags=None) -> bool:
    """A red 'troop cluster' with a level plate just under it is a barbarian (NPC) or a
    red-roofed city, never a player's army (those have their name above, no plate). Checked
    on the user's recordings: his armies never had one; red barbarians and cities did."""
    H = frame.shape[0]
    x, y, w, h = box
    tags = level_tags(frame, hud=False) if tags is None else tags
    return any(x - 0.03 * H <= tx <= x + w + 0.03 * H and y + 0.5 * h <= ty <= y + h + 0.06 * H
               for tx, ty in tags)


def player_troop_clusters(frame: np.ndarray):
    """find_troop_clusters without the barbarians / cities (see cluster_is_npc)."""
    boxes = find_troop_clusters(frame)
    if not boxes:
        return boxes
    tags = level_tags(frame, hud=False)
    return [b for b in boxes if not cluster_is_npc(frame, b, tags)]


def read_army_label(frame: np.ndarray, box) -> list[Text]:
    """The owner's name over a troop cluster: small white letters just above it, read
    enlarged 3x (too small for a normal read)."""
    H, W = frame.shape[:2]
    x, y, w, h = box
    cx = x + w // 2
    x0, x1 = max(0, int(cx - 0.16 * H)), min(W, int(cx + 0.16 * H))
    y0, y1 = max(0, int(y - 0.055 * H)), min(H, int(y + 0.35 * h))
    if x1 - x0 < 10 or y1 - y0 < 5:
        return []
    up = 3
    crop = cv2.resize(frame[y0:y1, x0:x1], None, fx=up, fy=up, interpolation=cv2.INTER_CUBIC)
    pad = 20
    crop = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_REPLICATE)
    out = []
    for t in read_text(crop, strip=True):
        bx, by, bw, bh = t.box
        out.append(Text(t.text, (int(x0 + (bx - pad) / up), int(y0 + (by - pad) / up),
                                 int(bw / up), int(bh / up)), t.conf))
    return out


def _name_part_similarity(text: str, name: str) -> float:
    """How much of his name (without the alliance tag) is in this text, at best (0-1).
    The tag is left out: other alliances' tags look alike ('IP83' vs 'P83L')."""
    from difflib import SequenceMatcher
    bare = _norm(re.sub(r"^\[[^\]]*\]", "", name))
    t = _norm(text)
    if not bare or not t:
        return 0.0
    L = len(bare)
    return max(SequenceMatcher(None, t[i:i + L], bare).ratio() for i in range(max(1, len(t) - L + 1)))


def army_owner_at_close(frame: np.ndarray, name: str, reach=0.4, his_city=None):
    """Whose is the army near the middle (close view)? ('him' | 'other' | None, texts
    read). The two troop clusters nearest the middle are read. 'him' on a clear read of
    his name; 'other' only on a clear read of a name that has nothing of his in it.
    his_city: where his city is on screen - his name there is his city's, not an army's
    (an army walking past his city was read as his)."""
    H, W = frame.shape[:2]
    read, other = [], False
    for box in player_troop_clusters(frame)[:2]:      # not barbarians (NPCs) or cities
        if math.dist((box[0] + box[2] / 2, box[1] + box[3] / 2), (W / 2, H / 2)) > reach * H:
            break
        texts = read_army_label(frame, box)
        if his_city is not None:
            texts = [t for t in texts if math.dist(t.center, his_city) > 0.15 * H]
        read += [t.text for t in texts]
        for t in texts:
            if name_score(t.text, name) >= 0.75:
                return "him", read
            if len(_norm(t.text)) >= 7 and _name_part_similarity(t.text, name) <= 0.3:
                other = True
    return ("other" if other else None), read


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
        if np.median(inner[..., 1]) < 12 and np.median(inner[..., 2]) > 210:
            continue                          # white all over: a resource icon (at night its
                                              # tinted outline passes for the dark ring), not
                                              # a lavender / mauve army diamond
        if ((inner[..., 2] > 235) & (inner[..., 1] < 30)).mean() < 0.08:
            continue                          # needs a white symbol inside
        if all(np.hypot(cx - f[0][0], cy - f[0][1]) > 0.5 * max(w, h) for f in found):
            if icon_kind(frame, (int(cx), int(cy))) == "army":
                found.append(((int(cx), int(cy)), "army"))
    for c in find_hostile_icons(frame):
        if all(np.hypot(c[0] - f[0][0], c[1] - f[0][1]) > 0.03 * H for f in found):
            found.append((c, "hostile"))
    # a plain diamond (no symbol) is a scout only if it's scout-sized: real scouts are
    # 33-39 px wide on a 1320 px tall screen, armies 52-58 (an army can show without
    # its symbol, e.g. on its way home)
    if any(k == "scout" for _, k in found):
        H = frame.shape[0]
        hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)
        both = (cv2.inRange(hsv, np.array((95, 8, 120), np.uint8), np.array((165, 60, 215), np.uint8))
                | cv2.inRange(hsv, np.array((0, 0, 215), np.uint8), np.array((180, 40, 255), np.uint8)))
        both = cv2.morphologyEx(both, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        _, lab, st, _ = cv2.connectedComponentsWithStats(both)
        out = []
        for c, k in found:
            if k == "scout":
                x, y = int(c[0]), int(c[1])
                i = lab[min(max(y, 0), H - 1), min(max(x, 0), frame.shape[1] - 1)]
                if i and st[i][2] >= 46 * H / 1320.0 and st[i][2] <= 110 * H / 1320.0:
                    k = "army"
            out.append((c, k))
        found = out
    if found:
        # a level plate under it: a resource point, city or barbarian (NPC), not a march
        tags = level_tags(frame, hud=False)
        found = [(c, k) for c, k in found if not has_level_tag_below(frame, c, tags)]
    if found:
        # at night a resource icon's plate touches the icon (one blob) and the plain plate
        # test misses it - the white stone-deposit icon came out as an "army" and was zoomed in
        # on 3x. The night-capable marks: a resource icon's plate sits 0.021-0.026 H under it
        # (17/17 measured); a real army's only mark is its own symbol's bottom, 0.010-0.013 H.
        marks = level_marks(frame, hud=False)   # (an icon low on screen has its plate in the
        found = [(c, k) for c, k in found        #  bottom band: live, a stone icon at 0.89 H)
                 if not any(abs(mx - c[0]) <= 0.010 * frame.shape[0]
                            and 0.016 * frame.shape[0] <= my - c[1] <= 0.034 * frame.shape[0]
                            for mx, my in marks)]
        # mostly red: a barbarian's axe / fort (measured 0.49-0.65 red; player armies and
        # scouts are grey-lavender: 0.00). Live, one came out as an "army" and was zoomed in on
        # 5 times. Red hostile armies are their own kind ('hostile'), not touched here.
        found = [(c, k) for c, k in found if k not in ("army", "scout") or _red_share(frame, c) < 0.25]
        # a caravan: a grey diamond with a solid blue diamond badge just under it (the user,
        # 2026-11-15 - it was taken for an army marching at a troop). Real armies have none.
        found = [(c, k) for c, k in found if k not in ("army", "scout") or not is_caravan(frame, c)]
    return found


def is_caravan(frame, c):
    """A blue diamond badge 0.025-0.085 H under this diamond (measured: centre +0.057 H, about
    0.04 H across): it's a caravan, not an army."""
    H, W = frame.shape[:2]
    x, y = int(c[0]), int(c[1])
    r = frame[max(0, int(y + 0.025 * H)):min(H, int(y + 0.09 * H)), max(0, int(x - 0.03 * H)):min(W, int(x + 0.03 * H))]
    if r.size == 0:
        return False
    hsv = cv2.cvtColor(r, cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= 95) & (hsv[..., 0] <= 115) & (hsv[..., 1] > 120) & (hsv[..., 2] > 120)).astype(np.uint8)
    k = max(3, int(0.01 * H)) | 1                  # the white symbol inside splits the blue: join it
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((k, k), np.uint8))
    n, _, st, cent = cv2.connectedComponentsWithStats(m)
    y0, x0 = max(0, int(y + 0.025 * H)), max(0, int(x - 0.03 * H))
    for i in range(1, n):
        bx, by, bw, bh, area = st[i]
        cx, cy = x0 + cent[i][0], y0 + cent[i][1]
        # the measured badge: 0.031 H square, diamond-shaped (fills 0.56 of its box - a round
        # button fills ~0.8), centred under the diamond (dx -0.6 px), 0.057 H below it. A loose
        # 'any blue blob below' took real armies near the blue troop panel for caravans.
        if (0.022 * H <= bw <= 0.045 * H and 0.022 * H <= bh <= 0.045 * H and 0.75 <= bw / bh <= 1.33
                and 0.42 <= area / (bw * bh) <= 0.68 and abs(cx - x) <= 0.012 * H
                and 0.042 * H <= cy - y <= 0.072 * H):
            return True
    return False


def _red_share(frame, c, r=0.012):
    H = frame.shape[0]
    k, x, y = int(r * H), int(c[0]), int(c[1])
    part = frame[max(0, y - k):y + k, max(0, x - k):x + k]
    if part.size == 0:
        return 0.0
    hsv = cv2.cvtColor(part, cv2.COLOR_BGR2HSV)
    return float((((hsv[..., 0] < 8) | (hsv[..., 0] > 170)) & (hsv[..., 1] > 120) & (hsv[..., 2] > 90)).mean())


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


# ---------------------------------------------------------------- danger icons

def danger_icons(frame: np.ndarray) -> set:
    """Big red round icons the game shows bottom right while something is coming at
    you: an eye ('scouted') or crossed swords ('attacked'). Told apart by the white
    symbol: the eye is wider than tall with a red pupil, the swords cross in white in
    the middle. (Red notification badges are much smaller.)"""
    H, W = frame.shape[:2]
    y0, x0 = int(0.55 * H), int(0.55 * W)
    roi = frame[y0:, x0:]
    hsv = cv2.cvtColor(roi, cv2.COLOR_BGR2HSV)
    red = (cv2.inRange(hsv, np.array((0, 170, 150), np.uint8), np.array((8, 255, 255), np.uint8))
           | cv2.inRange(hsv, np.array((172, 170, 150), np.uint8), np.array((180, 255, 255), np.uint8)))
    n, _, st, _ = cv2.connectedComponentsWithStats(red)
    found = set()
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (0.055 * H <= w <= 0.13 * H and 0.8 < w / h < 1.25 and 0.5 <= a / (w * h) <= 0.8):
            continue
        g = hsv[y + int(.15 * h):y + int(.85 * h), x + int(.15 * w):x + int(.85 * w)]
        white = cv2.inRange(g, np.array((0, 0, 200), np.uint8), np.array((180, 90, 255), np.uint8))
        ys, xs = np.where(white > 0)
        if len(xs) < 0.03 * white.size:
            continue
        aspect = (xs.max() - xs.min() + 1) / (ys.max() - ys.min() + 1)
        gh, gw = white.shape
        mid = white[int(.4 * gh):int(.6 * gh), int(.4 * gw):int(.6 * gw)]
        centre_white = float(np.mean(mid > 0)) if mid.size else 0.0
        eye = aspect > 1.17 or centre_white < 0.15
        found.add("scouted" if eye else "attacked")
    return found


def search_go_button(frame: np.ndarray):
    """The blue search button of the open coordinate bar (quick check, no text reading)."""
    H, W = frame.shape[:2]
    y0, y1 = int(0.08 * H), int(0.5 * H)
    hsv = cv2.cvtColor(frame[y0:y1], cv2.COLOR_BGR2HSV)
    blue = cv2.inRange(hsv, np.array((93, 170, 190), np.uint8), np.array((110, 255, 255), np.uint8))
    n, _, st, c = cv2.connectedComponentsWithStats(blue)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if (0.04 * H <= w <= 0.09 * H and 0.04 * H <= h <= 0.09 * H and a > 0.6 * w * h
                and x > 0.3 * W and (best is None or x < best[0])):
            best = (int(c[i][0]), int(c[i][1]) + y0)
    return best


# ---------------------------------------------------------------- enemy gatherers

def find_gather_badges(frame: np.ndarray, colour="red") -> list:
    """Round badges with a white pickaxe sitting on a resource point that someone is
    gathering from: red = not your alliance, green = you / your alliance.
    Same size at the sweeping and the close zoom. Returns [(x, y)] centres.
    (Notification badges on the menus are outside the map area checked here.)"""
    H, W = frame.shape[:2]
    # not below 0.82 H: the bubble above the menu buttons (bottom right) has a red
    # notification badge with a white number - live it passed for a farm badge on every look
    # (always at the same screen spot: a "farm" at a different tile wherever the camera was)
    x0, x1, y0, y1 = int(0.1 * W), int(0.9 * W), int(0.1 * H), int(0.82 * H)
    hsv = cv2.cvtColor(frame[y0:y1, x0:x1], cv2.COLOR_BGR2HSV)
    if colour == "red":
        mask = (cv2.inRange(hsv, np.array((0, 150, 120), np.uint8), np.array((8, 255, 255), np.uint8))
                | cv2.inRange(hsv, np.array((170, 150, 120), np.uint8), np.array((180, 255, 255), np.uint8)))
    else:
        mask = cv2.inRange(hsv, np.array((45, 120, 100), np.uint8), np.array((80, 255, 255), np.uint8))
    white = cv2.inRange(hsv, np.array((0, 0, 200), np.uint8), np.array((180, 70, 255), np.uint8))
    n, _, st, c = cv2.connectedComponentsWithStats(mask)
    out = []
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if not (0.016 * H <= w <= 0.06 * H and 0.75 < w / h < 1.33 and a >= 0.55 * w * h):
            continue
        k = max(2, w // 6)                       # round: the bounding box corners are empty
        corners = [mask[y:y + k, x:x + k], mask[y:y + k, x + w - k:x + w],
                   mask[y + h - k:y + h, x:x + k], mask[y + h - k:y + h, x + w - k:x + w]]
        if max(float(np.mean(cc > 0)) for cc in corners) > 0.35:
            continue                             # e.g. a red banner (square top corners)
        # round, not a diamond (red hostile army icons): just inside each corner, along
        # the diagonal, a circle is still red and a diamond isn't
        diag = 0
        for fx, fy in ((0.21, 0.21), (0.79, 0.21), (0.21, 0.79), (0.79, 0.79)):
            px, py, r = x + int(fx * w), y + int(fy * h), max(1, w // 14)
            if np.mean(mask[py - r:py + r + 1, px - r:px + r + 1] > 0) > 0.4:
                diag += 1
        if diag < 3:
            continue
        inner = white[y + h // 5:y + h - h // 5, x + w // 5:x + w - w // 5]
        if inner.size and np.mean(inner > 0) > 0.08:             # the white pickaxe
            out.append((int(c[i][0]) + x0, int(c[i][1]) + y0))
    return out


def read_gatherer(texts: list[Text], frame_h: int):
    """From a resource point's info box: (gatherer name, (x, y)) or (None, coords).
    (The camera readout in the top bar is skipped.)"""
    name, coords = None, None
    for t in texts:
        c = parse_coords(t.text)
        if c and coords is None and t.box[1] > 0.1 * frame_h:
            coords = (c[1], c[2])
    lab = [t for t in texts if re.search(r"gather", t.text, re.I)]
    for g in lab:
        gy = g.center[1]
        same_row = [t for t in texts if t is not g and abs(t.center[1] - gy) < max(12, g.box[3])
                    and t.box[0] > g.box[0] + g.box[2] - 5]
        if same_row:
            name = min(same_row, key=lambda t: t.box[0]).text.strip()
            break
        m = re.search(r"gatherer\s*[:：]?\s*(.+)$", g.text, re.I)   # read as one piece
        if m:
            name = m.group(1).strip()
            break
    return name, coords


# ---------------------------------------------------------------- resource points (searching zoom)
#
# Auto-gather reads, at the searching zoom, what each resource point is from its white icon
# (corn = food, rolled log = wood, cut stone = stone, ringed coin = gold, diamond = gems) and
# its level from the digit on its plate - no zooming in to find the best one. Both by shape:
# small black-and-white masks in rokwatch/resources/ (icon-<kind>-N.png, digit-<n>-N.png),
# compared by overlap, the same way as the troop badges.

RES_DIR = __import__("pathlib").Path(__file__).with_name("resources")
_res_t = None
_ICON = 32
_DIGIT = (16, 20)


def _res_templates():
    global _res_t
    if _res_t is None:
        _res_t = {"icon": {}, "digit": {}}
        for p in sorted(RES_DIR.glob("*.png")) if RES_DIR.exists() else []:
            part = p.stem.split("-")
            if len(part) < 2 or part[0] not in _res_t:
                continue
            m = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
            _res_t[part[0]].setdefault(part[1], []).append((m > 127).astype(np.float32))
    return _res_t


def _fit(mask, size):
    """The mask's lit part, cropped to its box and scaled to `size` (w, h), keeping the
    shape's proportions (padded)."""
    ys, xs = np.nonzero(mask)
    if len(xs) < 6:
        return None
    m = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8) * 255
    h, w = m.shape
    W, H = size
    s = min(W / w, H / h)
    m = cv2.resize(m, (max(1, int(round(w * s))), max(1, int(round(h * s)))), interpolation=cv2.INTER_AREA)
    out = np.zeros((H, W), np.uint8)
    y0, x0 = (H - m.shape[0]) // 2, (W - m.shape[1]) // 2
    out[y0:y0 + m.shape[0], x0:x0 + m.shape[1]] = m
    return (out > 100).astype(np.float32)


def resource_icon_mask(frame, mark):
    """The white icon just above a level plate, as a 32x32 mask (None if there's no white
    icon: barbarians are red, forts red, cities have no white glyph there)."""
    H, W = frame.shape[:2]
    mx, my = int(mark[0]), int(mark[1])
    y0, y1 = max(0, int(my - 0.068 * H)), max(0, int(my - 0.012 * H))
    x0, x1 = max(0, int(mx - 0.032 * H)), min(W, int(mx + 0.032 * H))
    c = frame[y0:y1, x0:x1]
    if c.size == 0:
        return None
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    v, s = hsv[..., 2].astype(np.int16), hsv[..., 1]
    bg = int(np.median(v))
    white = (s < 70) & (v > max(140, bg + 45))           # day and night (dimmer, bluish)
    # a gathering badge (round green / red / blue disc with a white pickaxe) can sit on the
    # icon's top: blank the disc out, or its pickaxe spoils the shape (your own gathering
    # points weren't recognised, so the panel couldn't say what your troops bring back)
    hh = hsv[..., 0]
    disc = (((hh >= 45) & (hh <= 85) & (s > 130) & (v > 110))
            | (((hh < 8) | (hh > 172)) & (s > 150) & (v > 140))
            | ((hh >= 95) & (hh <= 115) & (s > 150) & (v > 150))).astype(np.uint8) * 255
    cs, _ = cv2.findContours(cv2.morphologyEx(disc, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)),
                             cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    for cnt in cs:
        bx, by, bw, bh = cv2.boundingRect(cnt)
        if bw >= 0.012 * H and bh >= 0.008 * H:
            (cx, cy), r = cv2.minEnclosingCircle(cnt)
            r = max(r, bw / 2) + 2
            yy, xx = np.ogrid[:white.shape[0], :white.shape[1]]
            white &= (xx - cx) ** 2 + (yy - cy) ** 2 > r * r
    red = (((hsv[..., 0] < 10) | (hsv[..., 0] > 170)) & (s > 120) & (v > 100)).mean()
    if white.mean() < 0.03 or red > 0.02:
        return None
    n, lab, st, _ = cv2.connectedComponentsWithStats(white.astype(np.uint8))
    keep = np.zeros_like(white)
    for i in range(1, n):
        if st[i, 4] >= 0.15 * st[1:, 4].max():             # the glyph's pieces, not specks
            keep |= lab == i
    return _fit(keep, (_ICON, _ICON))


def plate_digit_mask(frame, mark):
    """The dark digit(s) on a level plate as a 16x20 mask, and how many digits (by width)."""
    H, W = frame.shape[:2]
    mx, my = int(mark[0]), int(mark[1])
    c = frame[max(0, int(my - 0.0095 * H)):int(my + 0.0095 * H), max(0, int(mx - 0.012 * H)):int(mx + 0.012 * H)]
    if c.size == 0:
        return None, 0
    g = cv2.cvtColor(c, cv2.COLOR_BGR2GRAY)
    dark = g < min(110, int(np.percentile(g, 70)) - 60)
    n, lab, st, _ = cv2.connectedComponentsWithStats(dark.astype(np.uint8))
    # the digit's strokes: tall enough, and not touching the crop's left/right edge (the
    # plate's dark outline does)
    parts = [i for i in range(1, n) if st[i, 4] >= 6 and st[i, 3] >= 0.4 * c.shape[0]
             and st[i, 0] > 0 and st[i, 0] + st[i, 2] < c.shape[1]]
    if not parts:
        return None, 0
    keep = np.isin(lab, parts)
    return _fit(keep, _DIGIT), len(parts)


def _best(mask, temps, floor):
    best, score = None, 0.0
    for name, ts in temps.items():
        for t in ts:
            inter = float((mask * t).sum())
            union = float(np.maximum(mask, t).sum())
            iou = inter / union if union else 0.0
            if iou > score:
                best, score = name, iou
    return (best, score) if score >= floor else (None, score)


def resource_kind(frame, mark):
    m = resource_icon_mask(frame, mark)
    if m is None:
        return None
    return _best(m, _res_templates()["icon"], 0.5)[0]


_digit_refs_c = None


def _digit_refs():
    """Reference digit masks as arrays: (labels, masks N x 20 x 16, may_shift N). Real plates
    cut from the user's game (digit-<d>-rNN.png, picked from ~450 hand-checked plates, day /
    night / desert; a pixel of shift allowed) - the old drawn ones only for digits with no
    real sample yet, unshifted (the drawn 5 read every real 5 as 6 or 3, 2026-11-15)."""
    global _digit_refs_c
    if _digit_refs_c is None:
        real, drawn = {}, {}
        for p in sorted(RES_DIR.glob("digit-*.png")) if RES_DIR.exists() else []:
            part = p.stem.split("-")
            m = (cv2.imread(str(p), cv2.IMREAD_GRAYSCALE) > 127).astype(np.float32)
            (real if part[2].startswith("r") else drawn).setdefault(part[1], []).append(m)
        refs = [(d, m, True) for d, ms in real.items() for m in ms]
        # (levels 7 / 8: not in the user's table - such a point can't be checked as intact;
        # the drawn 8 matched real 3s better than the real 3s did)
        refs += [(d, m, False) for d, ms in drawn.items() if d not in real and d not in "78" for m in ms]
        _digit_refs_c = ([r[0] for r in refs], np.stack([r[1] for r in refs]).reshape(len(refs), -1),
                         np.array([r[2] for r in refs]))
        labels = _digit_refs_c[0]
        _digit_refs_c += ({d: np.array([i for i, l in enumerate(labels) if l == d]) for d in set(labels)},)
    return _digit_refs_c


def plate_level(frame, mark):
    """The level on a resource point's plate (1-8), None if unreadable or two digits."""
    m, n = plate_digit_mask(frame, mark)
    if m is None or n != 1:
        return None
    labels, R, may_shift, groups = _digit_refs()
    m = m.astype(np.float32)
    T = np.stack([np.roll(np.roll(m, dx, axis=1), dy, axis=0)
                  for dx in (0, -1, 1) for dy in (0, -1, 1)]).reshape(9, -1)     # [0] = unshifted
    inter = T @ R.T                                                    # 9 x N
    union = T.sum(1)[:, None] + R.sum(1)[None, :] - inter
    iou = np.where(union > 0, inter / np.maximum(union, 1e-6), 0.0)
    score = np.where(may_shift, iou.max(0), iou[0])
    # each digit scored by its best two references (one odd reference can't swing a read:
    # tested on ~1250 hand-checked plates, day and night, every set held out in turn)
    best, top = None, 0.0
    for d, ix in groups.items():
        sc = np.sort(score[ix])[-2:].mean()
        if sc > top:
            best, top = d, float(sc)
    return int(best) if best and top >= 0.55 and best.isdigit() else None


def gather_badge_on(frame, mark):
    """A gathering badge (round green / red / blue disc with a pickaxe) right above this
    point's icon: someone is gathering there. Only a round, filled blob counts: the blue
    territory border passing above an icon is a thin line (it marked free points 'taken')."""
    H, W = frame.shape[:2]
    mx, my = int(mark[0]), int(mark[1])
    c = frame[max(0, int(my - 0.15 * H)):max(0, int(my - 0.03 * H)), max(0, int(mx - 0.045 * H)):min(W, int(mx + 0.045 * H))]
    if c.size == 0:
        return False
    hsv = cv2.cvtColor(c, cv2.COLOR_BGR2HSV)
    h, s_, v = hsv[..., 0], hsv[..., 1], hsv[..., 2]
    masks = [("green", (h >= 45) & (h <= 85) & (s_ > 130) & (v > 110)),              # you
             ("red", ((h < 8) | (h > 172)) & (s_ > 150) & (v > 140)),                  # others
             ("blue", (h >= 95) & (h <= 115) & (s_ > 150) & (v > 150))]                # alliance
    lo, hi = 0.018 * H, 0.06 * H
    for colour, m in masks:
        m = cv2.morphologyEx(m.astype(np.uint8) * 255, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        for cnt in cs:
            x, y, w, hh = cv2.boundingRect(cnt)
            # the disc's outline (the white pickaxe inside doesn't matter): round and filled
            if (lo <= w <= hi and lo <= hh <= hi and 0.7 <= w / hh <= 1.4
                    and cv2.contourArea(cnt) >= 0.55 * w * hh):
                return colour                  # 'green' / 'red' / 'blue' (truthy)
    return None


def find_resource_points(frame):
    """Resource points on a searching-zoom picture: [{'mark', 'kind', 'level', 'taken'}]."""
    H, W = frame.shape[:2]
    out = []
    marks = level_marks(frame, hud=False)
    # plates level_marks misses (on stone deposits): the user, "don't miss a single one"
    marks += [w for w in white_plates(frame) if not any(math.dist(w, m) < 0.02 * H for m in marks)]
    for mk in marks:
        if not (0.1 * W < mk[0] < 0.86 * W and 0.12 * H < mk[1] < 0.84 * H):
            continue
        kind = resource_kind(frame, mk)
        if not kind:
            continue
        lv = plate_level(frame, mk)
        if lv is None:
            # at night the icon can merge with the plate's top edge: the mark sits on the edge
            # and the digit is cut (live 2026-11-15: an unread level 3 stone was checked first)
            for k in (0.004, 0.007, 0.010, 0.013):
                lv = plate_level(frame, (mk[0], mk[1] + k * H))
                if lv is not None:
                    break
        out.append({"mark": (int(mk[0]), int(mk[1])), "kind": kind, "level": lv,
                    "taken": bool(gather_badge_on(frame, mk))})
    return out


# ---------------------------------------------------------------- alliance help (handshake)

_help_t = None


def find_help_bubble(frame):
    """The alliance-help bubble (white bubble, green ring with a handshake) that pops up over
    the Alliance button: pressing it helps every member. Its centre, or None. The picture is
    scaled to 1200 wide (the template's size) and only the bottom-right is searched.
    Measured: 0.93-0.99 on real bubbles (day and night), at most 0.55 elsewhere."""
    global _help_t
    if _help_t is None:
        p = __import__("pathlib").Path(__file__).with_name("hud") / "help.png"
        _help_t = cv2.imread(str(p)) if p.exists() else False
    if _help_t is False or frame is None:
        return None
    H, W = frame.shape[:2]
    s = 1200 / W
    g = cv2.resize(frame, (1200, int(round(H * s))), interpolation=cv2.INTER_AREA)
    h = g.shape[0]
    y0, x0 = int(0.6 * h), 720
    r = g[y0:int(0.97 * h), x0:1200]
    if r.shape[0] < _help_t.shape[0] or r.shape[1] < _help_t.shape[1]:
        return None
    m = cv2.matchTemplate(r, _help_t, cv2.TM_CCOEFF_NORMED)
    _, v, _, loc = cv2.minMaxLoc(m)
    if v < 0.8:
        return None
    th, tw = _help_t.shape[:2]
    return int((x0 + loc[0] + tw / 2) / s), int((y0 + loc[1] + th / 2) / s)


def find_uturn_icon(frame):
    """A selected MARCHING troop of yours shows three hexagon icons under it: two blue ones and
    then a pale U-turn arrow (turn it back - there's no RECALL text for a marching troop). The
    camera follows the troop with a lag, so they move about: found by the two blue hexagons
    side by side; the U-turn is the next one along. (x, y) or None."""
    H, W = frame.shape[:2]
    x0, y0 = int(0.25 * W), int(0.25 * H)
    hsv = cv2.cvtColor(frame[y0:int(0.8 * H), x0:int(0.75 * W)], cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= 95) & (hsv[..., 0] <= 115) & (hsv[..., 1] > 120) & (hsv[..., 2] > 150)).astype(np.uint8)
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))
    n, _, st, cent = cv2.connectedComponentsWithStats(m)
    hexes = [(x0 + cent[i][0], y0 + cent[i][1]) for i in range(1, n)
             if 0.016 * H <= st[i][2] <= 0.07 * H and 0.016 * H <= st[i][3] <= 0.07 * H
             and st[i][4] >= 0.4 * st[i][2] * st[i][3]]
    best = None
    for a in hexes:
        for b in hexes:
            dx = b[0] - a[0]
            if 0.035 * H <= dx <= 0.075 * H and abs(b[1] - a[1]) <= 0.012 * H:
                cand = (int(b[0] + dx), int(b[1]))
                if best is None or a[1] > best[1]:
                    best = cand
    return best
