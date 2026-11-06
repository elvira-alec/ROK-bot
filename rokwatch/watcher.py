"""Decides what to alert about, one screenshot at a time."""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .detectors import (AttackAlertDetector, EnemyMarchDetector, FrozenDetector,
                        MapShiftTracker, MarchMemory)


@dataclass
class Alert:
    key: str          # alerts with the same key respect the cooldown
    title: str
    message: str
    urgent: bool = False
    image: str | None = None
    cooldown: bool = True


def protect_points_from(e: dict):
    """protect_points: [{name, pos}] (new) or home: [x, y] (old configs)."""
    pts = []
    for i, p in enumerate(e.get("protect_points") or []):
        if isinstance(p, dict):
            pts.append((p.get("name") or f"spot {i + 1}", p["pos"]))
        else:
            pts.append((f"spot {i + 1}", p))
    if not pts and e.get("home") and list(e["home"]) != [0, 0]:
        pts.append(("your city", e["home"]))
    return pts


class Watcher:
    def __init__(self, cfg: dict, reference: np.ndarray | None = None, debug_dir="debug"):
        self.cfg = cfg
        self.debug_dir = Path(debug_dir)
        self.save_shots = cfg.get("save_alert_screenshots", True)

        a = cfg.get("attack_alert", {})
        self.attack = AttackAlertDetector(
            a.get("edge_width", 0.04), a.get("red_fraction", 0.10), a.get("confirm_frames", 2),
            a.get("template_dir"), a.get("template_threshold", 0.82),
            a.get("min_jump", 0.08), side_min=a.get("side_min", 0.08)) if a.get("enabled", True) else None

        e = cfg.get("enemy_watch", {})
        self.e = e
        self.base_enemy = np.array(e.get("enemy_city", [0, 0]), float)
        self.base_points = [(n, np.array(p, float)) for n, p in protect_points_from(e)]
        self.offset = np.zeros(2)
        self.march = None
        if e.get("enabled", True):
            self.march = EnemyMarchDetector(
                self.base_enemy, self.base_points, e.get("line_colors"),
                e.get("detect_any_new_line", True), e.get("change_threshold", 45),
                e.get("start_radius", 70), e.get("angle_tolerance_degrees", 35),
                e.get("arrive_radius", 60), e.get("min_line_length", 35))
        self.memory = MarchMemory(e.get("same_march_minutes", 10))

        m = cfg.get("map_tracking", {})
        self.track = None
        if m.get("enabled", True) and reference is not None and self.march:
            self.track = MapShiftTracker(reference)
        # "Lined up" means confidence near what's normal for your screen,
        # learned over the first checks (the map should be visible at start).
        self.min_conf = m.get("min_confidence", 0.08)
        self.lost_ratio = m.get("lost_ratio", 0.4)
        self._confs: list[float] = []
        self.lost_after = m.get("lost_after_checks", 20)
        self.follow_px = m.get("follow_threshold_px", 4)
        self._lost = 0
        self.last_confidence = None

        self.frozen = FrozenDetector(cfg.get("frozen_alert_minutes", 5))

    # positions after following the map
    @property
    def enemy(self):
        return self.base_enemy + self.offset

    @property
    def points(self):
        return [(n, p + self.offset) for n, p in self.base_points]

    def _snapshot(self, frame, tag, marches=()):
        if not self.save_shots:
            return None
        self.debug_dir.mkdir(parents=True, exist_ok=True)
        img = frame.copy()
        if self.march:
            cv2.circle(img, tuple(int(v) for v in self.enemy), 10, (0, 0, 255), 2)
            for name, p in self.points:
                pt = tuple(int(v) for v in p)
                cv2.circle(img, pt, 10, (0, 255, 0), 2)
                cv2.putText(img, name, (pt[0] + 12, pt[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.5,
                            (0, 255, 0), 1)
        for m in marches:
            cv2.arrowedLine(img, m.start, m.end, (0, 255, 255), 3, tipLength=0.15)
        path = self.debug_dir / f"{datetime.now():%Y%m%d-%H%M%S-%f}-{tag}.png"
        cv2.imwrite(str(path), img)
        return str(path)

    def _inside(self, p, frame):
        h, w = frame.shape[:2]
        return 0 <= p[0] < w and 0 <= p[1] < h

    def _follow_map(self, frame, alerts) -> bool:
        """Update positions if the map slid. Returns False if the map can't be seen."""
        if not self.track:
            return True
        dx, dy, conf = self.track.measure(frame)
        self.last_confidence = conf
        if len(self._confs) < 10 and conf >= self.min_conf:
            self._confs.append(conf)
        typical = float(np.median(self._confs)) if self._confs else 0.0
        if conf < max(self.min_conf, self.lost_ratio * typical):
            self._lost += 1
            if self._lost == self.lost_after:
                alerts.append(Alert("lost", "RoK watcher lost the map",
                                    "Can't line up the map with your setup. A menu may be open "
                                    "or the map was moved. Put it back where you set it up.",
                                    urgent=True, image=self._snapshot(frame, "lost")))
            return False
        if self._lost >= self.lost_after:
            alerts.append(Alert("found", "RoK watcher found the map again",
                                "Watching his city again."))
        self._lost = 0
        new = np.array([dx, dy])
        if np.linalg.norm(new - self.offset) > self.follow_px:
            self.offset = new
            self.march.reset(self.enemy, self.points)
            if not self._inside(self.enemy, frame):
                alerts.append(Alert("offscreen", "His city is off screen",
                                    "The map moved and his city isn't visible. "
                                    "Move the map back so the watcher can see him.",
                                    urgent=True, image=self._snapshot(frame, "offscreen")))
        return True

    def step(self, frame: np.ndarray, now: float | None = None) -> list[Alert]:
        now = time.time() if now is None else now
        alerts: list[Alert] = []

        if self.attack and (reason := self.attack.check(frame)):
            alerts.append(Alert("attack", "YOU ARE UNDER ATTACK",
                                f"{reason}. Pull your troops back!", urgent=True,
                                image=self._snapshot(frame, "attack")))

        if self.march and self._follow_map(frame, alerts):
            marches = self.march.check(frame)
            new = [m for m in marches if self.memory.is_new(m, now)]
            toward = [m for m in new if m.toward_home]
            other = [m for m in new if not m.toward_home]
            if toward:
                names = ", ".join(sorted({m.target for m in toward}))
                alerts.append(Alert(f"march-{now}", "ENEMY MARCH HEADING YOUR WAY",
                                    f"He sent troops toward {names}. Get ready / pull back!",
                                    urgent=True, image=self._snapshot(frame, "march", toward),
                                    cooldown=False))
            if other and self.e.get("report_other_marches", True):
                alerts.append(Alert(f"other-{now}", "Enemy sent a march",
                                    f"He sent {len(other)} march(es) out, not toward you.",
                                    image=self._snapshot(frame, "other", other), cooldown=False))

        if self.frozen.check(frame, now):
            alerts.append(Alert("frozen", "RoK watcher can't see the game",
                                "The game picture hasn't changed for a while. "
                                "Disconnected or logged out?", urgent=True))
        return alerts
