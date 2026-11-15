"""Auto-gather: keep every troop slot busy gathering, balancing the four resources.

(The user, 2026-11-15.) What it does, step by step:

1. Free troops: the troop list's "N/M" counter (used / total). M - N troops can go out.
2. Which resource: your storage (the bar at the top: food, wood, stone, gold) compared by what
   a resource point gives, not by raw amounts. From the user's table (the same at every level):
   food = wood = 1, stone = 0.75, gold = 1/3 - so 300k gold counts like 900k food. The lowest
   goes first; with two troops free, the first troop's load is counted in before the second
   is chosen (so it doesn't send both to food when one food trip already evens things out).
3. Where: around a centre the user picks (default the altar at 173,378) within a radius. The
   points there are surveyed at the close zoom: every level plate on screen is tapped and its
   box read (kind, level, reserves, gatherer, exact X:Y) into a book (gather_points.json).
   Barbarians are skipped. (The game's own resource search only looks near YOUR city, and
   tapping from the searching zoom's estimate missed by a tile - hence the survey.)
4. Gather: the highest-level point of that kind that's full (its reserves match the table)
   and free. Jump to its exact X:Y (it's then in the middle of the screen), tap it, read the
   box again (still full? still free?), GATHER -> New Troop -> MARCH (the game fills in the
   troops itself, like the user does). A point that isn't full or is taken is blacklisted
   for a while and the next one is tried.
5. Safety: the watcher runs at the same time. If an army comes at a gathering troop, the
   watcher recalls it, and auto-gather waits a while before sending troops out again.
"""

from __future__ import annotations

import json
import logging
import math
import re
import time
from pathlib import Path

from . import farms, screen

log = logging.getLogger("rokwatch")

# what one unit of each resource is worth, from the resource points' full amounts
RATIO = {"food": 1.0, "wood": 1.0, "stone": 0.75, "gold": 1 / 3}
KINDS = ("food", "wood", "stone", "gold")
BOOK = Path("gather_points.json")
DEFAULT_CENTRE = (173, 378)        # the altar (the user's usual farming spot)
DEFAULT_RADIUS = 12


# ------------------------------------------------------------------ choosing what to gather

def parse_amount(text: str):
    """'1.0M' -> 1_000_000, '776.1K' -> 776_100, '34,295' -> 34295, else None."""
    t = text.strip().upper().replace(" ", "").replace(",", "")
    m = re.fullmatch(r"(\d+(?:\.\d+)?)([KMB]?)", t)
    if not m:
        return None
    v = float(m.group(1)) * {"": 1, "K": 1e3, "M": 1e6, "B": 1e9}[m.group(2)]
    return int(round(v))


def read_storage(frame):
    """Your storage from the resource bar at the top right (shown at the close zoom and in
    your city): {'food', 'wood', 'stone', 'gold'} or None. The bar's numbers in order, left
    to right: food, wood, stone, gold, gems."""
    H, W = frame.shape[:2]
    texts = screen.read_text(frame, (int(0.40 * W), 0, int(0.55 * W), int(0.055 * H)), strip=True)
    nums = sorted(((t.center[0], parse_amount(t.text)) for t in texts), key=lambda p: p[0])
    nums = [v for _, v in nums if v is not None]
    if len(nums) < 4:
        return None
    if len(nums) == 4 and nums[3] < 1000:
        # one number missed and the gems (468) slid into gold's place: read again
        return None
    return dict(zip(KINDS, nums[:4]))


def balanced(storage: dict) -> dict:
    """Storage in 'food-equivalent': each amount divided by what its points give."""
    return {k: storage[k] / RATIO[k] for k in KINDS if k in storage}


def pick_kinds(storage: dict, n: int, load: float = 450_000) -> list:
    """What to send `n` free troops to, one after the other: always the lowest (balanced),
    counting in what each troop sent will bring back (its load, food-equivalent)."""
    b = balanced(storage)
    out = []
    for _ in range(max(0, n)):
        k = min(KINDS, key=lambda k: (b.get(k, float("inf")), KINDS.index(k)))
        out.append(k)
        b[k] = b.get(k, 0) + load
    return out


# ------------------------------------------------------------------ reading a point's box

def read_point(texts):
    """From a tapped object's box: {'xy', 'kind', 'level', 'reserves', 'gatherer', 'full'}
    for a resource point; {'xy', 'kind': 'other'} for anything else (barbarians, empty
    land...); None if unreadable."""
    joined = " ".join(t.text for t in texts)
    xy = None
    for t in texts:
        c = screen.parse_coords(t.text)
        if c:
            xy = (c[1], c[2])
            break
    what = next((t.text for t in texts if re.search(r"level|nivel", t.text, re.I)
                 and farms.parse_kind(t.text)[0]), None)
    if not what or re.search(r"barbarian|unoccupied|b[áa]rbaro|desocupad", joined, re.I):
        return {"xy": xy, "kind": "other"} if xy else None
    kind, level = farms.parse_kind(what)
    if kind not in KINDS:
        return {"xy": xy, "kind": "other"} if xy else None
    reserves = None
    for t in texts:
        v = parse_amount(t.text)
        if v and v >= 1000 and (reserves is None or v > reserves):
            reserves = v
    gatherer, _ = screen.read_gatherer(texts, 10 ** 6)
    if gatherer and re.fullmatch(r"none|ninguno|nadie|-+", gatherer.strip(), re.I):
        gatherer = None
    full = farms.FULL.get(kind, {}).get(level)
    return {"xy": xy, "kind": kind, "level": level, "reserves": reserves, "gatherer": gatherer,
            "full": bool(full and reserves and reserves >= full * 0.995)}


# ------------------------------------------------------------------ the book of points

class PointBook:
    """Resource points around the gathering centre (exact X:Y from their boxes), and a
    blacklist of ones that weren't full / were taken."""

    def __init__(self, path=BOOK):
        self.path = Path(path)
        try:
            d = json.loads(self.path.read_text())
        except (OSError, ValueError):
            d = {}
        self.points = {tuple(map(int, k.split(","))): v for k, v in (d.get("points") or {}).items()}
        self.black = {tuple(map(int, k.split(","))): v for k, v in (d.get("black") or {}).items()}
        self.surveyed = d.get("surveyed") or {}

    def save(self):
        try:
            self.path.write_text(json.dumps({
                "points": {f"{x},{y}": v for (x, y), v in self.points.items()},
                "black": {f"{x},{y}": v for (x, y), v in self.black.items()},
                "surveyed": self.surveyed}, indent=1))
        except OSError:
            pass

    def note(self, p: dict, now=None):
        if not p or not p.get("xy") or p.get("kind") not in KINDS:
            return
        now = now or time.time()
        self.points[tuple(p["xy"])] = {k: v for k, v in p.items() if k != "xy"} | {"seen": now}

    def blacklist(self, xy, minutes=120, now=None):
        self.black[tuple(xy)] = (now or time.time()) + minutes * 60

    def candidates(self, kind, centre, radius, now=None):
        """Points of this kind near the centre, best first: highest level, last seen full and
        free, not blacklisted; nearer to the centre breaks ties."""
        now = now or time.time()
        out = []
        for xy, p in self.points.items():
            if p.get("kind") != kind or math.dist(xy, centre) > radius:
                continue
            if self.black.get(xy, 0) > now or p.get("gatherer") or not p.get("full"):
                continue
            out.append((xy, p))
        out.sort(key=lambda xp: (-(xp[1].get("level") or 0), math.dist(xp[0], centre)))
        return out


# ------------------------------------------------------------------ the gatherer

class AutoGather:
    def __init__(self, game, hunter, cfg: dict):
        g = cfg.get("gather") or {}
        self.game, self.hunter = game, hunter
        self.centre = tuple(g.get("centre") or DEFAULT_CENTRE)
        self.radius = float(g.get("radius") or DEFAULT_RADIUS)
        self.every = float(g.get("check_seconds", 60))
        self.survey_every = float(g.get("survey_minutes", 30)) * 60
        self.book = PointBook()
        self.load = float(g.get("load", 450_000))       # learned from the MARCH screen
        self._at = 0.0
        self.paused_until = 0.0                         # after the watcher pulled a troop back
        self.last = ""                                  # what it did last (for the panel)
        self._scan = (0.0, [])                          # (when, points seen by the last scan)
        self._scan_taken = []                           # tiles tried since that scan
        self.threat = False                             # the watcher saw something: stop

    def _watch(self, frame, cam=None) -> bool:
        """The watcher's checks on a picture auto-gather took anyway (so it isn't blind while
        gathering): armies marching at your troops / city, the red eye / swords. True (and
        auto-gather stops) when something's coming; the alerts go out with the round."""
        h = self.hunter
        try:
            alerts = h.guard_frame(frame, cam) if cam else (h._dangers_frame(frame) or [])
        except Exception:
            log.debug("gather: watch check failed", exc_info=True)
            return False
        if alerts:
            h._guard_alerts = list(getattr(h, "_guard_alerts", []) or []) + alerts
        danger = bool({"attacked", "scouted"} & set(getattr(h, "dangers", set()))) or getattr(h, "warned", False)
        if alerts or danger:
            if not self.threat:
                log.warning("gather: something's coming - stopping auto-gather, the watcher takes over")
            self.threat = True
        return self.threat

    def pause(self, minutes=10):
        self.paused_until = time.time() + minutes * 60

    def due(self, now):
        return now >= self.paused_until and now - self._at >= self.every

    def say(self, msg):
        self.last = msg
        self.hunter._progress(0, 1, msg)

    # -------------------------------------------------------------- one go
    def step(self, stop=None) -> list[str]:
        """Send out as many free troops as there are, each to the right resource. Returns
        what it did (for the log / panel)."""
        self._at = time.time()
        self.threat = False
        done = []
        panel = self.game.troops()
        if not panel:
            log.info("gather: can't see your troop list")
            return done
        used, total, slots = panel
        if total:
            self.total = total                          # remembered (the counter's read is flaky)
        total = total or getattr(self, "total", None)
        if used is None:                                # counter unread: count the commanders
            used = sum(1 for s in slots if s.state not in ("scout",))
        if not total:
            log.info("gather: couldn't read how many troops you can send")
            return done
        free = total - used
        if free <= 0:
            return done
        if not self.hunter.zoom_close():
            return done
        try:
            storage = None
            # the bar fades in after zooming in from the map (read straight away it was empty),
            # and a number can be missed: keep reading for up to 3 s
            # ...and a decimal point can be lost ('1.1M' read as '11M'): two reads must agree
            time.sleep(0.5)
            prev = None
            for _ in range(8):
                cur = read_storage(self.game.frame())
                if cur and cur == prev:
                    storage = cur
                    break
                prev = cur
                time.sleep(0.35)
            if not storage:
                log.info("gather: couldn't read your storage")
                return done
            b = balanced(storage)
            log.info("gather: %d troop(s) free; storage %s", free,
                     {k: f"{v / 1000:.0f}k" for k, v in storage.items()})
            for _ in range(free):
                # lowest first; nothing free of it around -> the next lowest
                sent = False
                for kind in sorted(KINDS, key=lambda k: (b.get(k, float("inf")), KINDS.index(k))):
                    if (stop and stop()) or self.threat:
                        break
                    xy = self.send_one(kind, stop)
                    if xy:
                        done.append(f"{kind} at {xy[0]},{xy[1]}")
                        b[kind] = b.get(kind, 0) + self.load      # counted in for the next troop
                        sent = True
                        break
                if not sent or self.threat:
                    break
        finally:
            self.game.close_popups()
            self.hunter.zoom_out()
        if done:
            self.last = "sent troops to " + "; ".join(done)
        return done

    def send_one(self, kind, stop=None):
        """One troop to the best full, free point of this kind. (x, y) or None.
        Fast (the user's way): one scan at the searching zoom reads every point's kind,
        level and whether someone's on it; only the best one is zoomed in on and checked."""
        cands = self.scan(kind, stop)
        tried = 0
        for tile, level in cands:
            if (stop and stop()) or tried >= 4 or self.threat:
                break
            tried += 1
            self.say(f"Auto-gather: checking the level {level} {kind} point near {tile[0]:.0f},{tile[1]:.0f}")
            res, xy = self.try_estimate(tile, kind)
            if res == "sent":
                self._scan_taken.append(tile)
                log.info("gather: sent a troop to the level %s %s at %s", level, kind, xy)
                return xy or (round(tile[0]), round(tile[1]))
            if res == "no troop":
                return None
            self.book.blacklist(xy or (round(tile[0]), round(tile[1])), 120 if res in ("taken", "not full") else 30)
            self._scan_taken.append(tile)
            log.info("gather: the %s near %.0f,%.0f skipped (%s)", kind, tile[0], tile[1], res)
        self.book.save()
        if not cands:
            log.info("gather: no free %s point seen around %d,%d", kind, *self.centre)
        return None

    # -------------------------------------------------------------- the scan (searching zoom)
    _scan = (0.0, [])
    _scan_taken: list = []

    def scan(self, kind, stop=None):
        """[(tile, level)] of free points of this kind around the centre, best first (highest
        level, then nearest the centre). One scan covers every kind; kept for 5 minutes."""
        now = time.time()
        if now - self._scan[0] > 300:
            self._scan = (now, self._scan_area(stop))
            self._scan_taken = []
        out = []
        for p in self._scan[1]:
            t = p["tile"]
            if p["kind"] != kind or p["taken"] or not p["level"]:
                continue
            if any(math.dist(t, q) < 1.5 for q in self._scan_taken):
                continue
            if any(until > now and math.dist(t, xy) <= 2 for xy, until in self.book.black.items()):
                continue
            out.append((t, p["level"]))
        # inside the radius first (best first); then anything else the scan saw (its views
        # reach a little past the radius) - better than sending nothing
        out.sort(key=lambda tl: (math.dist(tl[0], self.centre) > self.radius, -tl[1],
                                 math.dist(tl[0], self.centre)))
        return out

    def _scan_area(self, stop=None):
        from .hunter import to_tile
        g, h = self.game, self.hunter
        h.zoom_out()
        axes = h.axes
        f = g.frame()
        H, W = f.shape[:2]
        span_x, span_y = abs(axes[0][0]) * W * 0.7, abs(axes[1][1]) * H * 0.65
        cx, cy = self.centre
        nx, ny = int(math.ceil(self.radius / span_x - 0.5)), int(math.ceil(self.radius / span_y - 0.5))
        views = sorted(((cx + i * span_x, cy + j * span_y) for i in range(-nx, nx + 1) for j in range(-ny, ny + 1)),
                       key=lambda v: math.dist(v, (cx, cy)))
        found = []
        t0 = time.time()
        for k, (x, y) in enumerate(views):
            if stop and stop():
                break
            self.say(f"Auto-gather: looking over the resource points around {cx},{cy} ({k + 1} of {len(views)})")
            if not g.pan_to(int(round(x)), int(round(y)), axes):
                continue
            f = g.fresh_frame()
            end = time.time() + 1.5
            while not screen.icons_drawn(f) and time.time() < end:
                time.sleep(0.1)
                f = g.frame()
            cam = g.read_camera(f)
            if not cam:
                continue
            g._cam = cam[1:3]
            if self._watch(f, cam[1:3]):
                break                                    # a threat: the watcher takes over
            for p in screen.find_resource_points(f):
                t = to_tile(cam[1:3], axes, p["mark"], f.shape)
                if any(math.dist(t, q["tile"]) < 1.5 for q in found):
                    continue
                found.append({**p, "tile": t})
        log.info("gather: scan of %d views in %.0f s: %s", len(views), time.time() - t0,
                 ", ".join(f"{k} {sum(1 for p in found if p['kind'] == k)}" for k in KINDS))
        return found

    # -------------------------------------------------------------- one point from the scan
    def try_estimate(self, tile, kind):
        """Zoom in where the scan put the point, tap its plate (the nearest real one on the
        close-up), check the box. ('sent' | 'taken' | 'not full' | 'gone' | 'no troop' |
        'failed', exact (x, y) or None)."""
        from .hunter import to_screen, to_tile
        g, h = self.game, self.hunter
        if not h.zoom_close():
            return "failed", None
        ac = h._axes_close()
        # a typed jump: exact (a drag went by the camera position from before the scan, stale
        # after zooming in, and missed the point)
        if not g.goto(int(round(tile[0])), int(round(tile[1]))):
            return "failed", None
        time.sleep(0.4)
        tried = []
        for _ in range(4):
            f = g.frame()
            H, W = f.shape[:2]
            if self._watch(f):
                return "failed", None                    # the watcher takes over
            cam = g.read_camera(f)
            if not cam:
                return "failed", None
            q = to_screen(cam[1:3], ac, tile, f.shape)
            plates = [m for m in screen.level_marks(f)
                      if math.dist(m, q) < 0.14 * H and 0.1 * W < m[0] < 0.86 * W and 0.12 * H < m[1] < 0.86 * H]
            plates = [m for m in plates
                      if not any(math.dist(to_tile(cam[1:3], ac, m, f.shape), t) < 1.0 for t in tried)]
            if not plates:
                break
            m = min(plates, key=lambda m: math.dist(m, q))
            tried.append(to_tile(cam[1:3], ac, m, f.shape))
            for off in (0.0, 0.02):
                texts, box = self._tap_box(m[0], m[1] - off * H)
                if not texts:
                    continue
                p = read_point(texts)
                if p and p.get("kind") == kind:
                    return self._check_and_gather(p, texts), p.get("xy")
                g.close_popups()                         # (the close drags the map: look again)
                break
        return "gone", None

    def _check_and_gather(self, p, texts):
        g = self.game
        self.book.note(p)
        if p.get("gatherer"):
            g.close_popups()
            return "taken"
        if not p.get("full"):
            g.close_popups()
            return "not full"
        btn = next((t for t in texts if re.fullmatch(r"gather|recolectar|recoger", t.text.strip(), re.I)), None)
        if not btn:
            g.close_popups()
            return "failed"
        g.b.click(*btn.center)
        return self._dispatch()

    def surveyed_fresh(self, now=None):
        """The area around the centre was surveyed recently, at least this wide."""
        sv = self.book.surveyed.get(kind_key(self.centre))
        if not isinstance(sv, dict):
            return False
        return ((now or time.time()) - sv.get("t", 0) < self.survey_every
                and sv.get("r", 0) >= self.radius)

    # -------------------------------------------------------------- one point
    def _tap_box(self, x, y):
        """Tap at (x, y) and read the box that opens: (texts, box) or (None, None)."""
        self.game.b.click(int(x), int(y))
        box = None
        for _ in range(15):
            time.sleep(0.07)
            box = screen.find_info_box(self.game.frame())
            if box:
                break
        if not box:
            return None, None
        time.sleep(0.2)
        f = self.game.frame()
        return screen.read_text(f, box), box

    def try_point(self, xy, kind):
        """'sent', 'not full', 'taken', 'gone', 'no troop' or 'failed'."""
        g = self.game
        if not g.goto(*xy):
            return "failed"
        time.sleep(0.4)
        f = g.frame()
        H, W = f.shape[:2]
        texts = None
        for off in (0.0, 0.02, -0.02):                  # the point sits on the screen's middle
            texts, box = self._tap_box(W / 2, H / 2 - off * H)
            if not texts:
                continue
            p = read_point(texts)
            if p and p.get("kind") == kind and p.get("xy") and math.dist(p["xy"], xy) <= 1:
                break
            g.close_popups()
            texts = None
        if not texts:
            return "gone"
        self.book.note(p)
        if p.get("gatherer"):
            g.close_popups()
            return "taken"
        if not p.get("full"):
            g.close_popups()
            return "not full"
        btn = next((t for t in texts if re.fullmatch(r"gather|recolectar|recoger", t.text.strip(), re.I)), None)
        if not btn:
            g.close_popups()
            return "failed"
        g.b.click(*btn.center)
        return self._dispatch()

    def _find_text(self, rx, region=None, timeout=3.0):
        end = time.time() + timeout
        while time.time() < end:
            f = self.game.frame()
            H, W = f.shape[:2]
            r = region(W, H) if region else None
            hits = [t for t in screen.read_text(f, r) if re.search(rx, t.text, re.I)]
            if hits:
                return hits[0], f
            time.sleep(0.25)
        return None, None

    def _dispatch(self):
        """After GATHER: 'New Troop' (send one from the city), then MARCH with what the game
        puts in. Reads the troop's load on the way (to balance the next choice)."""
        t, _ = self._find_text(r"new\s*troop|nueva\s*tropa",
                               lambda W, H: (int(0.55 * W), 0, int(0.45 * W), int(0.45 * H)))
        if not t:
            log.info("gather: no 'New Troop' button (no troop free?)")
            self.game.close_popups()
            return "no troop"
        self.game.b.click(*t.center)
        m, f = self._find_text(r"^march|marchar", None, timeout=4.0)
        if not m:
            log.warning("gather: the troop screen didn't open")
            self.game.b.press("Escape")
            return "failed"
        try:
            texts = screen.read_text(f)
            ld = next((t for t in texts if re.search(r"load|carga", t.text, re.I)), None)
            if ld:
                v = parse_amount(re.sub(r"(?i)load|carga|[:：]", "", ld.text)) or next(
                    (parse_amount(u.text) for u in texts if abs(u.center[1] - ld.center[1]) < 15
                     and u.box[0] > ld.box[0] and parse_amount(u.text)), None)
                if v and v > 1000:
                    self.load = float(v)
        except Exception:
            pass
        self.game.b.click(*m.center)
        time.sleep(1.0)
        still, _ = self._find_text(r"^march|marchar", None, timeout=0.6)
        if still:
            log.warning("gather: MARCH didn't go")
            self.game.b.press("Escape")
            return "failed"
        return "sent"

    # -------------------------------------------------------------- survey
    def survey(self, stop=None):
        """Tap every level plate in close-zoom views around the centre and note what's there."""
        from .hunter import to_tile
        g, h = self.game, self.hunter
        ac = h._axes_close()
        if not ac:
            return
        f = g.frame()
        H, W = f.shape[:2]
        span_x = abs(ac[0][0]) * W * 0.7                # tiles across one close view (kept inside)
        span_y = abs(ac[1][1]) * H * 0.6
        cx, cy = self.centre
        r = self.radius
        spots = []
        nx, ny = int(math.ceil(r / span_x)), int(math.ceil(r / span_y))
        for j in range(-ny, ny + 1):
            for i in range(-nx, nx + 1):
                x, y = cx + i * span_x, cy + j * span_y
                if math.dist((x, y), (cx, cy)) <= r + 0.5 * max(span_x, span_y):
                    spots.append((int(round(x)), int(round(y))))
        spots.sort(key=lambda s: math.dist(s, (cx, cy)))
        log.info("gather: surveying %d close-up views around %d,%d", len(spots), cx, cy)
        seen_xy = set()
        for k, (x, y) in enumerate(spots):
            if stop and stop():
                break
            self.say(f"Auto-gather: looking at the resource points around {cx},{cy} ({k + 1} of {len(spots)})")
            if not g.goto(x, y):
                continue
            time.sleep(0.5)
            done = []
            for _ in range(16):
                f = g.frame()
                cam = g.read_camera(f)
                if not cam:
                    break
                plates = [m for m in screen.level_marks(f)
                          if 0.1 * W < m[0] < 0.85 * W and 0.12 * H < m[1] < 0.84 * H]
                todo = [(m, to_tile(cam[1:3], ac, m, f.shape)) for m in plates]
                todo = [(m, t) for m, t in todo
                        if not any(math.dist(t, d) < 1.2 for d in done)
                        and not any(math.dist(t, s) < 1.2 for s in seen_xy)]
                if not todo:
                    break
                m, t = min(todo, key=lambda mt: math.dist(mt[0], (W / 2, H / 2)))
                done.append(t)
                for off in (0.0, 0.02):
                    texts, box = self._tap_box(m[0], m[1] - off * H)
                    if not texts:
                        continue
                    p = read_point(texts)
                    g.close_popups()
                    if p and p.get("xy"):
                        seen_xy.add(tuple(p["xy"]))
                        if p.get("kind") in KINDS:
                            self.book.note(p)
                        break
        if not (stop and stop()):
            self.book.surveyed[kind_key(self.centre)] = {"t": time.time(), "r": self.radius}
        self.book.save()
        n = sum(1 for xy in self.book.points if math.dist(xy, self.centre) <= self.radius)
        log.info("gather: %d resource points known around %d,%d", n, cx, cy)


def kind_key(centre):
    return f"{int(centre[0])},{int(centre[1])}"
