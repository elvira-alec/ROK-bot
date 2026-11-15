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


def find_march_button(frame):
    """Centre of the troop window's orange MARCH button (bottom right of the window, next to
    the blue MAX), or None. By colour: quick."""
    import cv2
    import numpy as np
    H, W = frame.shape[:2]
    x0, y0 = int(0.38 * W), int(0.55 * H)
    hsv = cv2.cvtColor(frame[y0:int(0.95 * H), x0:int(0.88 * W)], cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= 10) & (hsv[..., 0] <= 30) & (hsv[..., 1] > 150) & (hsv[..., 2] > 190)).astype(np.uint8)
    n, _, st, cent = cv2.connectedComponentsWithStats(m)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if 0.08 * W < w < 0.25 * W and 0.04 * H < h < 0.12 * H and a > 0.55 * w * h:
            if best is None or a > best[1]:
                best = (i, a)
    if best is None:
        return None
    cx, cy = cent[best[0]]
    return int(x0 + cx), int(y0 + cy)


# settings changed on the panel while running (same process): {'min_full': percent}
LIVE: dict = {}


def full_enough(p, pct=100):
    """The point holds at least pct % of its level's full amount (the user's table in
    farms.FULL). 100 = intact only (the user: "make sure that it's intact... if the value
    doesn't match the things I gave you, you don't do it")."""
    full = farms.FULL.get(p.get("kind"), {}).get(p.get("level"))
    r = p.get("reserves")
    return bool(full and r) and r >= full * max(1, min(100, pct)) / 100


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
    # (a cut-off "None" reads "Non" / "No": live 2026-11-15, free points taken as occupied)
    if gatherer and re.fullmatch(r"no(ne?)?|ninguno|nadie|-+", gatherer.strip(" .:"), re.I):
        gatherer = None
    full = farms.FULL.get(kind, {}).get(level)
    return {"xy": xy, "kind": kind, "level": level, "reserves": reserves, "gatherer": gatherer,
            "full": bool(full and reserves and reserves >= full)}   # intact: exactly the table


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
        self.load = d.get("load")                       # a troop's load, from the last send

    def save(self):
        try:
            self.path.write_text(json.dumps({
                "points": {f"{x},{y}": v for (x, y), v in self.points.items()},
                "black": {f"{x},{y}": v for (x, y), v in self.black.items()},
                "surveyed": self.surveyed, "load": self.load}, indent=1))
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
        # a troop's load: read on the MARCH screen at each send and remembered (a 450K guess
        # after a restart made it skip a partly mined level 6 that would have filled a troop)
        self.load = float(self.book.load or g.get("load", 450_000))
        self._min_full = float(g.get("min_full", 100))      # panel slider: fullness required, %
        self._at = 0.0
        self.paused_until = 0.0                         # after the watcher pulled a troop back
        self.last = ""                                  # what it did last (for the panel)
        self._scan = (0.0, [])                          # (when, points seen by the last scan)
        self._scan_taken = []                           # tiles tried since that scan
        self._checked = set()                           # exact X:Y boxes read since that scan
        from .persp import Persp
        self.persp = Persp()                            # map pixel -> tile, perspective-correct
        self.pending = []                               # points troops were sent to (watched)
        self.threat = False                             # the watcher saw something: stop
        self.storage, self.storage_at = None, 0.0       # your storage, last read (panel stats)
        self.sent = []                                  # recent sends (panel): time, kind, level, xy

    def _watch(self, frame, cam=None) -> bool:
        """The watcher's checks on a picture auto-gather took anyway (so it isn't blind while
        gathering): armies marching at your troops / city, the red eye / swords. True (and
        auto-gather stops) when something's coming; the alerts go out with the round."""
        h = self.hunter
        try:
            if hasattr(h, "_help_alliance"):
                h._help_alliance(frame)              # the help bubble: shows when zoomed in
            alerts = h.guard_frame(frame, cam) if cam else (h._dangers_frame(frame) or [])
        except Exception:
            log.debug("gather: watch check failed", exc_info=True)
            return False
        if alerts:
            h._guard_alerts = list(getattr(h, "_guard_alerts", []) or []) + alerts
        # a scout near a troop alerts but doesn't stop auto-gather (it can't attack) - only an
        # army does, or the red swords
        armies = [a for a in alerts if "SCOUT" not in a.title]
        danger = "attacked" in set(getattr(h, "dangers", set())) or getattr(h, "warned", False)
        if armies or danger:
            if not self.threat:
                log.warning("gather: something's coming - stopping auto-gather, the watcher takes over")
            self.threat = True
        return self.threat

    def read_storage_now(self):
        """Your storage from the top bar (only shown at the close zoom): two reads must agree.
        Kept for the panel. None if it couldn't be read."""
        time.sleep(0.5)
        prev = None
        for _ in range(8):
            f = self.game.frame()
            if hasattr(self.hunter, "_help_alliance"):
                self.hunter._help_alliance(f)        # zoomed in: the help bubble shows here
            cur = read_storage(f)
            if cur and cur == prev:
                self.storage, self.storage_at = cur, time.time()
                return cur
            prev = cur
            time.sleep(0.35)
        return None

    def refresh_storage(self):
        """For the panel's numbers between sends: zoom in a moment, read, zoom back out."""
        try:
            if self.hunter.zoom_close():
                self.read_storage_now()
        finally:
            self.hunter.zoom_out()
        self.storage_at = max(self.storage_at, time.time())     # (don't retry at once if it failed)

    def incoming(self, troops):
        """Roughly what the troops out gathering will bring back, per resource: each one's
        point type from what auto-gather has seen there (its box or its scan), one load of it
        (food-equivalent load x what the point gives), no more than the point has left."""
        out = {k: 0 for k in KINDS}
        scan = self._scan[1] if self._scan else []
        for t in (troops or {}).values():
            if t.get("state") != "gathering" or "x" not in t:
                continue
            xy = (t["x"], t["y"])
            p = next((v for k, v in self.book.points.items() if math.dist(k, xy) <= 1.5), None)
            kind = t.get("kind") or (p.get("kind") if p else None)     # read at the troop-list jump
            left = p.get("reserves") if p else None
            if not kind:
                q = next((q for q in scan if math.dist(q["tile"], xy) <= 1.5), None)
                kind = q["kind"] if q else None
            if kind in KINDS:
                amount = self.load * RATIO[kind]
                out[kind] += int(min(amount, left) if left else amount)
        return out

    def summary(self, troops=None):
        """For the panel: storage, what each counts as, what's coming in, the next pick,
        recent sends."""
        b = balanced(self.storage) if self.storage else {}
        nxt = min(KINDS, key=lambda k: (b.get(k, float("inf")), KINDS.index(k))) if b else None
        inc = self.incoming(troops)
        return {"storage": self.storage, "incoming": inc, "ratio": RATIO,
                "storage_at": time.strftime("%H:%M", time.localtime(self.storage_at)) if self.storage else None,
                "balanced": {k: round(v) for k, v in b.items()},
                "next": nxt, "next_brings": round(self.load * RATIO[nxt]) if nxt else None,
                "centre": list(self.centre), "radius": self.radius,
                "paused_min": max(0, round((self.paused_until - time.time()) / 60)),
                "sent": self.sent[-6:], "last": self.last}

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
            # a number can be missed, a decimal point lost ('1.1M' read as '11M'): two reads
            # must agree
            storage = self.read_storage_now()
            if not storage:
                log.info("gather: couldn't read your storage")
                return done
            b = balanced(storage)
            log.info("gather: %d troop(s) free; storage %s", free,
                     {k: f"{v / 1000:.0f}k" for k, v in storage.items()})
            for _ in range(free):
                # lowest first; nothing free of it around -> the next lowest
                sent = False
                order = sorted(KINDS, key=lambda k: (b.get(k, float("inf")), KINDS.index(k)))
                # never the most plentiful one: if the lower ones all fail, wait for the next
                # round (it sent a level 4 food with 2.7M food in storage)
                for kind in order[:-1]:
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
        cands = list(self.scan(kind, stop))
        tried = 0
        while cands:
            tile, level = cands.pop(0)
            if (stop and stop()) or tried >= 8 or self.threat:
                break
            self.say(f"Auto-gather: checking the level {level or '?'} {kind} point near {tile[0]:.0f},{tile[1]:.0f}")
            self._last_box = None
            self._need = max([level or 0] + [lv for _, lv in cands if lv])
            res, xy = self.try_estimate(tile, kind, level)
            self._need = 0
            self._keep_miss(tile, kind, level)
            if res == "double":
                # the scan saw one point twice (views land ~3 tiles apart): not a try
                self._scan_taken.append(tile)
                continue
            if res == "lower" and xy:
                # its real level, from the box: queued again at that level (checked again
                # when its turn comes - its exact X:Y this time)
                real = (getattr(self, "_last_box", None) or {}).get("level")
                self._scan_taken.append(tile)
                log.info("gather: the %s near %.0f,%.0f is level %s, not %s", kind, tile[0], tile[1], real, level)
                at = next((i for i, (_, lv) in enumerate(cands) if (lv or 0.5) < real), len(cands))
                cands.insert(at, ((float(xy[0]), float(xy[1])), real))
                continue
            tried += 1
            if xy:
                self._checked.add(tuple(xy))
            if res == "sent":
                self._scan_taken.append(tile)
                self.sent.append({"time": time.strftime("%H:%M"), "kind": kind, "level": level,
                                  "xy": list(xy or (round(tile[0]), round(tile[1])))})
                real = (getattr(self, "_last_box", None) or {}).get("level") or level
                self.sent[-1]["level"] = real
                log.info("gather: sent a troop to the level %s %s at %s", real, kind, xy)
                self.note_sent(xy or (round(tile[0]), round(tile[1])), kind, real)
                return xy or (round(tile[0]), round(tile[1]))
            if res == "no troop":
                return None
            self.book.blacklist(xy or (round(tile[0]), round(tile[1])), 120 if res in ("taken", "not full") else 30)
            self._scan_taken.append(tile)
            if xy:
                self._scan_taken.append(tuple(xy))       # (the real spot: its double is skipped too)
            log.info("gather: the %s near %.0f,%.0f skipped (%s)", kind, tile[0], tile[1], res)
        self.book.save()
        if not cands:
            log.info("gather: no free %s point seen around %d,%d", kind, *self.centre)
        return None

    # -------------------------------------------------------------- the scan (searching zoom)
    _scan = (0.0, [])
    _scan_taken: list = []
    _checked: set = set()

    def scan(self, kind, stop=None):
        """[(tile, level)] of free points of this kind around the centre, best first (highest
        level, then nearest the centre). One scan covers every kind; kept for 5 minutes."""
        now = time.time()
        if now - self._scan[0] > 300:
            self._scan = (now, self._scan_area(stop))
            self._scan_taken = []
            self._checked = set()                      # exact X:Y boxes read since that scan
        out = []
        for p in self._scan[1]:
            t = p["tile"]
            if p["kind"] != kind or p["taken"]:
                continue
            if not p["level"]:
                # no level plate: an alliance resource point (a small shield on the icon; the
                # user: "they don't count") - night plates cut off at the top are read again
                # lower by find_resource_points, so a real point has its level
                continue
            # only the checked spot itself (the scan's estimate is ~1 tile off): a wider skip
            # threw away full points next to a checked one (the user: "don't miss a single one")
            if any(math.dist(t, q) < 1.3 for q in self._scan_taken):
                continue
            # a point a troop is on its way to (no badge on it yet): never a second troop
            if any(math.dist(t, pd["xy"]) < 1.5 for pd in getattr(self, "pending", [])):
                continue
            if any(until > now and math.dist(t, xy) <= 1.3
                   for xy, until in self.book.black.items()):
                continue
            out.append((t, p["level"]))
        # highest level first, wherever the scan saw it (the user: "always the highest ones";
        # inside-the-radius first sent level 3s before level 4s just past it); nearer breaks ties
        # an unread plate goes last: those are icons without a plate (a small shield on them,
        # seen at night - nothing to tap at the close zoom either); a cut-off night plate is
        # read again lower now (find_resource_points)
        out.sort(key=lambda tl: (-(tl[1] if tl[1] else 0.5), math.dist(tl[0], self.centre)))
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
            before = len(found)
            self.say(f"Auto-gather: looking over the resource points around {cx},{cy} ({k + 1} of {len(views)})")
            if not g.pan_to(int(round(x)), int(round(y)), axes):
                continue
            f = g.fresh_frame()
            end = time.time() + 1.5
            while not screen.icons_drawn(f) and time.time() < end:
                time.sleep(0.1)
                f = g.frame()
            if screen.view_level(f) != "patrol":
                # zoomed in by something (a tap that landed on an icon): back out and look
                # again - every view after it was read at the wrong zoom (2026-11-15)
                log.info("gather: scan view %d wasn't at the searching zoom; zooming out", k + 1)
                g._at = None
                h.zoom_out()
                if not g.pan_to(int(round(x)), int(round(y)), axes):
                    continue
                f = g.fresh_frame()
                time.sleep(0.3)
                f = g.frame()
                if screen.view_level(f) != "patrol":
                    continue
            cam = g.read_camera(f)
            if not cam:
                continue
            g._cam = cam[1:3]
            if self._watch(f, cam[1:3]):
                break                                    # a threat: the watcher takes over
            pts = self._settled_points(g, f)
            exact = self.persp.usable(axes, f.shape)
            here = [(p, self.persp.to_tile(cam[1:3], axes, p["mark"], f.shape)) for p in pts]
            # the same point seen from two views lands a tile or two apart: one level 6 stone
            # came out as 4 "points", 3 of them then checked for nothing (20 s each). But two
            # points on one picture are two real points, and an earlier point is the double of
            # one point here at most (nearest pairs first) - a full neighbour is never merged away
            pairs = sorted(((math.dist(t, q["tile"]), i, j) for i, (p, t) in enumerate(here)
                            for j, q in enumerate(found[:before])
                            # (perspective-correct positions: a double lands within ~1.8 tiles,
                            # and a real neighbour 2 tiles off stays a separate point)
                            if math.dist(t, q["tile"]) < ((1.8 if exact else 2.5) if q["kind"] == p["kind"] else 1.2)))
            twin_of, used = {}, set()
            for _, i, j in pairs:
                if i not in twin_of and j not in used:
                    twin_of[i] = j
                    used.add(j)
            for i, (p, t) in enumerate(here):
                if i in twin_of:
                    twin = found[twin_of[i]]
                    if twin["kind"] == p["kind"] and (p["level"] or 0) > (twin["level"] or 0):
                        twin.update(level=p["level"], taken=p["taken"])     # keep the higher level
                    continue
                mx, my = p["mark"]
                found.append({**p, "tile": t, "cam": tuple(cam[1:3]), "shape": f.shape[:2],
                              "crop": (f[max(0, my - 30):my + 30, max(0, mx - 40):mx + 40].copy(),
                                                       (max(0, mx - 40), max(0, my - 30)), f.shape[:2])})
        log.info("gather: scan of %d views in %.0f s: %s", len(views), time.time() - t0,
                 ", ".join(f"{k} {sum(1 for p in found if p['kind'] == k)}" for k in KINDS))
        return found

    @staticmethod
    def _settled_points(g, f):
        """The points on this view, each level read on two pictures (one picture read a
        level 3 wood as 2 - live 2026-11-15, the user caught it); when they disagree a third
        picture decides (majority), else the level stays unknown (still a candidate)."""
        H = f.shape[0]
        pts = screen.find_resource_points(f)
        time.sleep(0.3)
        f2 = g.frame()
        pts2 = screen.find_resource_points(f2)
        f3 = None

        def near(lst, m):
            return min(lst, key=lambda q: math.dist(q["mark"], m), default=None) if lst else None
        for p in pts:
            q = near(pts2, p["mark"])
            if q is None or math.dist(q["mark"], p["mark"]) > 0.02 * H:
                continue                                   # the map moved: keep the one reading
            p["taken"] = p["taken"] or q["taken"]
            if q["level"] == p["level"]:
                continue
            if f3 is None:
                time.sleep(0.3)
                f3 = g.frame()
            r = screen.plate_level(f3, p["mark"])
            votes = [p["level"], q["level"], r]
            p["level"] = next((v for v in votes if v and votes.count(v) >= 2), None)
        return pts

    # -------------------------------------------------------------- one point from the scan
    def try_estimate(self, tile, kind, level=None):
        """Zoom in where the scan put the point, tap its plate (the nearest real one on the
        close-up), check the box. ('sent' | 'taken' | 'not full' | 'gone' | 'no troop' |
        'failed', exact (x, y) or None)."""
        from .hunter import to_screen, to_tile
        g, h = self.game, self.hunter
        if not h.zoom_close():
            return "failed", None
        ac = h._axes_close()
        # already on screen (points sit close together): tapped where it is, no jump (~3 s)
        jumped = True
        if self._close_off is not None:
            f0 = g.frame()
            cam0 = g.read_camera(f0)
            if cam0 and screen.view_level(f0) == "close":
                H0, W0 = f0.shape[:2]
                co = self._close_off
                q0 = to_screen(cam0[1:3], ac, (tile[0] - co[0], tile[1] - co[1]), f0.shape)
                if 0.2 * W0 < q0[0] < 0.78 * W0 and 0.22 * H0 < q0[1] < 0.78 * H0:
                    jumped = False
        # else a typed jump: exact (a drag went by the camera position from before the scan,
        # stale after zooming in, and missed the point)
        if jumped:
            if not g.goto(int(round(tile[0])), int(round(tile[1]))):
                return "failed", None
            time.sleep(0.4)
        tried = []
        seen_plates = False
        for _ in range(5):
            f = g.frame()
            H, W = f.shape[:2]
            if self._watch(f):
                return "failed", None                    # the watcher takes over
            cam = g.read_camera(f)
            if not cam:
                return "failed", None
            # the close-zoom projection is off by a steady ~2.5 tiles (perspective); learnt from
            # every box read (its exact X:Y vs where it was tapped) and taken off
            co = self._close_off or (0.0, 0.0)
            q = to_screen(cam[1:3], ac, (tile[0] - co[0], tile[1] - co[1]), f.shape)

            def real_tile(m):
                t = to_tile(cam[1:3], ac, m, f.shape)
                return (t[0] + co[0], t[1] + co[1])
            # the scan's spot can be a tile or so off (live: a level 6 wood 150 px from the middle,
            # a barbarian fort right in it - the old 0.14 H search tapped the fort, found nothing
            # else and called the point "gone"): look up to ~1.5 tiles round, the plate with the
            # level the scan read first, then the nearest
            marks = screen.level_marks(f)
            marks += [w for w in screen.white_plates(f)
                      if not any(math.dist(w, m) < 0.02 * H for m in marks)]
            seen_plates = seen_plates or bool(marks)
            # the projection at the close zoom can be ~2.5 tiles off (live: a level 6 stone
            # 0.37 H from it, called "gone" untapped): round it or round the middle (the jump
            # centres the point)
            mid = (W / 2, H / 2) if jumped else q          # (no jump: the point isn't centred)

            def off(m):
                return min(math.dist(m, q), math.dist(m, mid))
            plates = [m for m in marks
                      if off(m) < 0.42 * H and 0.1 * W < m[0] < 0.86 * W and 0.12 * H < m[1] < 0.86 * H]
            plates = [m for m in plates
                      if not any(math.dist(to_tile(cam[1:3], ac, m, f.shape), t) < 1.0 for t in tried)]
            if self._close_off is not None and self._checked:
                # plates this scan already checked (the scan lists a point twice when two views
                # place it ~3 tiles apart): not tapped again
                plates = [m for m in plates if not any(math.dist(real_tile(m), xy) < 1.2 for xy in self._checked)]
            if not plates:
                break
            def rank(m):
                lv = screen.plate_level(f, m) if level else None
                return (0 if (level and lv == level) else 1, off(m))
            m = min(plates, key=rank)
            tried.append(to_tile(cam[1:3], ac, m, f.shape))
            for dy in (0.0, 0.02):
                texts, box = self._tap_box(m[0], m[1] - dy * H)
                if not texts:
                    continue
                p = read_point(texts)
                self._learn_close_off(p, to_tile(cam[1:3], ac, m, f.shape))
                if p and p.get("kind") == kind:
                    if p.get("xy") and tuple(p["xy"]) in self._checked:
                        g.close_popups()
                        return "double", p.get("xy")        # this scan checked it already
                    return self._check_and_gather(p, texts, level), p.get("xy")
                g.close_popups()                         # (the close drags the map: look again)
                break
        # plates can be missed on light ground (white plates on pale sand, live): tap the spot
        # itself and the tiles round it, nearest first - each box says exactly what's there.
        # Only when no plate showed at all: with plates seen, the point isn't there (the user:
        # "clicking everywhere, popups" - 25 s of taps round a gone point)
        if seen_plates:
            return "gone", None
        done_xy = set()
        for dx, dy in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)):
            f = g.frame()
            H, W = f.shape[:2]
            if self._watch(f):
                return "failed", None
            cam = g.read_camera(f)
            if not cam:
                return "failed", None
            t = (round(tile[0]) + dx, round(tile[1]) + dy)
            if t in done_xy:
                continue
            q = to_screen(cam[1:3], ac, t, f.shape)
            if not (0.1 * W < q[0] < 0.86 * W and 0.12 * H < q[1] < 0.86 * H):
                continue
            texts, box = self._tap_box(q[0], q[1] - 0.01 * H)
            if not texts:
                continue
            p = read_point(texts)
            if p and p.get("xy"):
                done_xy.add(tuple(p["xy"]))
            if p and p.get("kind") == kind:
                if p.get("xy") and tuple(p["xy"]) in self._checked:
                    g.close_popups()
                    return "double", p.get("xy")
                return self._check_and_gather(p, texts, level), p.get("xy")
            g.close_popups()
        return "gone", None

    def _check_and_gather(self, p, texts, level=None):
        g = self.game
        self.book.note(p)
        self._last_box = p
        # never lower than the scan's level for it, nor than any candidate still waiting (an
        # unread plate went first and its box said 3 with 4s waiting - 2026-11-15)
        need = max(level or 0, getattr(self, "_need", 0) or 0)
        if need and p.get("level") and p["level"] < need:
            # the scan misread the plate (a level 3 wood read as 5 - a troop went to a 3 with
            # 4s, 5s around, the user 2026-11-15): back in the queue at its real level
            g.close_popups()
            return "lower"
        if p.get("gatherer"):
            g.close_popups()
            return "taken"
        if not full_enough(p, self.min_full):          # 100 % = intact only (the panel slider)
            g.close_popups()
            return "not full"
        btn = next((t for t in texts if re.fullmatch(r"gather|recolectar|recoger", t.text.strip(), re.I)), None)
        if not btn:
            g.close_popups()
            return "failed"
        g.b.click(*btn.center)
        return self._dispatch()


    _close_off = None              # (dx, dy) tiles: box X:Y minus the close-zoom projection

    def _learn_close_off(self, p, projected):
        if not p or not p.get("xy"):
            return
        d = (p["xy"][0] - projected[0], p["xy"][1] - projected[1])
        if math.hypot(*d) > 6:
            return                                   # a far-off read: not the plate tapped
        o = self._close_off
        self._close_off = d if o is None else (0.6 * o[0] + 0.4 * d[0], 0.6 * o[1] + 0.4 * d[1])

    def _learn_pair(self, tile, kind, box):
        """The candidate's box gave its exact X:Y: one more sample for the map positions."""
        if not box.get("xy") or box.get("kind") != kind or math.dist(box["xy"], tile) > 2.5:
            return
        q = min((q for q in self._scan[1] if q["kind"] == kind and q.get("cam")),
                key=lambda q: math.dist(q["tile"], tile), default=None)
        if q is None or math.dist(q["tile"], tile) > 0.5:
            return
        try:
            self.persp.add_pair(q["mark"], q["shape"], q["cam"], box["xy"], self.hunter.axes)
        except Exception:
            log.debug("gather: couldn't learn the map position", exc_info=True)

    MISS_DIR = Path("plate_misses")

    def _keep_miss(self, tile, kind, level):
        """The box's level differs from the map plate's read: keep that plate picture with the
        true level (more real samples for the digit references - the user: "keep on doing it
        till it never fails")."""
        box = getattr(self, "_last_box", None) or {}
        self._learn_pair(tile, kind, box)
        real = box.get("level")
        if not real or real == level:
            return
        if not box.get("xy") or math.dist(box["xy"], tile) > 2.5:
            return                                   # a neighbour's box, not this plate's
        q = min((q for q in self._scan[1] if q["kind"] == kind and q.get("crop") is not None),
                key=lambda q: math.dist(q["tile"], tile), default=None)
        if q is None or math.dist(q["tile"], tile) > 0.5:
            return
        try:
            import cv2
            crop, origin, size = q["crop"]
            self.MISS_DIR.mkdir(exist_ok=True)
            name = time.strftime("%Y%m%d-%H%M%S") + f"_read{level}_true{real}"
            cv2.imwrite(str(self.MISS_DIR / f"{name}.png"), crop)
            mark = (q["mark"][0] - origin[0], q["mark"][1] - origin[1])
            (self.MISS_DIR / f"{name}.json").write_text(json.dumps(
                {"mark": mark, "origin": origin, "size": list(size), "level": real, "read": level}))
            log.info("gather: plate read %s, box says %s - kept in %s", level, real, self.MISS_DIR)
        except Exception:
            log.debug("gather: couldn't keep the plate", exc_info=True)

    # -------------------------------------------------------------- after sending
    def note_sent(self, xy, kind, level):
        """Watch this point until our troop is gathering there (the user: someone else can
        get there first and leave the troop stranded)."""
        self.pending = [p for p in getattr(self, "pending", []) if p["xy"] != tuple(xy)]
        self.pending.append({"xy": tuple(xy), "kind": kind, "level": level, "at": time.time(),
                             "troop": None, "odd": 0})

    PENDING_MAX = 1800          # a troop not there after 30 min: stop watching

    def check_pending(self, h) -> list[str]:
        """For each point a troop was sent to and isn't gathering at yet (after the troop
        list was read this round): someone else's pickaxe there (red / blue) while ours is on
        its way -> recall ours; ours standing there without gathering for two rounds
        (stranded) -> recall it. Returns what it did."""
        out = []
        now = time.time()
        troops = h.my_troops or {}
        moving = getattr(h, "MOVING_STATES", ("marching", "returning", "fighting", "stopped"))
        for pd in list(getattr(self, "pending", [])):
            if now - pd["at"] > self.PENDING_MAX:
                self.pending.remove(pd)
                continue
            near = [t for t in troops.values() if "x" in t and math.dist((t["x"], t["y"]), pd["xy"]) <= 1.5]
            if any(t.get("state") == "gathering" for t in near):
                self.pending.remove(pd)                # ours is gathering there: fine
                continue
            # which troop is going there: the first marching one not already someone's
            mine = {p["troop"] for p in self.pending if p is not pd}
            if pd["troop"] is None or troops.get(pd["troop"], {}).get("state") not in moving + ("other", "idle"):
                cand = [t for t in troops.values() if t.get("state") == "marching" and t["index"] not in mine
                        and "x" in t]
                if cand:
                    pd["troop"] = min(cand, key=lambda t: math.dist((t["x"], t["y"]), pd["xy"]))["index"]
            stuck = [t for t in near if t.get("state") not in ("gathering",) + moving]
            pd["odd"] = pd["odd"] + 1 if stuck else 0
            if pd["odd"] >= 2:
                out += [f"{pd['kind']} at {pd['xy'][0]},{pd['xy'][1]}: troop stranded, "]
                out[-1] += "; ".join(h._recall_troop(stuck[0]["index"]))
                self.book.blacklist(pd["xy"], 120)
                self.pending.remove(pd)
                continue
            col = self._badge_at(h, pd)
            if col in ("red", "blue"):
                who = "an alliance member" if col == "blue" else "someone else"
                msg = f"{pd['kind']} at {pd['xy'][0]},{pd['xy'][1]}: {who} got there first"
                if pd["troop"] is not None:
                    msg += "; " + "; ".join(h._recall_troop(pd["troop"]))
                out.append(msg)
                self.book.blacklist(pd["xy"], 120)
                self.pending.remove(pd)
        return out

    def _badge_at(self, h, pd):
        """The pickaxe badge colour on the point (searching zoom), None if none / not seen."""
        from .hunter import to_screen
        g = self.game
        try:
            if not g.pan_to(int(pd["xy"][0]), int(pd["xy"][1]), h.axes):
                return None
            f = g.fresh_frame()
            cam = g.read_camera(f)
            if not cam:
                return None
            self._watch(f, cam[1:3])
            H = f.shape[0]
            q = to_screen(cam[1:3], h.axes, pd["xy"], f.shape)
            pts = [p for p in screen.find_resource_points(f)
                   if p["kind"] == pd["kind"] and math.dist(p["mark"], q) < 0.1 * H]
            if not pts:
                return None
            m = min(pts, key=lambda p: math.dist(p["mark"], q))["mark"]
            return screen.gather_badge_on(f, m)
        except Exception:
            log.debug("gather: badge check failed", exc_info=True)
            return None

    @property
    def min_full(self):
        return float(LIVE.get("min_full", getattr(self, "_min_full", 100)))

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
            texts = screen.read_text(f, r)
            hits = [t for t in texts if re.search(rx, t.text, re.I)]
            if hits:
                self._last_texts = texts
                return hits[0], f
            time.sleep(0.15)
        return None, None

    def _dispatch(self):
        """After GATHER: 'New Troop' (send one from the city), then MARCH with what the game
        puts in. (No troop amounts: the user, "ship it with whatever troops it suggests".)"""
        t, _ = self._find_text(r"new\s*troop|nueva\s*tropa",
                               lambda W, H: (int(0.55 * W), 0, int(0.45 * W), int(0.45 * H)))
        if not t:
            log.info("gather: no 'New Troop' button (no troop free?)")
            self.game.close_popups()
            return "no troop"
        self.game.b.click(*t.center)
        # the orange MARCH button by its colour (a text read took 1-2 s each on this PC)
        btn, f, end = None, None, time.time() + 4.0
        while time.time() < end:
            f = self.game.frame()
            btn = find_march_button(f)
            if btn:
                break
            time.sleep(0.1)
        if not btn:
            log.warning("gather: the troop screen didn't open")
            self.game.b.press("Escape")
            return "failed"
        time.sleep(0.15)                                 # the game's filled in the troops
        self.game.b.click(*btn)
        time.sleep(0.3)
        if find_march_button(self.game.frame()):
            time.sleep(0.6)                              # (still closing?)
        still = find_march_button(self.game.frame())
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
