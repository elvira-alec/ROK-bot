"""Hunt one player's armies: sweep the map around his city, read his name over
his troops, work out where each army is and where it's going, and turn changes
between sweeps into alerts (farmers pulled back, armies sent out, ...).
"""

from __future__ import annotations

import csv
import logging
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from . import screen
from .detectors import AttackAlertDetector
from .watcher import Alert

log = logging.getLogger("rokwatch")


# ---------------------------------------------------------------- screen <-> map

def to_tile(camera, axes, point, frame_shape):
    """Map coordinates of a screen point. camera = (x, y) at the screen centre,
    axes = 2x2 tiles-per-pixel matrix learned at this zoom level."""
    h, w = frame_shape[:2]
    d = np.array([point[0] - w / 2, point[1] - h / 2], float)
    t = np.array(camera, float) + np.array(axes, float) @ d
    return float(t[0]), float(t[1])


def view_size_tiles(axes, frame_shape):
    """How many tiles the screen covers across and down (roughly)."""
    h, w = frame_shape[:2]
    a = np.abs(np.array(axes, float))
    return float(a[0, 0] * w + a[0, 1] * h), float(a[1, 0] * w + a[1, 1] * h)


def sweep_points(centre, radius, axes, frame_shape, overlap=0.8):
    """Camera stops covering everything within `radius` tiles of centre, his city
    first. Stops are one screen apart in the screen's own directions (the map is
    drawn at an angle, so a square grid of tiles would leave gaps)."""
    h, w = frame_shape[:2]
    A = np.array(axes, float)
    # the part of the screen that shows map: not under the side panels, the troop list,
    # the top bar or the chat (the same area `_on_screen` counts as looked at)
    vis_w, vis_h = (0.88 - 0.14) * w, (0.84 - 0.12) * h
    a = A @ np.array([vis_w * overlap, 0.0])      # one view to the right, in tiles
    b = A @ np.array([0.0, vis_h * overlap])      # one view down, in tiles
    step = min(np.linalg.norm(a), np.linalg.norm(b))
    n = int(math.ceil(radius / max(step, 1e-6))) + 1
    reach = radius + max(np.linalg.norm(a), np.linalg.norm(b)) / 2
    c = np.array(centre, float)
    cells = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            p = c + i * a + j * b
            if np.linalg.norm(p - c) <= reach:
                # a spiral: ring by ring outward, going round each ring in order, so every
                # stop is next to the previous one (sorting by distance alone made it hop
                # from one side of his city to the other)
                ring = max(abs(i), abs(j))
                ang = (math.atan2(j, i) + 2 * math.pi) % (2 * math.pi)
                cells.append((ring, ang, (int(round(p[0])), int(round(p[1])))))
    # within each ring, always the nearest stop not done yet (the circle leaves gaps in
    # the outer ring, so going round by angle alone could still jump)
    out, last = [], tuple(centre)
    for ring in sorted({r for r, _, _ in cells}):
        todo = list(dict.fromkeys(q for r, _, q in sorted(cells) if r == ring and q not in out))
        while todo:
            nxt = min(todo, key=lambda q: math.dist(q, last))
            todo.remove(nxt)
            out.append(nxt)
            last = nxt
    return out


# ---------------------------------------------------------------- what we know about him

@dataclass
class ArmySeen:
    tile: tuple                 # map coordinates
    moving: bool
    ends: tuple | None          # map coordinates of both ends of its march line
    seen: float                 # time
    owner: str | None = None    # "him" when his name was read right there
    kind: str = "army"          # "army", "scout" (plain diamond) or "farm" (gathering)
    id: int = 0
    checks: int = 0
    info: dict = field(default_factory=dict)   # farm details read from its box (when checked)
    unsure: bool = False        # near the screen edge with no line: may be marching (lines
                                # aren't drawn that far from the middle of the view)


DEFAULT_SPEED = 4.0       # tiles a minute: a guess for a march seen only once


def _speed(a):
    """Tiles a minute, from its last two sightings (None if it can't tell yet)."""
    if len(a.history) < 2:
        return None
    (t0, p0), (t1, p1) = a.history[-2], a.history[-1]
    if t1 - t0 < 3:
        return None
    return math.dist(p0, p1) / ((t1 - t0) / 60)


def same_spot(a, b) -> bool:
    """Two sightings in one round of the same thing: close together (marching ones may
    have moved on a few tiles between the two looks)."""
    lim = 8.0 if (a.moving or b.moving or getattr(a, "unsure", False) or getattr(b, "unsure", False)) else 4.0
    return a.kind == b.kind and math.dist(a.tile, b.tile) <= lim


def _r(x):
    return round(x, 1) if x is not None else None


def predict(a, now):
    """Where a marching army probably is now: along its march line, toward where the
    line goes, at the speed it was seen going - never past the end of the line."""
    if not a.moving:
        return a.tile
    t1, p1 = (a.history[-1] if a.history else (a.last_seen, a.tile))
    dt = min(max(0.0, now - t1), 300) / 60
    target = a.dest
    if target is None and a.ends:                 # no direction yet: the far end of its line
        target = max(a.ends, key=lambda e: math.dist(e, p1))
    if target is not None:
        d = math.dist(p1, target)
        if d < 0.5:
            return target
        go = min(d, (_speed(a) or DEFAULT_SPEED) * dt)
        return (p1[0] + (target[0] - p1[0]) * go / d, p1[1] + (target[1] - p1[1]) * go / d)
    if len(a.history) >= 2:                       # no line: keep going the way it went
        (t0, p0), _ = a.history[-2], a.history[-1]
        if t1 - t0 > 0:
            k = min(now - t1, 120) / (t1 - t0)
            return (p1[0] + (p1[0] - p0[0]) * k, p1[1] + (p1[1] - p0[1]) * k)
    return p1


@dataclass
class Army:
    id: int
    tile: tuple
    first_seen: float
    last_seen: float
    moving: bool = False
    ends: tuple | None = None
    direction: str | None = None   # "out" (leaving his city / heading somewhere) or "home"
    dest: tuple | None = None
    history: list = field(default_factory=list)
    alerted: set = field(default_factory=set)
    owner: str | None = None       # "him", "other", or None (not checked yet)
    kind: str = "army"             # "army", "scout" or "farm" (gathering at a resource point)
    prev_tile: tuple | None = None # where it was last sweep
    checks: int = 0                # how many times we zoomed in to read its name
    info: dict = field(default_factory=dict)   # farm details read from its box (what, reserves, xy)
    missed: int = 0                # looked right at its spot and didn't see it (times in a row)
    unsure: bool = False           # seen only near the screen edge without a line so far


class Tracker:
    """Keeps his armies across sweeps and says what changed.

    One look can't tell which way an army walks along its line, so the
    direction comes from how it moved since the last sweep."""

    def __init__(self, city, my_sites, near_city=6.0, match_dist=12.0, near_site=8.0,
                 approach=20.0):
        self.city = tuple(city)
        self.my_sites = my_sites          # [(name, (x, y))]
        self.near_city = near_city
        self.match_dist = match_dist
        self.near_site = near_site
        self.approach = approach          # "moving close" distance (tiles)
        self.armies: list[Army] = []
        self._next = 1
        self.sweeps = 0
        self._last_away = None
        self._last_home_count = 0

    def at_home(self, tile):
        return math.dist(tile, self.city) <= self.near_city

    def _site_near(self, tile):
        """Your spot closest to tile, if within near_site tiles."""
        best = min(self.my_sites, key=lambda sp: math.dist(tile, sp[1]), default=None)
        if best and math.dist(tile, best[1]) <= self.near_site:
            return best[0]
        return None

    def _direction(self, a: Army, prev_tile, prev_moving, s: ArmySeen):
        """Work out which way a moving army is going, and where to."""
        e0, e1 = s.ends
        city_end = next((e for e in (e0, e1) if self.at_home(e)), None)
        if city_end is not None:
            other = e1 if city_end is e0 else e0
            if prev_tile is not None:
                d_now, d_before = math.dist(s.tile, self.city), math.dist(prev_tile, self.city)
                if not prev_moving and not self.at_home(prev_tile):
                    return "home", city_end           # was parked out there, now on the way in
                if not prev_moving and self.at_home(prev_tile):
                    return "out", other               # was at his city, now leaving
                if d_now < d_before - 0.5:
                    return "home", city_end
                if d_now > d_before + 0.5:
                    return "out", other
            return a.direction, (city_end if a.direction == "home" else other)
        # a line that doesn't touch his city: the army heads for the end it moved toward
        if prev_tile is not None and math.dist(prev_tile, s.tile) > 0.5:
            v = np.subtract(s.tile, prev_tile)
            dest = max((e0, e1), key=lambda e: float(np.subtract(e, s.tile) @ v))
            return "out", dest
        return a.direction, a.dest

    def update(self, sightings: list[ArmySeen], now: float) -> list[tuple[str, str, dict]]:
        """Fold one sweep's sightings in. Returns events: (kind, message, details)."""
        self.match(sightings, now)
        return self.events()

    def match(self, sightings: list[ArmySeen], now: float, covered=None):
        """Pair this sweep's sightings with known armies (by position).
        covered: [(x, y, half_w, half_h)] map areas actually looked at this time (None =
        everywhere). Armies outside those areas are kept as they were (not lost)."""
        self.sweeps += 1
        unmatched = list(self.armies)
        current = []
        for s in sorted(sightings, key=lambda s: s.tile):
            same = [a for a in unmatched if a.kind == getattr(s, "kind", "army")]
            # compare with where each one should be by now (marching ones move on)
            def where(a):
                if a.kind == "farm" and a.info and a.info.get("xy"):
                    # its exact X:Y or where the zoomed-out view put it, whichever is closer
                    xy = tuple(a.info["xy"])
                    return xy if math.dist(xy, s.tile) < math.dist(a.tile, s.tile) else a.tile
                return predict(a, now)
            best = min(same, key=lambda a: math.dist(where(a), s.tile), default=None)
            if getattr(s, "kind", "army") == "farm":
                # farms don't move; the zoomed-out estimate drifts a few tiles with where the
                # camera is, so allow that (two farms seen together still pair up correctly)
                limit = 5.0
            elif best is not None and best.moving:
                # the longer since it was seen, the further it may be off the prediction
                limit = self.match_dist + 0.5 * (_speed(best) or DEFAULT_SPEED) * (now - best.last_seen) / 60
            else:
                limit = self.match_dist
            if best and math.dist(where(best), s.tile) <= limit:
                unmatched.remove(best)
                a, prev_tile, prev_moving = best, best.tile, best.moving
            else:
                a = Army(self._next, s.tile, now, now)
                self._next += 1
                prev_tile, prev_moving = None, False
                if getattr(s, "owner", None):
                    a.owner = s.owner
            a.prev_tile = prev_tile
            a.missed = 0
            if getattr(s, "unsure", False) and not s.moving and best is not None and a.moving:
                # near the edge without its line: still the march we knew - keep its line
                a.tile, a.last_seen = s.tile, now
                a.history.append((now, s.tile))
                if a.owner is None and getattr(s, "owner", None):
                    a.owner = s.owner
                a.checks = max(a.checks, getattr(s, "checks", 0))
                current.append(a)
                continue
            a.unsure = bool(getattr(s, "unsure", False)) and not s.moving
            a.tile, a.last_seen, a.moving, a.ends = s.tile, now, s.moving, s.ends
            a.kind = getattr(s, "kind", "army")
            if a.owner is None and getattr(s, "owner", None):
                a.owner = s.owner
            a.checks = max(a.checks, getattr(s, "checks", 0))
            if getattr(s, "info", None):
                a.info = {**(a.info or {}), **s.info}
            a.history.append((now, s.tile))
            if s.moving and s.ends:
                a.direction, a.dest = self._direction(a, prev_tile, prev_moving, s)
            else:
                a.direction, a.dest = None, None
            current.append(a)
        if covered is not None:
            def looked_at(a):
                p = predict(a, now)
                return any(c(p) if callable(c) else (abs(p[0] - c[0]) < c[2] and abs(p[1] - c[1]) < c[3])
                           for c in covered)
            gone = []
            for a in unmatched:
                if looked_at(a):
                    a.missed += 1
                    # something that stays put (a farm, a parked army) is gone only after its
                    # spot was in view twice without it (one look can miss); a marching one
                    # simply isn't there any more
                    # stays put: gone after two looks without it; marching: also searched
                    # once more further along its line before giving up on it
                    # (other players' armies: no second look, we don't follow them)
                    (gone if a.missed >= 2 or a.owner == "other" else current).append(a)
                elif now - a.last_seen < 900:
                    current.append(a)
            self.gone = gone
        else:
            gone = []
            for a in unmatched:
                a.missed += 1
                (gone if a.missed >= 2 else current).append(a)
            self.gone = gone
        self.armies = current

    def events(self, include=None) -> list[tuple[str, str, dict]]:
        """What changed, counting only the armies `include` accepts (default: all)."""
        include = include or (lambda a: True)
        his = [a for a in self.armies if include(a)]
        events = []
        for a in his:
            events += self._scout_events(a) if a.kind == "scout" else self._army_events(a)
            if a.kind == "hostile" and ("hostile",) not in a.alerted:
                a.alerted.add(("hostile",))
                events.append(("hostile", "A red (hostile) army is marching against you or "
                                          "your alliance.", {"target": "you", "id": a.id}))
        for a in getattr(self, "gone", []):
            if a.kind == "farm" and include(a) and a.owner == "him":
                events.append(("farm_left", f"He stopped gathering at {a.tile[0]:.0f},{a.tile[1]:.0f}.",
                               {"id": a.id, "info": a.info, "tile": a.tile}))
        self.gone = []
        mine = [a for a in his if a.kind != "scout"]    # scouts don't count as troops
        def heading_home(a):
            if a.kind == "farm" and not a.moving:
                return False            # a farm stays put: its estimate just wobbles a tile or two
            if a.direction == "home":
                return True
            # its line may be missed on one look; the move itself also tells: it was
            # out in the field and is now clearly closer to his city
            if a.prev_tile is not None and not self.at_home(a.prev_tile):
                return (math.dist(a.tile, self.city) < math.dist(a.prev_tile, self.city) - 1.0
                        and a.direction != "out")
            return False
        home = [a for a in mine if heading_home(a)]
        if len(home) >= 2 and self._last_home_count < 2:
            events.append(("pulling_back", f"He's pulling troops back to his city "
                                           f"({len(home)} armies heading home).",
                           {"armies": len(home)}))
        self._last_home_count = len(home)
        away = [a for a in mine if not self.at_home(a.tile)]
        if self._last_away and self._last_away >= 2 and not away:
            events.append(("all_home", "All his armies are back home. He may be getting ready "
                                       "to attack (or going offline).", {}))
        self._last_away = len(away)
        return events

    def _army_events(self, a: Army):
        out = []
        if not a.moving or not a.ends:
            return out
        # A line reaching (or pointing at, when it runs off the screen) one of your
        # spots: tell you straight away, whichever way the army is walking.
        hit = next((self._site_near(e) for e in a.ends if self._site_near(e)), None)
        hit = hit or self._route_points_at(a.ends)
        if a.direction == "out" and a.dest is not None:
            hit = self._site_near(a.dest) or self.target_of(a) or hit
        if a.direction == "home":
            return out
        if hit:
            if ("at", hit) not in a.alerted:
                a.alerted.add(("at", hit))
                out.append(("army_out", f"He has an army on its way toward {hit}.",
                            {"target": hit, "id": a.id}))
            return out          # aimed at it: "moving close" would only repeat that
        # Not aimed at a spot, but the march ends close to one (moving in without
        # targeting yet - the game shows no warning for that)
        close = self._close_to(a)
        if close:
            if ("close", close[0]) not in a.alerted:
                a.alerted.add(("close", close[0]))
                near = self._close_to(a, every=True)
                names = ", ".join(f"{n} (~{d:.0f} tiles)" for n, d in near)
                out.append(("army_close", f"His army is moving close to {names}: its path passes "
                                          f"about {close[1]:.0f} tiles from the nearest.",
                            {"target": close[0], "id": a.id}))
            return out          # already told; "sent an army out" would only repeat it
        elif a.direction == "out" and "out" not in a.alerted and not hit:
            a.alerted.add("out")
            dest = f" (heading for about {a.dest[0]:.0f},{a.dest[1]:.0f})" if a.dest else ""
            out.append(("army_out", f"He sent an army out{dest}.", {"target": None, "id": a.id}))
        return out

    PAST_EDGE = 30.0          # tiles: how far a march line may run on past where the screen cut it

    def _close_to(self, a: Army, every=False):
        """(spot name, distance) when its march passes within `approach` tiles of one of
        your spots (and it isn't walking away home). Uses the closest point of its path,
        followed a little past the end we saw: a line running off the screen is cut
        there, which isn't where the army is going."""
        if not a.ends or a.direction == "home":
            return None
        e1, e2 = a.ends
        if self.at_home(e1) or self.at_home(e2):
            start, far = (e1, e2) if self.at_home(e1) else (e2, e1)
        else:                       # no end at his city: towards the end away from him
            start, far = sorted((e1, e2), key=lambda e: math.dist(e, self.city))
        length = math.dist(start, far)
        if length < 1:
            return None
        ux, uy = (far[0] - start[0]) / length, (far[1] - start[1]) / length
        reach = length + self.PAST_EDGE
        hits = []
        for name, pos in self.my_sites:
            t = (pos[0] - start[0]) * ux + (pos[1] - start[1]) * uy
            t = max(0.0, min(reach, t))
            d = math.dist(pos, (start[0] + ux * t, start[1] + uy * t))
            if d <= self.approach:
                hits.append((name, d))
        hits.sort(key=lambda h: h[1])
        if every:
            return hits                  # all of your spots it passes close to, nearest first
        return hits[0] if hits else None

    def _scout_events(self, a: Army):
        """His scout on a route to (or pointing at) one of your spots."""
        if not a.moving or not a.ends:
            return []
        hit = next((self._site_near(e) for e in a.ends if self._site_near(e)), None)
        hit = hit or self._route_points_at(a.ends)
        if a.direction == "out" and a.dest is not None:
            hit = self._site_near(a.dest) or self.target_of(a) or hit
        if a.direction == "home" or not hit or ("scout", hit) in a.alerted:
            return []
        a.alerted.add(("scout", hit))
        his = a.owner == "him" or (a.owner is None and a.ends and any(self.at_home(e) for e in a.ends))
        if his:
            return [("scout_at", f"His scout is on its way to {hit}. An attack often follows.",
                     {"target": hit, "id": a.id})]
        return [("scout_any", f"A scout (not confirmed his) is on its way to {hit}.",
                 {"target": hit, "id": a.id})]

    def _route_points_at(self, ends):
        """Name of your spot that a march line points at from his city's end
        (the line may be cut off by the screen edge before it gets there)."""
        e0, e1 = ends
        starts = [(e0, e1), (e1, e0)]
        for a_, b_ in starts:
            if not self.at_home(a_):
                continue                      # only routes leaving his city
            v = np.subtract(b_, a_)
            best, best_off = None, 15.0
            for name, pos in self.my_sites:
                u = np.subtract(pos, a_)
                nv, nu = np.linalg.norm(v), np.linalg.norm(u)
                if nv < 2 or nu < nv - 1:
                    continue
                off = math.degrees(math.acos(max(-1.0, min(1.0, float(v @ u) / (nv * nu)))))
                if off < best_off:             # the spot the line points at most directly
                    best, best_off = name, off
            if best:
                return best
        return None

    def target_of(self, a: Army):
        """Which of your spots the army's line points at, from where it is now."""
        if not a.dest:
            return None
        v = np.subtract(a.dest, a.tile)
        best, best_off = None, 15.0
        for name, pos in self.my_sites:
            u = np.subtract(pos, a.tile)
            if np.linalg.norm(v) and np.linalg.norm(u) and np.linalg.norm(u) <= np.linalg.norm(v) + 15:
                cos = float(v @ u / (np.linalg.norm(v) * np.linalg.norm(u)))
                off = math.degrees(math.acos(max(-1.0, min(1.0, cos))))
                if off < best_off:
                    best, best_off = name, off
        return best

    def threat(self, a: Army):
        """(spot name, how) when this army looks like it's coming for one of your spots:
        'aimed' (its march points at it) or 'close' (its march ends near it)."""
        if a.kind == "farm" or not a.moving or a.direction == "home":
            return None
        if a.ends:
            hit = next((self._site_near(e) for e in a.ends if self._site_near(e)), None)
            hit = hit or self._route_points_at(a.ends) or self.target_of(a)
            if hit:
                return hit, "aimed"
        close = self._close_to(a)
        return (close[0], "close") if close else None

    def summary(self, include=None):
        # his, plus ones not checked yet; ones checked without finding his name are left out
        include = include or (lambda a: a.owner == "him" or (a.owner is None and a.checks < 3))

        def state(a):
            if a.moving:
                return {"home": "going home", "out": "marching out"}.get(a.direction, "marching")
            if a.kind == "scout":
                return "scout"
            if a.kind == "farm":
                return "gathering"
            if a.unsure:
                return "moving? (near the edge)"
            return "at his city" if self.at_home(a.tile) else "parked / gathering"
        out = []
        for a in self.armies:
            if not include(a):
                continue
            th = self.threat(a)
            xy = a.info.get("xy") if a.info else None          # exact, read from its box
            out.append({"id": a.id, "x": xy[0] if xy else round(a.tile[0]),
                        "y": xy[1] if xy else round(a.tile[1]), "state": state(a),
                        "dest": [round(a.dest[0]), round(a.dest[1])] if a.dest else None,
                        "last_seen": datetime.fromtimestamp(a.last_seen).strftime("%H:%M:%S"),
                        "seen_ts": a.last_seen, "his": a.owner == "him", "kind": a.kind,
                        "checks": a.checks,
                        "threat": th[0] if th else None, "threat_how": th[1] if th else None,
                        "dist_home": round(math.dist(a.tile, self.city)),
                        # where it has been (not for farms: they stay put, the estimate wobbles)
                        "trail": [] if a.kind == "farm" and not a.moving else
                                 [[round(p[0]), round(p[1])] for _, p in a.history[-10:]],
                        "info": {k: v for k, v in (a.info or {}).items() if k != "readings"},
                        **self._farm_timing(a), **self._motion(a)})
        # the dangerous ones first, then his confirmed ones, nearest to you first
        out.sort(key=lambda r: (r["threat"] is None, not r["his"], r["dist_home"]))
        return out

    load = None                     # his armies' typical load (set by the hunter)

    def _farm_timing(self, a):
        from . import farms
        if a.kind != "farm" or not a.info:
            return {}
        now = time.time()
        rate = farms.rate_per_min(a.info.get("readings"))
        full = farms.full_amount(a.info.get("what", ""))
        return {"rate": round(rate) if rate else None, "full": full,
                "gathered": round(farms.gathered(a.info, now) or 0) or None,
                "gathering_min": _r(farms.minutes_gathering(a.info, now)),
                "runs_out_min": _r(farms.minutes_left(a.info, now)),
                "full_in_min": _r(farms.minutes_to_full(a.info, now, self.load))}

    @staticmethod
    def _motion(a):
        """Speed (tiles a minute) from its last moves, and minutes until it gets where
        its march line ends."""
        if not a.moving or len(a.history) < 2:
            return {"speed": None, "eta_min": None}
        (t0, p0), (t1, p1) = a.history[-2], a.history[-1]
        if t1 - t0 < 5:
            return {"speed": None, "eta_min": None}
        speed = math.dist(p0, p1) / ((t1 - t0) / 60)
        eta = math.dist(p1, a.dest) / speed if a.dest and speed > 0.05 else None
        return {"speed": round(speed, 1), "eta_min": round(eta, 1) if eta is not None else None}

    def map_info(self):
        """For the panel's little map: his city and your spots."""
        return {"city": [round(self.city[0]), round(self.city[1])],
                "mine": [{"name": n, "x": round(p[0]), "y": round(p[1])} for n, p in self.my_sites]}

# ---------------------------------------------------------------- the hunter

class Hunter:
    def __init__(self, game, cfg: dict, my_sites, log_path="his_armies.csv", debug_dir="debug"):
        h = cfg.get("hunt", {})
        self.game = game
        self.name = h.get("player_name", "")
        self.city = tuple(h.get("city", (0, 0)))
        self.radius = float(h.get("radius", 25))
        # "zoomed_out": sweep the simplified map (wide view, grey diamonds), zoom in only to
        # read who a diamond belongs to. "close": sweep at the close zoom reading names.
        self.method = h.get("method", "zoomed_out")
        self.axes_close = h.get("axes_close")
        self.axes_out = cfg.get("patrol", {}).get("axes")
        z = h.get("zoom")
        if isinstance(z, dict) and z.get("gap"):
            game.zoom_cfg = z
        self.progress = (0, 0, "")
        self.on_progress = None
        self.axes = self.axes_out if self.method == "zoomed_out" else self.axes_close
        self.checks_per_sweep = int(h.get("checks_per_sweep", 3))
        self.my_sites = my_sites
        self.tracker = Tracker(self.city, list(my_sites), h.get("near_city", 6),
                               h.get("match_distance", 12), h.get("near_site", 8),
                               h.get("moving_close", 20))
        self.fixed_sites = list(my_sites)
        self.my_troops = {}                       # troop list index -> what we know of it
        self.refresh_spots = float(h.get("track_my_gathering_minutes", 10)) * 60
        self._spots_at = 0.0
        self.log_path = Path(log_path)
        self.debug_dir = debug_dir
        self.auto_recall = bool(cfg.get("patrol", {}).get("auto_recall", False))
        self.seen_text: dict = {}     # army id -> text read when we zoomed in (for checking)
        self.warning = AttackAlertDetector()
        from .activity import ActivityLog
        self.activity = ActivityLog()
        # after the first sweep it only follows his armies (+ his city); a new sweep only
        # when one of them goes missing away from home, or as a safety check now and then
        self.full_sweep_every = float(h.get("full_sweep_minutes", 30)) * 60
        self._need_sweep = False
        from concurrent.futures import ThreadPoolExecutor
        self._pool = ThreadPoolExecutor(max_workers=1)
        self.timing = {"move": 0.0, "look": 0.0, "check": 0.0}
        self.last_round = None
        self._scale_checked = 0.0
        self._last_refresh = 0.0
        self._sweep_left: list = []        # sweep spots still to do (carried over between rounds)
        self._sweep_total = 0
        from .farms import FarmBook
        self.farms = FarmBook()
        self.focus: dict = {}          # army id -> {"follow", "alert_moves", "alert_home"} (panel)
        self._last_full = 0.0
        self._rounds = 0
        self.mode = ""                 # what the last round did: sweep / track / base
        self.dangers: set = set()
        self._recalled: set = set()
        self._last_attack_recall = 0.0
        self.warned = False
        self.toasts: list = []
        if not self.log_path.exists():
            self.log_path.write_text("time_utc,army,x,y,state,dest_x,dest_y\n")

    def ready(self):
        problems = []
        if not self.name:
            problems.append("enter his name first (step 2 in the panel)")
        if not self.axes:
            problems.append("set up the zoom first (step 3 in the panel)")
        return problems

    def read_view(self, view) -> list[ArmySeen]:
        """His armies on this screen, in map coordinates."""
        frame = view.frame
        if not view.camera:
            return []
        cam = view.camera[1:3]
        labels = [lb for lb in screen.find_player(frame, view.texts, self.name) if lb.kind == "army"]
        if not labels:
            return []
        lines = [ln for ln in screen.find_march_lines(frame) if ln.color != "green"]
        H = frame.shape[0]
        out = []
        for lb in labels:
            body = (lb.pos[0], lb.pos[1] + int(0.03 * H))   # troops are just under the name
            tile = to_tile(cam, self.axes_close, body, frame.shape)
            # the name floats a little above (and to the side of) the troops: check a
            # few points under it for a march line
            probes = [(lb.pos[0], lb.pos[1] + int(k * H)) for k in (0.0, 0.03, 0.06)]

            def gap(ln):
                return min(ln.distance_to(q) for q in probes)
            line = min(lines, key=gap, default=None)
            moving = bool(line and gap(line) < 0.075 * H)
            ends = None
            if moving:
                ends = tuple(to_tile(cam, self.axes_close, p, frame.shape) for p in (line.p1, line.p2))
            out.append(ArmySeen(tile, moving, ends, time.time(), owner="him"))
        return out

    @staticmethod
    def detect(frame):
        """Everything on the zoomed-out map, in screen pixels (no coordinates needed):
        [(point, kind, march line or None)]."""
        H = frame.shape[0]
        lines = [ln for ln in screen.find_march_lines(frame) if ln.color != "green"]
        out = []
        W = frame.shape[1]
        for d, kind in screen.find_map_icons(frame):
            line = min(lines, key=lambda ln: ln.distance_to(d), default=None)
            moving = bool(line and line.distance_to(d) < 0.05 * H)  # the icon hides the first dots
            # the game only draws march lines near the middle of the view: an army near the
            # edge without a line may still be marching
            edge = not (0.22 * W < d[0] < 0.78 * W and 0.22 * H < d[1] < 0.78 * H)
            out.append((d, kind, line if moving else ("edge" if edge else None)))
        # someone not in your alliance gathering: red pickaxe badge over a resource point
        for bx, by in screen.find_gather_badges(frame):
            out.append(((bx, by + 0.035 * H), "farm", None))
        return out

    def to_map(self, found, cam, shape) -> list[ArmySeen]:
        now = time.time()
        out = []
        for d, kind, line in found:
            tile = to_tile(cam, self.axes_out, d, shape)
            unsure = line == "edge"
            line = None if unsure else line
            ends = tuple(to_tile(cam, self.axes_out, p, shape) for p in (line.p1, line.p2)) if line else None
            out.append(ArmySeen(tile, line is not None, ends, now, kind=kind, unsure=unsure))
        return out

    def read_view_out(self, view) -> list[ArmySeen]:
        """Armies on the zoomed-out map (grey diamonds), in map coordinates."""
        if not view.camera:
            return []
        return self.to_map(self.detect(view.frame), view.camera[1:3], view.frame.shape)

    def look_spot(self):
        """One look: a fresh picture; reading its coordinates and finding the armies on it
        run at the same time (neither waits for the other). Returns (view, sightings)."""
        t0 = time.time()
        frame = self.game.fresh_frame()            # as soon as the icons have loaded
        job = self._pool.submit(self.game.read_camera, frame)
        found = self.detect(frame)
        self._dangers_frame(frame)
        cam = job.result()
        self.timing["look"] += time.time() - t0
        from .trace import TRACE
        if TRACE.on:
            TRACE.event("look", camera=cam, seconds=round(time.time() - t0, 2),
                        found=[{"px": [int(d[0]), int(d[1])], "kind": k, "line": l is not None and l != "edge",
                                "edge": l == "edge"} for d, k, l in found])
        from .game import View
        view = View(frame, [], cam, True)
        try:
            self.game.b.last_view = view
        except AttributeError:
            pass
        if not cam:
            return view, []
        return view, self.to_map(found, cam[1:3], frame.shape)

    def identify(self, armies, limit=None) -> int:
        """Zoom in on armies we don't know the owner of and find out: a marching army
        shows its owner's name over it; a gathering one only in the resource point's
        box (tap it). His first (near his city or on a route to / from it)."""
        # scouts are gone before we could zoom in on them: not worth it
        todo = [a for a in armies if a.owner is None and a.checks < 3 and a.kind != "scout"]
        if not todo:
            return 0

        def likely(a):
            touches = a.ends and any(self.tracker.at_home(e) for e in a.ends)
            return (0 if touches else 1, math.dist(a.tile, self.city))
        todo.sort(key=likely)
        todo = todo[:limit or self.checks_per_sweep]
        if not self.zoom_close():
            log.warning("hunt: couldn't zoom in to read names")
            return 0
        done = 0
        # nearest-first order, so each next one is usually a short drag away
        order, here = [], tuple(self.game._cam or todo[0].tile)
        rest = list(todo)
        while rest:
            nxt = min(rest, key=lambda a: math.dist(a.tile, here))
            rest.remove(nxt)
            order.append(nxt)
            here = nxt.tile
        try:
            for i, a in enumerate(order):
                if a.owner is not None:            # already settled from the same frame
                    continue
                self._progress(i, len(order), f"Checking whose {'farm' if a.kind == 'farm' else 'army'} "
                                              f"is at {a.tile[0]:.0f},{a.tile[1]:.0f}")
                self._check_owner(a, others=[o for o in order if o is not a and o.owner is None])
                done += 1
        finally:
            self.zoom_out()
        return done

    def _axes_close(self):
        r = (self.game.zoom_cfg or {}).get("close_ratio")
        return (np.array(self.axes_out, float) * float(r)).tolist() if r and self.axes_out else None

    def _check_owner(self, a, others=()):
        a.checks += 1
        from .trace import TRACE
        TRACE.event("check_owner", kind=a.kind, tile=[round(a.tile[0], 1), round(a.tile[1], 1)])
        x, y = int(round(a.tile[0])), int(round(a.tile[1]))
        ac = self._axes_close()
        # zoomed in already: a nearby one is a drag away; far ones are typed in
        moved = self.game.pan_to(x, y, ac, max_drags=2) if ac else self.game.goto(x, y)
        if not moved:
            return
        if a.kind == "farm":
            self._identify_farm(a)
            # other unchecked farms in this same frame: tap them too, without moving
            if ac:
                self._farms_in_frame([o for o in others if o.kind == "farm"], ac)
        else:
            # close view: the army is a cluster of red troops with a small name above it
            # (read enlarged). Two looks a moment apart: 'his' on one clear read, 'not his'
            # only if both clearly read someone else.
            votes, read = [], []
            for attempt in range(2):
                f = self.game.frame()
                who, txt = screen.army_owner_at_close(f, self.name)
                read += txt
                if who == "him":
                    a.owner = "him"
                    break
                votes.append(who)
                if attempt == 0:
                    time.sleep(0.25)
            if a.owner is None and votes == ["other", "other"]:
                a.owner = "other"
            self.seen_text[getattr(a, "id", 0)] = read
        for attempt in range(0 if a.kind == "farm" or a.owner else 2):
            # not settled by the troops' name: the old way, any army label near the middle
            v = self.game.look_middle()
            H, W = v.frame.shape[:2]
            centre = (W / 2, H / 2)
            near = [lb for lb in screen.find_player(v.frame, v.texts, self.name)
                    if lb.kind == "army" and math.dist(lb.pos, centre) < 0.4 * H]
            self.seen_text.setdefault(getattr(a, "id", 0), [])
            self.seen_text[getattr(a, "id", 0)] = list(self.seen_text[getattr(a, "id", 0)]) + [
                (t.text, screen.label_kind(v.frame, t), round(math.dist(t.center, centre) / H, 2))
                for t in v.texts if math.dist(t.center, centre) < 0.4 * H]
            if near:
                a.owner = "him"
            elif self._other_army_label_near(v, centre, H):
                a.owner = "other"
            if a.owner:
                break
            time.sleep(0.2)
        log.info("hunt: %s at %.0f,%.0f is %s", "farm" if a.kind == "farm" else "army",
                 a.tile[0], a.tile[1], a.owner or "unclear")
        TRACE.event("owner", kind=a.kind, tile=[round(a.tile[0], 1), round(a.tile[1], 1)], owner=a.owner,
                    read=self.seen_text.get(getattr(a, "id", 0)))

    # ---- zoom: bounce between the searching zoom and the close zoom
    def zoom_out(self, anchor=False):
        return self.game.to_sweep()

    def zoom_close(self):
        return self.game.to_close()

    def _progress(self, done, total, doing):
        self.progress = (done, total, doing)
        if self.on_progress:
            try:
                self.on_progress(done, total, doing)
            except Exception:
                pass

    GATHER_RX = re.compile(r"gather|recolect|sammel|raccogl|r[ée]colt|coletor|toplay", re.I)

    def _identify_farm(self, a):
        """A gathering army shows no name over it: tap the resource point (only works
        at the close zoom) and read its box. His name anywhere in the box = his (works in
        any game language); the box's X:Y gives the exact spot."""
        for attempt in range(2):
            self.game.check_stop()
            f = self.game.frame()
            H, W = f.shape[:2]
            badges = [b for b in screen.find_gather_badges(f)
                      if math.dist(b, (W / 2, H / 2)) < 0.3 * H]     # the farm we jumped to
            if not badges:
                # never tap blindly: near his base the middle of the screen is his city
                time.sleep(0.15)
                continue
            bx, by = min(badges, key=lambda b: math.dist(b, (W / 2, H / 2)))
            if self._read_farm_box(a, (bx, int(by + 0.02 * H))):   # the camp just under its badge
                return
        log.info("hunt: couldn't open the resource point at %.0f,%.0f", a.tile[0], a.tile[1])
        self.game.close_popups()

    def _read_farm_box(self, a, tap) -> bool:
        """Tap at `tap`, read the resource point's box (owner, X:Y, what, reserves), close
        it. False if no box opened."""
        self.game.b.click(int(tap[0]), int(tap[1]))
        box = None
        for _ in range(16):                              # wait for the box (up to ~1 s)
            time.sleep(0.06)
            f = self.game.frame()
            box = screen.find_info_box(f)
            if box:
                break
        if not box:
            return False
        try:
            self._read_box_contents(a, box)
        finally:
            self.game.close_popups()                     # always closed (an open box blocks zooming)
        return True

    def _read_box_contents(self, a, box):
        time.sleep(0.12)                                 # let the text finish drawing
        f = self.game.frame()
        texts = screen.read_text(f, box)
        self.seen_text[getattr(a, "id", 0)] = [t.text for t in texts]
        mine = [t for t in texts if screen.name_score(t.text, self.name) >= 0.75]
        coords = next((screen.parse_coords(t.text) for t in texts if screen.parse_coords(t.text)), None)
        details = self._farm_details(texts)
        if mine:
            a.owner = "him"
        elif any(self.GATHER_RX.search(t.text) for t in texts):
            a.owner = "other"                            # someone else is gathering there
        if a.owner == "him":
            info = dict(a.info or {})
            info.update(details)
            if coords:
                info["xy"] = [int(coords[1]), int(coords[2])]
            if details.get("reserves") is not None:
                hist = info.setdefault("readings", [])
                if not hist and self._rounds > 1:
                    info["from_start"] = True       # appeared after the first sweep: just started
                hist.append([round(time.time()), details["reserves"]])
                del hist[:-6]
            a.info = info
            self.farms.learn(details)

    RESOURCE_RX = re.compile(r"(level|nivel|niveau|stufe)|camp|farm|quarry|mine|cropland|"
                             r"tala|campo|cantera|mina|cultivo", re.I)
    RESERVES_RX = re.compile(r"reserv|r[ée]serv|vorr", re.I)

    def _farm_details(self, texts):
        """What the resource point is (e.g. 'Level 6 Logging Camp') and its reserves."""
        out = {}
        what = [t.text.strip() for t in texts if self.RESOURCE_RX.search(t.text)
                and not self.GATHER_RX.search(t.text) and len(t.text.strip()) > 6]
        if what:
            out["what"] = max(what, key=len)
        lab = next((t for t in texts if self.RESERVES_RX.search(t.text)), None)
        if lab:
            row = [t for t in texts if t is not lab and abs(t.center[1] - lab.center[1]) < max(12, lab.box[3])]
            for t in row:
                digits = re.sub(r"[^0-9]", "", t.text)
                if len(digits) >= 3:
                    out["reserves"] = int(digits)
                    break
        return out

    def _refresh_one_farm(self, now, every=240):
        """Re-read one of his farms' reserves (the oldest reading, if a few minutes old),
        for his gathering rate. At most one a round, and not more than every 2 minutes."""
        if now - self._last_refresh < 120:
            return
        due = [a for a in self.tracker.armies if a.kind == "farm" and a.owner == "him"
               and now - ((a.info or {}).get("readings") or [[0]])[-1][0] > every]
        if not due:
            return
        a = min(due, key=lambda a: ((a.info or {}).get("readings") or [[0]])[-1][0])
        self._last_refresh = now
        t0 = time.time()
        self._progress(0, 1, f"Re-reading his farm at {a.tile[0]:.0f},{a.tile[1]:.0f}")
        try:
            if self.zoom_close():
                xy = (a.info or {}).get("xy") or a.tile
                if self.game.goto(int(round(xy[0])), int(round(xy[1]))):
                    self._identify_farm(a)
        finally:
            self.zoom_out()
            self.timing["check"] += time.time() - t0

    def _farms_in_frame(self, farms, axes_close):
        """Tap every red-pickaxe farm visible right now (close zoom) that matches one of
        `farms`, and read its box - no moving between them."""
        if not farms:
            return
        f = self.game.fresh_frame()
        H, W = f.shape[:2]
        cam = self.game.read_camera(f)
        if not cam:
            return
        for bx, by in screen.find_gather_badges(f):
            tap = (bx, int(by + 0.02 * H))
            tile = to_tile(cam[1:3], axes_close, tap, f.shape)
            cand = [o for o in farms if o.owner is None and math.dist(o.tile, tile) < 4]
            if not cand:
                continue
            o = min(cand, key=lambda o: math.dist(o.tile, tile))
            o.checks += 1
            self._read_farm_box(o, tap)

    MOVING_STATES = ("marching", "returning", "fighting", "stopped")

    def update_my_troops(self):
        """Where are your armies right now, and what are they doing? Tap each commander
        in the troop list on the right: the game jumps to it. Gathering ones give their
        exact X:Y (from the box); marching ones where the camera went. No map search.
        Their spots then count for 'heading for your troops' alerts."""
        try:
            panel = self.game.troops()
        except Exception as e:
            log.warning("hunt: couldn't read your troops: %s", e)
            return
        if not panel:
            return
        now = time.time()
        seen = {}
        for slot in panel[2]:
            if slot.state in ("scout", "idle"):
                continue                          # scouts, and commanders at home
            self.game.check_stop()
            spot = self.game.troop_spot(slot)
            t = self.my_troops.get(slot.index) or {"index": slot.index, "history": []}
            t.update(state=slot.state, checked=now)
            if spot:
                t.update(x=int(spot["xy"][0]), y=int(spot["xy"][1]), exact=spot["exact"])
                t["history"] = (t["history"] + [(now, (t["x"], t["y"]))])[-10:]
            seen[slot.index] = t
        self.my_troops = seen
        # back to the searching zoom (a jump can change the view)
        self.game._at = None
        self.zoom_out()
        spots = [(f"your troop {t['index'] + 1} ({t['state']} at {t['x']},{t['y']})", (t["x"], t["y"]))
                 for t in seen.values() if "x" in t]
        self.tracker.my_sites = self.fixed_sites + spots
        if seen:
            log.info("hunt: your troops: %s", "; ".join(
                f"{t['index'] + 1} {t['state']}" + (f" at {t['x']},{t['y']}" if "x" in t else "")
                for t in seen.values()))

    update_my_spots = update_my_troops            # (old name)

    def my_troops_summary(self):
        """For the panel: your troops, with speed for moving ones."""
        out = []
        for t in sorted(self.my_troops.values(), key=lambda t: t["index"]):
            r = {k: t.get(k) for k in ("index", "state", "x", "y", "exact")}
            r["checked"] = datetime.fromtimestamp(t["checked"]).strftime("%H:%M:%S") if t.get("checked") else None
            h = t.get("history") or []
            r["speed"] = None
            if t.get("state") in self.MOVING_STATES and len(h) >= 2 and h[-1][0] - h[-2][0] >= 10:
                (t0, p0), (t1, p1) = h[-2], h[-1]
                r["speed"] = round(math.dist(p0, p1) / ((t1 - t0) / 60), 1)
            r["trail"] = [list(p) for _, p in h] if t.get("state") in self.MOVING_STATES else []
            out.append(r)
        return out

    def _troops_due(self, now):
        if not self.refresh_spots:
            return False
        moving = any(t.get("state") in self.MOVING_STATES for t in self.my_troops.values())
        return now - self._spots_at >= (min(self.refresh_spots, 45) if moving else self.refresh_spots)

    def is_his(self, a) -> bool:
        """His for sure (name read), or not checked yet but on a route that starts or
        ends at his city (almost always his own army)."""
        if a.owner == "him":
            return True
        if a.owner is None and a.ends:
            return any(self.tracker.at_home(e) for e in a.ends)
        return False

    @staticmethod
    def _other_army_label_near(view, centre, H):
        for t in view.texts:
            txt = t.text.strip()
            # a player name: letters in it, not chat ("x: y"), not coordinates or numbers.
            # OCR often garbles the [TAG] brackets, so they aren't required.
            if (len(re.sub(r"[^A-Za-z]", "", txt)) >= 4 and ":" not in txt and "#" not in txt
                    and math.dist(t.center, centre) < 0.3 * H
                    and screen.label_kind(view.frame, t) == "army"):
                return True
        return False

    def sweep(self, stop=None):
        """Visit the sweep grid around his city. Carries on where it stopped last time
        (a problem at one spot doesn't make it start over). Returns
        (sightings, spots visited, what was on screen, finished)."""
        if self.ready():
            raise RuntimeError("; ".join(self.ready()))
        from .game import Stopped
        if not self._sweep_left:
            shape = self.game.frame().shape
            self._sweep_left = list(sweep_points(self.city, self.radius, self.axes, shape))
            self._sweep_total = len(self._sweep_left)
        seen: list[ArmySeen] = []
        covered = []
        visited = 0
        while self._sweep_left:
            if stop and stop():
                break
            pt = self._sweep_left[0]
            i = self._sweep_total - len(self._sweep_left)
            self._progress(i, self._sweep_total,
                           f"Searching around his city: spot {i + 1} of {self._sweep_total} ({pt[0]},{pt[1]})")
            try:
                t0 = time.time()
                moved = (self.game.pan_to(*pt, self.axes) if self.method == "zoomed_out"
                         else self.game.goto(*pt))
                self.timing["move"] += time.time() - t0
                if not moved:
                    log.warning("hunt: couldn't get to %s", pt)
                    self._sweep_left.pop(0)
                    continue
                if self.method == "zoomed_out":
                    view, sightings = self.look_spot()
                else:
                    view = self.game.look()
                    self._dangers(view)
                    sightings = self.read_view(view)
                visited += 1
                if view.camera:
                    covered.append(self._on_screen(view.camera[1:3], view.frame.shape))
                here = [s for s in sightings if not any(same_spot(s, o) for o in seen)]
                seen += here
                if self.method == "zoomed_out":
                    for s2, cov in self._check_new_here(here):
                        seen += [s for s in s2 if not any(same_spot(s, o) for o in seen)]
                        covered.append(cov)
            except Stopped:
                raise
            except Exception as e:
                log.warning("hunt: problem at %s (%s); skipping that spot", pt, e)
                try:
                    self.game.close_popups()
                    self.zoom_out()
                except Stopped:
                    raise
                except Exception:
                    pass
            self._sweep_left.pop(0)
        return seen, visited, covered, not self._sweep_left

    def _dangers(self, view):
        self._dangers_frame(view.frame)

    def _dangers_frame(self, frame):
        if self.warning.check(frame):
            self.warned = True
        self.dangers |= screen.danger_icons(frame)

    def plan(self, now):
        """sweep: search the whole area around his city (first time and every few minutes);
        track: follow the armies of his we know about (+ a look at his city);
        base: none of his armies out: keep an eye on his city."""
        if self._sweep_left:                # a search that was cut short: carry on with it
            return "sweep"
        if self.method != "zoomed_out" or not self._last_full or self._need_sweep or \
                now - self._last_full >= self.full_sweep_every:
            return "sweep"
        if any(a.owner != "other" and a.kind != "scout" for a in self.tracker.armies):
            return "track"
        return "base"

    def track_points(self, now, frame_shape):
        """Where to look: each tracked army's likely spot now, plus his city; spots close
        together are looked at in one go. Ordered so the camera moves the least."""
        vw, vh = view_size_tiles(self.axes, frame_shape)
        followed = [a for a in self.tracker.armies if (self.focus.get(a.id) or {}).get("follow")
                    and a.kind != "scout"]
        if followed and self._rounds % 3:
            # following some closely: just those (the rest every third round)
            pts = [predict(a, now) for a in followed]
        else:
            # his armies (scouts move too fast to follow), and always his city
            pts = [tuple(self.city)]
            for a in self.tracker.armies:
                if a.owner == "other" or a.kind == "scout" or (a.owner is None and a.checks >= 3):
                    continue                     # not his / couldn't be confirmed 3 times
                pts.append(predict(a, now))
                if a.moving and a.missed:
                    # not where we expected last time: also look further along its line
                    ahead = predict(a, now + 60)
                    end = a.dest or (max(a.ends, key=lambda e: math.dist(e, a.tile)) if a.ends else None)
                    pts += [ahead] + ([end] if end else [])
        merged = []
        for p in pts:
            if all(abs(p[0] - q[0]) > 0.3 * vw or abs(p[1] - q[1]) > 0.3 * vh for q in merged):
                merged.append(p)
        cam = self.game.camera_fast() or tuple(self.city)
        order = []
        while merged:
            nxt = min(merged, key=lambda q: math.dist(q, cam))
            merged.remove(nxt)
            order.append((int(round(nxt[0])), int(round(nxt[1]))))
            cam = nxt
        return order

    def visit(self, points, stop=None):
        """Look at a few spots (dragging the map between them). Returns (sightings, covered)."""
        seen, covered = [], []
        shape = None
        for i, pt in enumerate(points):
            if stop and stop():
                break
            self._progress(i, len(points), f"Following his armies: spot {i + 1} of {len(points)} ({pt[0]},{pt[1]})"
                           if self.mode == "track" else f"Watching his city ({pt[0]},{pt[1]})")
            t0 = time.time()
            ok = self.game.pan_to(*pt, self.axes)
            self.timing["move"] += time.time() - t0
            if not ok:
                continue
            view, sightings = self.look_spot()
            if not view.camera:
                continue
            shape = view.frame.shape
            covered.append(self._on_screen(view.camera[1:3], shape))
            here = [s for s in sightings if not any(same_spot(s, o) for o in seen)]
            seen += here
            for s2, cov in self._check_new_here(here):
                seen += [s for s in s2 if not any(same_spot(s, o) for o in seen)]
                covered.append(cov)
        return seen, covered

    def _on_screen(self, cam, shape):
        """Is a map tile visible in this view (and not under the side panels / chat)?"""
        Ainv = np.linalg.inv(np.array(self.axes, float))
        H, W = shape[:2]
        cx, cy = float(cam[0]), float(cam[1])

        def visible(tile):
            px, py = Ainv @ np.array([tile[0] - cx, tile[1] - cy])
            px, py = px + W / 2, py + H / 2
            return 0.14 * W < px < 0.88 * W and 0.12 * H < py < 0.84 * H
        return visible

    def _check_new_here(self, found, again=2):
        """Armies / farms just seen that aren't known yet: find out whose they are right
        away (zoom in, check, zoom back out), then look again where it is now and do the
        same for anything new on screen. Returns [(sightings, on-screen test)] of those
        extra looks."""
        extra = []
        new = self._unknown(found)
        while new:
            t0 = time.time()
            self.identify(new, limit=6)
            self.timing["check"] += time.time() - t0
            if again <= 0:
                break
            again -= 1
            view, seen2 = self.look_spot()        # what's on screen now (e.g. more farms)
            if not view.camera:
                break
            extra.append((seen2, self._on_screen(view.camera[1:3], view.frame.shape)))
            done = list(new)
            new = [s for s in self._unknown(seen2) if not any(same_spot(s, d) for d in done)]
        return extra

    def _unknown(self, found):
        """Of these sightings, the ones whose owner isn't known yet (and worth checking)."""
        known = [a for a in self.tracker.armies if a.owner]
        new = []
        for s in found:
            if math.dist(s.tile, self.city) < 3.5:  # his city itself: never clicked on
                continue
            # on a march line that starts or ends at his city: his (leaving or coming home) -
            # no need to zoom in for that
            if s.moving and s.ends and any(self.tracker.at_home(e) for e in s.ends):
                s.owner = "him"
                continue
            if s.kind == "scout":
                continue
            k = min((a for a in known if a.kind == s.kind),
                    key=lambda a: math.dist(predict(a, time.time()), s.tile), default=None)
            near = 2.5 if s.kind == "farm" else 8      # farms: only the very same spot
            if k and math.dist(predict(k, time.time()), s.tile) < near:
                s.owner = k.owner
                continue
            new.append(s)
        return new

    def round(self, stop=None) -> list[Alert]:
        now = time.time()
        self.warned = False
        self.toasts = []
        self.dangers = set()
        self.timing = {"move": 0.0, "look": 0.0, "check": 0.0}
        if self.method == "zoomed_out":
            if self.game._at != "sweep":
                self._progress(0, 1, "Getting to the searching zoom")
                self.zoom_out()
            if self._rounds == 0:
                # starting: go straight to his city (not looking around wherever the camera
                # happened to be); the zoom check can wait for the next few minutes
                self._progress(0, 1, "Going to his city")
                self.game.goto(int(self.city[0]), int(self.city[1]))
                self._scale_checked = now
            # every few minutes: is the zoom still right? (measured, corrected by a notch)
            if self.axes and now - self._scale_checked > 300:
                self._scale_checked = now
                ss = float(np.linalg.norm(np.array(self.axes, float)[:, 0]))
                r = self.game.keep_scale(ss)
                if r and not 0.89 <= r <= 1.12:
                    log.warning("hunt: zoom is off (%.2fx the searching zoom) and couldn't be fixed", r)
        self._rounds += 1
        self.mode = self.plan(now)
        from .trace import TRACE
        TRACE.event("round", mode=self.mode, n=self._rounds,
                    armies=[{"id": a.id, "kind": a.kind, "owner": a.owner, "tile": [round(a.tile[0]), round(a.tile[1])],
                             "moving": a.moving, "missed": a.missed} for a in self.tracker.armies])
        covered = None
        if self.mode == "sweep":
            sightings, visited, covered, finished = self.sweep(stop)
            if finished:
                self._last_full = now
                covered = None             # the whole area was looked at
        else:
            frame = self.game.frame()
            pts = self.track_points(now, frame.shape) if self.mode == "track" else [tuple(self.city)]
            sightings, covered = self.visit(pts, stop)
            visited = len(covered)
        # where my troops are gathering (after the search, so it gets going at once)
        if self._troops_due(now):
            self._spots_at = now
            self._progress(0, 1, "Checking your troops (tapping each in the troop list)")
            self.update_my_troops()
        if self.method == "zoomed_out":
            self.tracker.match(sightings, now, covered)
            lost = [a for a in getattr(self.tracker, "gone", [])
                    if a.owner == "him" and a.kind == "army" and a.moving and a.direction != "home"
                    and not self.tracker.at_home(a.tile)]
            if self.mode == "sweep":
                self._need_sweep = False
            elif lost:
                log.info("hunt: lost track of %d of his armies; searching the area again", len(lost))
                self._need_sweep = True
            if self.mode == "sweep" or any(a.owner is None for a in self.tracker.armies):
                self.identify(self.tracker.armies)
            # his armies; plus any scout not known to be someone else's (heading for you
            # is worth knowing, and scouts are too quick to zoom in on)
            events = [e for e in self.tracker.events(self.is_his)
                      if e[0] not in ("scout_at", "scout_any")]   # scouting: only the game's red alert
        else:
            events = self.tracker.update(sightings, now)
        self._log()
        changes = []
        try:
            changes = self.activity.round(self.tracker.summary(), now) or []
            if "attacked" in self.dangers or "scouted" in self.dangers:
                self.activity.action("scouted_you" if "scouted" in self.dangers else "attacked_you", now=now)
        except Exception:
            log.debug("activity log failed", exc_info=True)
        alerts = []
        for aid, before, after in changes:                 # alerts you asked for on one army
            f = self.focus.get(aid) or {}
            if f.get("alert_moves") and before and after and before != after:
                alerts.append(Alert(f"army-{aid}-{now}", f"His army #{aid} changed",
                                    f"Was {before}, now {after}.", urgent=False, cooldown=False))
            elif f.get("alert_moves") and after is None:
                alerts.append(Alert(f"army-{aid}-{now}", f"His army #{aid} is gone",
                                    f"It was {before}; it's no longer where it was.", cooldown=False))
            if f.get("alert_home") and after in ("going home", "at his city") and before != after:
                alerts.append(Alert(f"home-{aid}-{now}", f"His army #{aid} is heading home",
                                    f"Was {before}, now {after}.", cooldown=False))
        if "attacked" in self.dangers:
            alerts.append(Alert("danger-attacked", "YOU'RE UNDER ATTACK",
                                "The red sword is showing in the game: an attack is coming at you.",
                                urgent=True))
        if "scouted" in self.dangers:
            alerts.append(Alert("danger-scouted", "YOU'RE BEING SCOUTED",
                                "The red eye is showing in the game: a scout is coming at you.",
                                urgent=True))
        if "attacked" in self.dangers and self.auto_recall and now - self._last_attack_recall > 120:
            self._last_attack_recall = now
            report = self.game.recall_all()
            alerts.append(Alert(f"recall-{now}", "Auto-recall (under attack)", "; ".join(report),
                                urgent=True, cooldown=False))
        if self.warned and not self.dangers:
            alerts.append(Alert("warning", "YOU'RE BEING SCOUTED OR ATTACKED",
                                "The red warning is flashing in the game.", urgent=True))
        for kind, text in dict(self.toasts).items():
            title = {"scouted": "YOU'VE BEEN SCOUTED", "attacked": "YOU'RE UNDER ATTACK",
                     "rallied": "A RALLY IS COMING AT YOU"}[kind]
            alerts.append(Alert(f"toast-{kind}", title, f"The game says: {text}", urgent=True))
        from . import farms as _farms
        self.tracker.load = self.farms.typical_load()
        for kind, msg, info in events:
            if kind == "farm_left":
                # finished (army full / point empty) or pulled back early (he's online)?
                fi = info.get("info") or {}
                est = _farms.minutes_to_full(fi, now, self.tracker.load)
                if est is None:
                    est = _farms.minutes_left(fi, now)
                self.farms.farm_ended(fi, bool(fi.get("from_start")), now)
                if est is not None and est > 5:
                    msg += f" He pulled it back early (about {est:.0f} min of gathering left): he's online."
                    self.activity.action("recalled_farm", "pulled a farmer back early", *info.get("tile", (None, None)), now=now)
                elif est is not None:
                    msg += " It had finished: his army is on its way home."
                else:
                    msg += " His army is heading home."
            urgent = kind in ("army_out", "scout_at", "scout_any", "hostile", "army_close") and bool(info.get("target"))
            title = {"army_out": "HE SENT AN ARMY OUT" + (" AT YOU" if urgent else ""),
                     "army_close": "HIS ARMY IS MOVING CLOSE TO YOU",
                     "hostile": "HIS ARMY IS MARCHING AGAINST YOU",
                     "scout_at": "HE'S SCOUTING YOU",
                     "scout_any": "A SCOUT IS HEADING FOR YOU",
                     "pulling_back": "He's pulling his troops back",
                     "all_home": "All his troops are home",
                     "farm_left": "His farmer is heading home"}[kind]
            alerts.append(Alert(f"hunt-{kind}-{now}-{info.get('id', '')}", title, msg,
                                urgent=urgent, cooldown=False))
            if urgent and kind in ("army_out", "hostile", "army_close") and self.auto_recall \
                    and f"recalled-{info.get('id')}" not in self._recalled:
                self._recalled.add(f"recalled-{info.get('id')}")
                pos = dict(self.my_sites).get(info["target"])
                report = self.game.recall_all([pos] if pos else None)
                alerts.append(Alert(f"recall-{now}", "Auto-recall", "; ".join(report),
                                    urgent=True, cooldown=False))
        for a in self.tracker.armies:
            if a.kind != "farm" or a.owner != "him" or not a.info:
                continue
            est = _farms.minutes_to_full(a.info, now, self.tracker.load)
            what = "his army is full and heads home"
            if est is None:
                est, what = _farms.minutes_left(a.info, now), "the resource point runs out"
            for lim in (10, 5):
                if est is not None and est <= lim and ("soon", lim) not in a.alerted:
                    a.alerted.add(("soon", lim))
                    xy = a.info.get("xy") or [round(a.tile[0]), round(a.tile[1])]
                    alerts.append(Alert(f"soon-{a.id}-{lim}", f"His farmer leaves in about {max(1, round(est))} min",
                                        f"At {xy[0]},{xy[1]}: {what} in about {est:.0f} min.", cooldown=False))
        if self.mode != "sweep" and not self.timing["check"]:
            self._refresh_one_farm(now)
        his = [a for a in self.tracker.armies if self.is_his(a) or (self.method == "close")]
        t = self.timing
        log.info("Round (%s): %d spots, %d armies seen (%d his) in %.1f s - moving %.1f s, "
                 "looking %.1f s, checking owners %.1f s", self.mode, visited, len(sightings), len(his),
                 time.time() - now, t["move"], t["look"], t["check"])
        self.last_round = {"mode": self.mode, "spots": visited, "seconds": round(time.time() - now, 1),
                           **{k: round(v, 1) for k, v in t.items()}}
        return alerts

    def _log(self):
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self.log_path.open("a", newline="") as f:
            w = csv.writer(f)
            for a in self.tracker.summary():
                d = a["dest"] or ["", ""]
                w.writerow([stamp, a["id"], a["x"], a["y"], a["state"], d[0], d[1]])
