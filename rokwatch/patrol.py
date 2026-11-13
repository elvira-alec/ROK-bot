"""Patrol: visit each site in turn, look for marches, log activity, alert, recall.

After a coordinate jump the site is in the middle of the screen, so a march
that starts or ends at the site is a dotted line with one end at the centre.
"""

from __future__ import annotations

import csv
import logging
import math
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np

from . import screen
from .detectors import AttackAlertDetector
from .game import Game, coords_in_chat
from .watcher import Alert

log = logging.getLogger("rokwatch")


@dataclass
class Site:
    name: str
    x: int
    y: int
    kind: str  # "enemy" or "mine"

    @classmethod
    def from_cfg(cls, d):
        kind = str(d.get("kind", "mine")).lower()
        return cls(d.get("name") or f"{kind} {d['x']},{d['y']}", int(d["x"]), int(d["y"]),
                   "enemy" if kind.startswith("enem") else "mine")


@dataclass
class Sighting:
    site: Site
    line: screen.MarchLine
    far_end: tuple          # screen point of the end away from the site
    heading: float          # degrees on screen, from the site toward the far end
    toward: str | None = None   # name of one of your sites it points at (if known)
    army_at: float | None = None  # how far the army icon is from the site (screen heights)


@dataclass
class Track:
    """One march line seen at a site, followed across visits."""
    heading: float
    first: float
    last: float
    army_at: float | None = None
    direction: str | None = None   # "leaving" / "returning" (or "coming" / "going" at your spots)


class ActivityLog:
    FIELDS = ["time_utc", "site", "kind", "event", "detail"]

    def __init__(self, path="activity.csv"):
        self.path = Path(path)
        if not self.path.exists():
            with self.path.open("w", newline="") as f:
                csv.writer(f).writerow(self.FIELDS)

    def add(self, site: str, kind: str, event: str, detail: str = ""):
        with self.path.open("a", newline="") as f:
            csv.writer(f).writerow([datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                                    site, kind, event, detail])


def activity_report(path="activity.csv") -> str:
    """When was he active? Counts his march sightings per hour of the day (UTC)."""
    p = Path(path)
    if not p.exists():
        return "No activity log yet."
    rows = list(csv.DictReader(p.open()))
    his = [r for r in rows if r["kind"] == "enemy" and r["event"] == "march"]
    if not his:
        return f"{len(rows)} log entries, no marches seen at his city yet."
    hours = Counter(int(r["time_utc"][11:13]) for r in his)
    days = sorted({r["time_utc"][:10] for r in his})
    top = max(hours.values())
    out = his[-1]
    lines = [f"His march sightings: {len(his)} over {len(days)} day(s).",
             f"Last seen: {out['time_utc']} UTC", "", "Hour (UTC)  activity"]
    for h in sorted(hours):  # only the hours he was seen
        n = hours[h]
        lines.append(f"  {h:02d}:00   {'#' * max(1, round(20 * n / top)):<20} {n}")
    quiet = [h for h in range(24) if h not in hours]
    if quiet and len(days) >= 3:
        lines.append(f"Never seen at: {', '.join(f'{h:02d}' for h in quiet)} (UTC hours)")
    busiest = ", ".join(f"{h:02d}:00" for h, _ in hours.most_common(3))
    lines.append(f"Most active around: {busiest} UTC")
    return "\n".join(lines)


def _heading(a, b):
    return math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) % 360


def _hdiff(a, b):
    return abs((a - b + 180) % 360 - 180)


@dataclass
class Patrol:
    game: Game
    sites: list[Site]
    cfg: dict = field(default_factory=dict)
    log_path: str = "activity.csv"
    debug_dir: str = "debug"

    def __post_init__(self):
        p = self.cfg
        self.radius = float(p.get("site_radius", 0.09))
        self.auto_recall = bool(p.get("auto_recall", False))
        self.recall_scope = p.get("recall_scope", "threatened")
        self.repeat = float(p.get("same_march_minutes", 10)) * 60
        self.looks = int(p.get("looks_per_site", 2))
        self.axes = np.array(p["axes"], float) if p.get("axes") else None  # tiles per pixel
        self.activity = ActivityLog(self.log_path)
        a = p.get("attack_alert") or {}
        # the red pulsing border: same for being scouted and being attacked
        self.warning = AttackAlertDetector(red_fraction=a.get("red_fraction", 0.10),
                                           side_min=a.get("side_min", 0.08),
                                           min_jump=a.get("min_jump", 0.08))
        self._seen: dict[str, list] = {}       # chat coords already logged
        self._tracks: dict[str, list] = {}     # site -> [Track]
        self._troops = None
        self._last_recall = 0.0

    # ------------------------------------------------------------ helpers
    def _new(self, key, heading, now):
        seen = [(h, t) for h, t in self._seen.get(key, []) if now - t < self.repeat]
        fresh = not any(_hdiff(h, heading) < 15 for h, _ in seen)
        if fresh:
            seen.append((heading, now))
        self._seen[key] = seen
        return fresh

    def _track(self, key, s: "Sighting", now):
        """Find (or start) the track for this line. Returns (track, is_new)."""
        tracks = [t for t in self._tracks.get(key, []) if now - t.last < self.repeat]
        self._tracks[key] = tracks
        for t in tracks:
            if _hdiff(t.heading, s.heading) < 15:
                t.last = now
                return t, False
        t = Track(s.heading, now, now, s.army_at)
        tracks.append(t)
        return t, True

    def _direction(self, site, track: "Track", s: "Sighting"):
        """Compare where the army icon is now with last visit. Returns the newly
        worked-out direction (once), else None."""
        if s.army_at is None:
            return None
        before, track.army_at = track.army_at, s.army_at
        if before is None or track.direction or abs(s.army_at - before) < 0.015:
            return None
        away = s.army_at > before
        if site.kind == "enemy":
            track.direction = "leaving" if away else "returning"
        else:
            track.direction = "going" if away else "coming"
        return track.direction

    def _toward(self, site: Site, heading_deg: float):
        """Which of your sites does a march leaving `site` in this screen direction point at?
        Needs the screen->map axes (learned with --learn-axes)."""
        if self.axes is None:
            return None
        v = np.array([math.cos(math.radians(heading_deg)), math.sin(math.radians(heading_deg))])
        world = self.axes @ v
        best, best_off = None, 25.0
        for s in self.sites:
            if s.kind != "mine":
                continue
            d = np.array([s.x - site.x, s.y - site.y], float)
            if not d.any():
                continue
            cos = float(world @ d / (np.linalg.norm(world) * np.linalg.norm(d)))
            off = math.degrees(math.acos(max(-1, min(1, cos))))
            if off < best_off:
                best, best_off = s.name, off
        return best

    def _snapshot(self, frame, tag, lines=()):
        Path(self.debug_dir).mkdir(parents=True, exist_ok=True)
        img = frame.copy()
        h, w = img.shape[:2]
        cv2.circle(img, (w // 2, h // 2), int(self.radius * h), (0, 255, 255), 3)
        for ln in lines:
            cv2.line(img, ln.p1, ln.p2, (255, 0, 255), 5)
        path = Path(self.debug_dir) / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{tag}.png"
        cv2.imwrite(str(path), img)
        return str(path)

    # ------------------------------------------------------------ one site
    def sightings(self, site: Site, frames) -> list[Sighting]:
        """March lines with an end at the middle of the screen (= the site)."""
        out = []
        for frame in frames:
            h, w = frame.shape[:2]
            centre = (w / 2, h / 2)
            armies = screen.find_army_icons(frame)
            for ln in screen.find_march_lines(frame):
                if ln.color == "green":
                    # Yours or your alliance's: going to your own spots, or you
                    # attacking him. Never his march.
                    continue
                if not ln.end_near(centre, self.radius * h):
                    continue
                near = ln.p1 if math.dist(ln.p1, centre) < math.dist(ln.p2, centre) else ln.p2
                far = ln.p2 if near == ln.p1 else ln.p1
                hd = _heading(centre, far)
                if any(_hdiff(hd, o.heading) < 10 for o in out):
                    continue
                out.append(Sighting(site, ln, far, hd, self._toward(site, hd),
                                    self._army_along(ln, near, far, armies, h)))
        return out

    @staticmethod
    def _army_along(ln, near, far, armies, h):
        """How far the army icon on this line is from the site, in screen heights. The
        line is cut where the icon sits (it covers it), so an icon just past the line's
        end counts too. Distance from the site, not a share of the line: the drawn line
        changes length as the army moves."""
        a, b = np.array(near, float), np.array(far, float)
        best = None                                    # (how far off the line, distance)
        for c in armies:
            c = np.array(c, float)
            off = min(ln.distance_to(tuple(c)), float(np.linalg.norm(c - b)))
            if off > 0.03 * h and float(np.linalg.norm(c - b)) > 0.07 * h:
                continue
            if best is None or off < best[0]:
                best = (off, float(np.linalg.norm(c - a)) / h)
        return best[1] if best else None

    def visit(self, site: Site, now=None) -> list[Alert]:
        now = time.time() if now is None else now
        alerts: list[Alert] = []
        if not self.game.goto(site.x, site.y):
            self.activity.add(site.name, site.kind, "goto_failed")
            return [Alert("goto", "RoK patrol can't move the map",
                          f"Couldn't jump to {site.name} ({site.x},{site.y}). "
                          "Is a menu open, or has the game disconnected?", urgent=True)]
        self.game.wait()
        frames = []
        view = None
        for i in range(self.looks):
            if i:
                self.game.wait(1.0)
            view = self.game.look()
            frames.append(view.frame)
            alerts += self._toasts_and_hostiles(site, view)
            if self.warning.check(view.frame):
                self.activity.add("you", "mine", "warning", "red border: scouted or attacked")
                alerts.append(Alert("warning", "YOU'RE BEING SCOUTED OR ATTACKED",
                                    "The red warning is flashing in the game. Check your city "
                                    "and troops!", urgent=True,
                                    image=self._snapshot(view.frame, "warning")))
        self._check_troops(view, alerts)
        for s in self.sightings(site, frames):
            track, is_new = self._track(site.name, s, now)
            moved = self._direction(site, track, s)
            color = {"white": "", "red": "red (hostile) "}.get(s.line.color, "")
            img = None
            if site.kind == "enemy":
                where = f" toward {s.toward}" if s.toward else ""
                if is_new:
                    self.activity.add(site.name, "enemy", "march",
                                      f"{color}line heading {s.heading:.0f}deg{where}")
                    img = self._snapshot(view.frame, "enemy-march", [s.line])
                    if s.toward:
                        alerts.append(Alert(f"enemy-{now}-{s.heading:.0f}",
                                            "ENEMY MARCH HEADING YOUR WAY",
                                            f"{site.name}: march line toward {s.toward}.",
                                            urgent=True, image=img, cooldown=False))
                        alerts += self._maybe_recall(s.toward)
                    else:
                        alerts.append(Alert(f"enemy-{now}-{s.heading:.0f}",
                                            "He has troops moving",
                                            f"{color}march line at {site.name}. Checking if "
                                            f"it's leaving or coming back.", image=img,
                                            cooldown=False))
                if moved:
                    self.activity.add(site.name, "enemy", moved, f"heading {s.heading:.0f}deg{where}")
                    if moved == "leaving":
                        img = img or self._snapshot(view.frame, "enemy-leaving", [s.line])
                        alerts.append(Alert(f"leave-{now}-{s.heading:.0f}",
                                            "He sent troops OUT" + (f" toward {s.toward}" if s.toward else ""),
                                            f"The army is moving away from {site.name}.",
                                            urgent=bool(s.toward), image=img, cooldown=False))
            else:
                if is_new:
                    self.activity.add(site.name, "mine", "incoming",
                                      f"{color}line from heading {s.heading:.0f}deg")
                    img = self._snapshot(view.frame, "incoming", [s.line])
                    alerts.append(Alert(f"incoming-{site.name}-{now}", "MARCH COMING AT YOUR SPOT",
                                        f"A {color}march line ends at {site.name} "
                                        f"({site.x},{site.y}).", urgent=True, image=img,
                                        cooldown=False))
                    alerts += self._maybe_recall(site.name)
                if moved:
                    self.activity.add(site.name, "mine", moved, f"heading {s.heading:.0f}deg")
        for c in coords_in_chat(view.texts):
            if self._new(f"chat-{c}", 0, now):
                self.activity.add("chat", "info", "coords_posted", f"{c[0]},{c[1]}")
        return alerts

    def _toasts_and_hostiles(self, site, view) -> list[Alert]:
        """The game's own pop-ups ('You have been scouted') and red army diamonds
        (armies marching against you or your alliance)."""
        out = []
        for kind, text in screen.read_toasts(view.frame, view.texts):
            title = {"scouted": "YOU'VE BEEN SCOUTED", "attacked": "YOU'RE UNDER ATTACK",
                     "rallied": "A RALLY IS COMING AT YOU"}[kind]
            self.activity.add("you", "mine", f"toast_{kind}", text)
            out.append(Alert(f"toast-{kind}", title, f"The game says: {text}", urgent=True,
                             image=self._snapshot(view.frame, f"toast-{kind}")))
        hostiles = [c for c, k in screen.find_map_icons(view.frame) if k == "hostile"]
        if hostiles:
            self.activity.add(site.name, site.kind, "hostile_army", f"{len(hostiles)} on screen")
            out.append(Alert("hostile-army", "ENEMY ARMY MARCHING AT YOU",
                             f"{len(hostiles)} red (hostile) army marker(s) on the map near "
                             f"{site.name}.", urgent=True,
                             image=self._snapshot(view.frame, "hostile")))
        return out

    def _check_troops(self, view, alerts):
        panel = screen.troop_panel(view.frame, view.texts)
        if not panel:
            return
        used, total, slots = panel
        states = [s.state for s in slots if s.state != "scout"]
        before = self._troops or []
        fighting = states.count("fighting")
        if fighting > before.count("fighting"):
            self.activity.add("you", "mine", "troop_state", f"{fighting} troop(s) fighting")
            alerts.append(Alert("fighting", "YOUR TROOPS ARE FIGHTING",
                                f"{fighting} of your troops are in a battle right now.",
                                urgent=True, image=self._snapshot(view.frame, "fighting")))
        recalled_by_me = time.time() - self._last_recall < 180  # we just pulled it back
        if (self._troops is not None and not recalled_by_me
                and states.count("gathering") < self._troops.count("gathering")):
            self.activity.add("you", "mine", "troop_state",
                              f"gathering {self._troops.count('gathering')} -> "
                              f"{states.count('gathering')}")
            alerts.append(Alert("troops", "One of your troops stopped gathering",
                                "Finished, returning, or under attack. Check your troops."))
        self._troops = states

    def _maybe_recall(self, threatened: str) -> list[Alert]:
        if not self.auto_recall or time.time() - self._last_recall < 60:
            return []
        self._last_recall = time.time()
        near = None
        if self.recall_scope == "threatened":
            near = [(s.x, s.y) for s in self.sites if s.name == threatened]
        report = self.game.recall_all(near)
        self.activity.add("you", "mine", "auto_recall", "; ".join(report))
        return [Alert(f"recall-{time.time()}", "Auto-recall", "; ".join(report), urgent=True,
                      cooldown=False)]

    def round(self) -> list[Alert]:
        alerts = []
        for site in self.sites:
            try:
                alerts += self.visit(site)
            except Exception as e:  # one bad site shouldn't stop the patrol
                log.exception("visit %s failed", site.name)
                alerts.append(Alert("visit-error", "RoK patrol problem", f"{site.name}: {e}"))
        return alerts


def learn_axes(game: Game, px=300) -> list | None:
    """Work out how screen pixels map to map tiles by dragging the map a known
    amount and reading the coordinates. Returns a 2x2 matrix (tiles per pixel)."""
    v0 = game.look()
    if not v0.camera:
        return None
    H, W = v0.frame.shape[:2]
    cols = []
    for dx, dy in ((px, 0), (0, px)):
        start = game.look().camera
        # dragging the map right moves the camera left, so drag the opposite way
        game.b.drag(W // 2 + dx // 2, H // 2 + dy // 2, W // 2 - dx // 2, H // 2 - dy // 2)
        game.wait(1.0)
        end = game.look().camera
        game.b.drag(W // 2 - dx // 2, H // 2 - dy // 2, W // 2 + dx // 2, H // 2 + dy // 2)
        game.wait(1.0)
        if not (start and end):
            return None
        cols.append([(end[1] - start[1]) / px, (end[2] - start[2]) / px])
    m = np.array(cols).T  # columns: tiles per pixel for screen x, screen y
    if abs(np.linalg.det(m)) < 1e-6:
        return None
    return m.round(5).tolist()
