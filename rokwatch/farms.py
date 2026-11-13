"""His farms: how fast he's gathering and how long until he's done.

The game shows a resource point's remaining reserves in its box. Read twice a few
minutes apart, that gives his gathering rate, and so roughly when the point runs out
(an upper bound: his army may fill up and leave sooner).

There's no reliable published table of reserves per level, so the bot learns the full
amount of each kind ("Level 6 Cropland" ...) from the highest reading it has seen, kept in
farm_capacity.json. With that it can also say how much he's gathered from a point.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BOOK = Path("farm_capacity.json")


def kind_key(what: str) -> str:
    """'Level 6 Logging Camp' / 'Campo de Cultivo de Nivel 6' -> a stable key."""
    w = re.sub(r"\s+", " ", (what or "")).strip().lower()
    return w


class FarmBook:
    def __init__(self, path=BOOK):
        self.path = Path(path)
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            data = {}
        self.cap = {k: v for k, v in data.items() if k != "_loads"}
        self.loads = list(data.get("_loads", []))   # how much his armies carried home

    def _save(self):
        try:
            self.path.write_text(json.dumps({**self.cap, "_loads": self.loads[-20:]}, indent=1))
        except OSError:
            pass

    def typical_load(self):
        if not self.loads:
            return None
        s = sorted(self.loads)
        return s[len(s) // 2]

    def farm_ended(self, info: dict, seen_from_start: bool, now: float):
        """One of his farms ended: if it was watched from the start, remember how much
        his army carried (its load)."""
        rd = (info or {}).get("readings") or []
        rate = rate_per_min(rd)
        if not seen_from_start or len(rd) < 2 or not rate:
            return
        load = rd[0][1] - rd[-1][1] + rate * max(0.0, now - rd[-1][0]) / 60
        if load > 0:
            self.loads.append(int(load))
            self._save()

    def learn(self, details: dict):
        k, r = kind_key(details.get("what", "")), details.get("reserves")
        if not k or not r:
            return
        if r > self.cap.get(k, 0):
            self.cap[k] = r
            self._save()

    def capacity(self, what):
        return self.cap.get(kind_key(what))


def rate_per_min(readings):
    """Reserves gone per minute, from the first and last reading that are at least a
    minute apart and going down. None if it can't tell yet."""
    if not readings or len(readings) < 2:
        return None
    (t0, r0), (t1, r1) = readings[0], readings[-1]
    if t1 - t0 < 60 or r1 >= r0:
        return None
    return (r0 - r1) / ((t1 - t0) / 60)


def minutes_left(info: dict, now: float):
    """Minutes until the point runs out at his rate (from the latest reading)."""
    rd = (info or {}).get("readings") or []
    rate = rate_per_min(rd)
    if not rate:
        return None
    t1, r1 = rd[-1]
    return max(0.0, r1 / rate - (now - t1) / 60)


def minutes_to_full(info: dict, now: float, load):
    """Minutes until his army is full (it then heads home), using his typical load and
    what it has gathered since we first read the point."""
    rd = (info or {}).get("readings") or []
    rate = rate_per_min(rd)
    if not rate or not load or not info.get("from_start"):
        return None
    t1, r1 = rd[-1]
    gathered = rd[0][1] - r1 + rate * (now - t1) / 60
    return max(0.0, (load - gathered) / rate)
