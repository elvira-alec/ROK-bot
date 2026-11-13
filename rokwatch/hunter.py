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
    a = A @ np.array([w * overlap, 0.0])      # one screen to the right, in tiles
    b = A @ np.array([0.0, h * overlap])      # one screen down, in tiles
    step = min(np.linalg.norm(a), np.linalg.norm(b))
    n = int(math.ceil(radius / max(step, 1e-6))) + 1
    reach = radius + max(np.linalg.norm(a), np.linalg.norm(b)) / 2
    c = np.array(centre, float)
    pts = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            p = c + i * a + j * b
            if np.linalg.norm(p - c) <= reach:
                pts.append((int(round(p[0])), int(round(p[1]))))
    pts = list(dict.fromkeys(pts))
    pts.sort(key=lambda q: (q[0] - centre[0]) ** 2 + (q[1] - centre[1]) ** 2)
    return pts


# ---------------------------------------------------------------- what we know about him

@dataclass
class ArmySeen:
    tile: tuple                 # map coordinates
    moving: bool
    ends: tuple | None          # map coordinates of both ends of its march line
    seen: float                 # time
    owner: str | None = None    # "him" when his name was read right there
    kind: str = "army"          # "army", "scout" (plain diamond) or "farm" (gathering)


def predict(a, now):
    """Where a moving army probably is now (from its last two sightings)."""
    if not a.moving or len(a.history) < 2:
        return a.tile
    (t0, p0), (t1, p1) = a.history[-2], a.history[-1]
    if t1 - t0 <= 0:
        return p1
    k = min(now - t1, 120) / (t1 - t0)
    return (p1[0] + (p1[0] - p0[0]) * k, p1[1] + (p1[1] - p0[1]) * k)


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
            best = min(same, key=lambda a: math.dist(a.tile, s.tile), default=None)
            if best and math.dist(best.tile, s.tile) <= self.match_dist:
                unmatched.remove(best)
                a, prev_tile, prev_moving = best, best.tile, best.moving
            else:
                a = Army(self._next, s.tile, now, now)
                self._next += 1
                prev_tile, prev_moving = None, False
                if getattr(s, "owner", None):
                    a.owner = s.owner
            a.prev_tile = prev_tile
            a.tile, a.last_seen, a.moving, a.ends = s.tile, now, s.moving, s.ends
            a.kind = getattr(s, "kind", "army")
            a.history.append((now, s.tile))
            if s.moving and s.ends:
                a.direction, a.dest = self._direction(a, prev_tile, prev_moving, s)
            else:
                a.direction, a.dest = None, None
            current.append(a)
        if covered is not None:
            def looked_at(a):
                p = predict(a, now)
                return any(abs(p[0] - x) < hw and abs(p[1] - y) < hh for x, y, hw, hh in covered)
            current += [a for a in unmatched if not looked_at(a) and now - a.last_seen < 900]
            self.gone = [a for a in unmatched if looked_at(a)]
        else:
            self.gone = list(unmatched)
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
                events.append(("farm_left", f"He stopped gathering at {a.tile[0]:.0f},{a.tile[1]:.0f} "
                                            "(pulled that army back or sent it somewhere).",
                               {"id": a.id}))
        self.gone = []
        mine = [a for a in his if a.kind != "scout"]    # scouts don't count as troops
        def heading_home(a):
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
                out.append(("army_close", f"His army is moving close to {close[0]} (its march "
                                          f"ends about {close[1]:.0f} tiles away).",
                            {"target": close[0], "id": a.id}))
            return out          # already told; "sent an army out" would only repeat it
        elif a.direction == "out" and "out" not in a.alerted and not hit:
            a.alerted.add("out")
            dest = f" (heading for about {a.dest[0]:.0f},{a.dest[1]:.0f})" if a.dest else ""
            out.append(("army_out", f"He sent an army out{dest}.", {"target": None, "id": a.id}))
        return out

    def _close_to(self, a: Army):
        """(spot name, distance) when one end of its march is within `approach` tiles of
        one of your spots (and it isn't walking away home)."""
        if not a.ends or a.direction == "home":
            return None
        best = None
        for e in a.ends:
            if self.at_home(e):
                continue
            for name, pos in self.my_sites:
                d = math.dist(e, pos)
                if d <= self.approach and (best is None or d < best[1]):
                    best = (name, d)
        return best

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
        return [("scout_at", f"His scout is on its way to {hit}. An attack often follows.",
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

    def summary(self, include=None):
        include = include or (lambda a: a.owner in (None, "him"))

        def state(a):
            if a.moving:
                return {"home": "going home", "out": "marching out"}.get(a.direction, "marching")
            if a.kind == "scout":
                return "scout"
            if a.kind == "farm":
                return "gathering"
            return "at his city" if self.at_home(a.tile) else "parked / gathering"
        return [{"id": a.id, "x": round(a.tile[0]), "y": round(a.tile[1]), "state": state(a),
                 "dest": [round(a.dest[0]), round(a.dest[1])] if a.dest else None,
                 "last_seen": datetime.fromtimestamp(a.last_seen).strftime("%H:%M:%S"),
                 "his": a.owner == "him", "kind": a.kind}
                for a in self.armies if include(a)]


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
        zn, cn = h.get("zoom_notches"), h.get("close_notches")
        self.zoom_notches = int(zn) if zn not in (None, "") else None
        self.close_notches = int(cn) if cn not in (None, "") else None
        self.progress = (0, 0, "")
        self.on_progress = None
        self.axes = self.axes_out if self.method == "zoomed_out" else self.axes_close
        self.checks_per_sweep = int(h.get("checks_per_sweep", 3))
        self.my_sites = my_sites
        self.tracker = Tracker(self.city, list(my_sites), h.get("near_city", 6),
                               h.get("match_distance", 12), h.get("near_site", 8),
                               h.get("moving_close", 20))
        self.fixed_sites = list(my_sites)
        self.refresh_spots = float(h.get("track_my_gathering_minutes", 10)) * 60
        self._spots_at = 0.0
        self.log_path = Path(log_path)
        self.debug_dir = debug_dir
        self.auto_recall = bool(cfg.get("patrol", {}).get("auto_recall", False))
        self.seen_text: dict = {}     # army id -> text read when we zoomed in (for checking)
        self.warning = AttackAlertDetector()
        self.full_sweep_every = float(h.get("full_sweep_minutes", 4)) * 60
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
            problems.append("set hunt.player_name (his name as it shows in game)")
        if not self.axes:
            problems.append("learn the map directions first (" +
                            ("at the zoomed-out patrol view" if self.method == "zoomed_out"
                             else "at the close zoom") + ")")
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

    def read_view_out(self, view) -> list[ArmySeen]:
        """Armies on the zoomed-out map (grey diamonds), in map coordinates."""
        frame = view.frame
        if not view.camera:
            return []
        cam = view.camera[1:3]
        H = frame.shape[0]
        lines = [ln for ln in screen.find_march_lines(frame) if ln.color != "green"]
        out = []
        for d, kind in screen.find_map_icons(frame):
            tile = to_tile(cam, self.axes_out, d, frame.shape)
            line = min(lines, key=lambda ln: ln.distance_to(d), default=None)
            moving = bool(line and line.distance_to(d) < 0.05 * H)  # the icon hides the first dots
            ends = (tuple(to_tile(cam, self.axes_out, p, frame.shape) for p in (line.p1, line.p2))
                    if moving else None)
            out.append(ArmySeen(tile, moving, ends, time.time(), kind=kind))
        # someone not in your alliance gathering: red pickaxe badge over a resource point
        for bx, by in screen.find_gather_badges(frame):
            tile = to_tile(cam, self.axes_out, (bx, by + 0.035 * H), frame.shape)
            out.append(ArmySeen(tile, False, None, time.time(), kind="farm"))
        return out

    def identify(self, armies) -> int:
        """Zoom in on armies we haven't identified yet and read the name over them.
        Most likely his first (on a route to/from his city, or near it)."""
        todo = [a for a in armies if a.owner is None and a.checks < 3]
        if not todo:
            return 0

        def likely(a):
            touches = a.ends and any(self.tracker.at_home(e) for e in a.ends)
            return (0 if touches else 1, math.dist(a.tile, self.city))
        todo.sort(key=likely)
        todo = todo[:self.checks_per_sweep]
        if not self.zoom_close():
            log.warning("hunt: couldn't zoom in to read names")
            return 0
        done = 0
        for i, a in enumerate(todo):
            a.checks += 1
            self._progress(i, len(todo), f"Zooming in on army #{a.id} to read the name")
            if not self.game.goto(int(round(a.tile[0])), int(round(a.tile[1]))):
                continue
            self.game.wait()
            if a.kind == "farm":
                self._identify_farm(a)
                done += 1
                continue
            for attempt in range(2):          # a name can be missed on one look
                v = self.game.look()
                H, W = v.frame.shape[:2]
                centre = (W / 2, H / 2)
                near = [lb for lb in screen.find_player(v.frame, v.texts, self.name)
                        if lb.kind == "army" and math.dist(lb.pos, centre) < 0.3 * H]
                self.seen_text[a.id] = [(t.text, screen.label_kind(v.frame, t),
                                         round(math.dist(t.center, centre) / H, 2))
                                        for t in v.texts if math.dist(t.center, centre) < 0.4 * H]
                if near:
                    a.owner = "him"
                elif self._other_army_label_near(v, centre, H):
                    a.owner = "other"
                if a.owner:
                    break
                self.game.wait(0.6)
            done += 1
            log.info("hunt: army #%d at %.0f,%.0f is %s", a.id, a.tile[0], a.tile[1],
                     a.owner or "unclear")
        self.zoom_out()
        return done

    # ---- zoom: a fixed number of wheel notches in from fully zoomed out, when set
    def zoom_out(self, anchor=False):
        if self.zoom_notches is not None:
            self.game.set_zoom(self.zoom_notches, anchor=anchor)
            return True
        return self.game.zoom_to_patrol_level()

    def zoom_close(self):
        if self.close_notches is not None:
            self.game.set_zoom(self.close_notches)
            return True
        return self.game.zoom_to_close()

    def _progress(self, done, total, doing):
        self.progress = (done, total, doing)
        if self.on_progress:
            try:
                self.on_progress(done, total, doing)
            except Exception:
                pass

    def _identify_farm(self, a):
        """A gathering army shows no name over it: tap the resource point (at the close
        zoom; it can't be tapped from further out) and read 'Gatherer' in its box."""
        for attempt in range(2):
            f = self.game.frame()
            H, W = f.shape[:2]
            badges = screen.find_gather_badges(f)
            if badges:
                bx, by = min(badges, key=lambda b: math.dist(b, (W / 2, H / 2)))
                tap = (bx, int(by + 0.045 * H))          # the camp just under its badge
            else:
                tap = (W // 2, H // 2)
            self.game.b.click(int(tap[0]), int(tap[1]))
            self.game.wait(0.8)
            v = self.game.look()
            name, coords = screen.read_gatherer(v.texts, H)
            self.seen_text[a.id] = [("gatherer", name, coords)]
            if name or coords:
                self.game.close_popups()
            if name:
                a.owner = "him" if screen.name_score(name, self.name) >= 0.75 else "other"
                if coords:
                    a.tile = (float(coords[0]), float(coords[1]))
                return
            if coords:                                   # a box, but nobody gathering
                a.owner = "other"
                return
        log.info("hunt: couldn't open the resource point at %.0f,%.0f", a.tile[0], a.tile[1])

    def update_my_spots(self):
        """Where are my gathering troops right now? Tap each gathering troop's portrait
        and read the resource point's coordinates from its info box (viewing only)."""
        try:
            panel = self.game.troops()
        except Exception as e:
            log.warning("hunt: couldn't read your troops: %s", e)
            return
        if not panel:
            return
        spots = []
        for slot in panel[2]:
            if slot.state != "gathering":
                continue
            loc = self.game.troop_location(slot)
            self.game.close_popups()
            if loc:
                spots.append((f"your troop {slot.index + 1} (gathering at {loc[0]},{loc[1]})", loc))
        self.tracker.my_sites = self.fixed_sites + spots
        if spots:
            log.info("hunt: your gathering troops are at %s", [s[1] for s in spots])

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

    def sweep(self, stop=None) -> tuple[list[ArmySeen], int]:
        """Visit every point of the sweep grid. Returns (sightings, points visited)."""
        if self.ready():
            raise RuntimeError("; ".join(self.ready()))
        v0 = self.game.look()
        seen: list[ArmySeen] = []
        visited = 0
        pts = list(sweep_points(self.city, self.radius, self.axes, v0.frame.shape))
        for i, pt in enumerate(pts):
            if stop and stop():
                break
            self._progress(i, len(pts), f"Sweeping around his city: spot {i + 1} of {len(pts)} ({pt[0]},{pt[1]})")
            # nearby spots: drag the map (quick); far ones: type the coordinates
            moved = (self.game.pan_to(*pt, self.axes) if self.method == "zoomed_out"
                     else self.game.goto(*pt))
            if not moved:
                log.warning("hunt: couldn't jump to %s", pt)
                continue
            visited += 1
            view = self.game.fast_view() if self.method == "zoomed_out" else self.game.look()
            self._dangers(view)
            for kind, text in screen.read_toasts(view.frame, view.texts):
                self.toasts.append((kind, text))
            reader = self.read_view_out if self.method == "zoomed_out" else self.read_view
            for s in reader(view):
                # the same army seen from two overlapping stops: keep one
                if all(math.dist(s.tile, o.tile) > 4 for o in seen):
                    seen.append(s)
        return seen, visited

    def _dangers(self, view):
        if self.warning.check(view.frame):
            self.warned = True
        self.dangers |= screen.danger_icons(view.frame)

    def plan(self, now):
        """sweep: search the whole area around his city (first time and every few minutes);
        track: follow the armies of his we know about (+ a look at his city);
        base: none of his armies out: keep an eye on his city."""
        if self.method != "zoomed_out" or not self._last_full or \
                now - self._last_full >= self.full_sweep_every:
            return "sweep"
        if any(a.owner != "other" for a in self.tracker.armies):
            return "track"
        return "base"

    def track_points(self, now, frame_shape):
        """Where to look: each tracked army's likely spot now, plus his city; spots close
        together are looked at in one go. Ordered so the camera moves the least."""
        vw, vh = view_size_tiles(self.axes, frame_shape)
        pts = [predict(a, now) for a in self.tracker.armies if a.owner != "other"]
        pts.append(tuple(self.city))
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
            if not self.game.pan_to(*pt, self.axes):
                continue
            view = self.game.fast_view()
            if not view.camera:
                continue
            shape = view.frame.shape
            self._dangers(view)
            vw, vh = view_size_tiles(self.axes, shape)
            covered.append((view.camera[1], view.camera[2], 0.45 * vw, 0.45 * vh))
            for s in self.read_view_out(view):
                if all(math.dist(s.tile, o.tile) > 4 for o in seen):
                    seen.append(s)
        return seen, covered

    def round(self, stop=None) -> list[Alert]:
        now = time.time()
        self.warned = False
        self.toasts = []
        self.dangers = set()
        if self.method == "zoomed_out":
            self._progress(0, 1, "Setting the zoom")
            self.zoom_out(anchor=self._rounds % 15 == 0)
        self._rounds += 1
        self.mode = self.plan(now)
        covered = None
        if self.mode == "sweep":
            sightings, visited = self.sweep(stop)
            self._last_full = now
        else:
            frame = self.game.frame()
            pts = self.track_points(now, frame.shape) if self.mode == "track" else [tuple(self.city)]
            sightings, covered = self.visit(pts, stop)
            visited = len(covered)
        # where my troops are gathering (after the search, so it gets going at once)
        if self.refresh_spots and now - self._spots_at >= self.refresh_spots:
            self._spots_at = now
            self._progress(0, 1, "Checking where your troops are gathering")
            self.update_my_spots()
        # pop-up messages (bottom right): read once per round, just that corner
        try:
            f = self.game.frame()
            H, W = f.shape[:2]
            texts = screen.read_text(f, (int(0.55 * W), int(0.72 * H), int(0.45 * W), int(0.28 * H)))
            self.toasts += screen.read_toasts(f, texts)
        except Exception:
            log.debug("toast check failed", exc_info=True)
        if self.method == "zoomed_out":
            self.tracker.match(sightings, now, covered)
            if self.mode == "sweep" or any(a.owner is None for a in self.tracker.armies):
                self.identify(self.tracker.armies)
            events = self.tracker.events(self.is_his)
        else:
            events = self.tracker.update(sightings, now)
        self._log()
        alerts = []
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
        for kind, msg, info in events:
            urgent = kind in ("army_out", "scout_at", "hostile", "army_close") and bool(info.get("target"))
            title = {"army_out": "HE SENT AN ARMY OUT" + (" AT YOU" if urgent else ""),
                     "army_close": "HIS ARMY IS MOVING CLOSE TO YOU",
                     "hostile": "HIS ARMY IS MARCHING AGAINST YOU",
                     "scout_at": "HE'S SCOUTING YOU",
                     "pulling_back": "He's pulling his troops back",
                     "all_home": "All his troops are home",
                     "farm_left": "He pulled a farmer back"}[kind]
            alerts.append(Alert(f"hunt-{kind}-{now}-{info.get('id', '')}", title, msg,
                                urgent=urgent, cooldown=False))
            if urgent and kind in ("army_out", "hostile", "army_close") and self.auto_recall \
                    and f"recalled-{info.get('id')}" not in self._recalled:
                self._recalled.add(f"recalled-{info.get('id')}")
                pos = dict(self.my_sites).get(info["target"])
                report = self.game.recall_all([pos] if pos else None)
                alerts.append(Alert(f"recall-{now}", "Auto-recall", "; ".join(report),
                                    urgent=True, cooldown=False))
        his = [a for a in self.tracker.armies if self.is_his(a) or (self.method == "close")]
        log.info("Round (%s): %d spots looked at, %d armies seen, %d of them his",
                 self.mode, visited, len(sightings), len(his))
        return alerts

    def _log(self):
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with self.log_path.open("a", newline="") as f:
            w = csv.writer(f)
            for a in self.tracker.summary():
                d = a["dest"] or ["", ""]
                w.writerow([stamp, a["id"], a["x"], a["y"], a["state"], d[0], d[1]])
