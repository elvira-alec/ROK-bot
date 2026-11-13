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

# Full amount in a resource point when nobody has gathered from it (from the user).
# Level 3 stone was given as 354,475; every other row is level 2 x1.5 / x2 / x3 / x4,
# so 354,375 is used.
FULL = {
    "stone": {2: 236_250, 3: 354_375, 4: 472_500, 5: 708_750, 6: 945_000},
    "wood":  {2: 315_000, 3: 472_500, 4: 630_000, 5: 945_000, 6: 1_260_000},
    "food":  {2: 315_000, 3: 472_500, 4: 630_000, 5: 945_000, 6: 1_260_000},
    "gold":  {2: 105_000, 3: 157_500, 4: 210_000, 5: 315_000, 6: 420_000},
    "gems":  {1: 10, 2: 20, 3: 30, 4: 40},
}
_KIND_RX = [
    ("gems", r"gem|gema"),
    ("food", r"crop|farm|food|cultivo|comida|granja|alimento"),
    ("wood", r"logging|wood|lumber|tala|madera|[áa]rbol"),
    ("stone", r"stone|quarry|cantera|piedra"),
    ("gold", r"gold|oro|mine|mina"),
]


def parse_kind(what: str):
    """'Level 6 Logging Camp' / 'Campo de Cultivo de Nivel 6' -> ('wood', 6) / ('food', 6)."""
    w = (what or "").lower()
    lv = re.search(r"(?:level|nivel|lv\.?|niveau|stufe)\s*(\d)|(\d)\s*(?:level|nivel)", w)
    level = int(lv.group(1) or lv.group(2)) if lv else None
    kind = next((k for k, rx in _KIND_RX if re.search(rx, w)), None)
    return kind, level


def full_amount(what: str):
    kind, level = parse_kind(what)
    return FULL.get(kind, {}).get(level)


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
        if len(rd) < 2 or not rate:
            return
        g = gathered({**info, "from_start": seen_from_start}, now)
        if g is None:
            return
        load = g
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
        """Full amount of this kind of point: the table, else the highest seen."""
        return full_amount(what) or self.cap.get(kind_key(what))


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


def gathered(info: dict, now: float):
    """How much he has taken from the point so far: full amount minus what's left (the
    point was full when he got there - points don't refill while someone's on them), or
    else since our first reading if we saw him start."""
    rd = (info or {}).get("readings") or []
    if not rd:
        return None
    t1, r1 = rd[-1]
    rate = rate_per_min(rd) or 0
    now_left = r1 - rate * max(0.0, now - t1) / 60
    full = full_amount(info.get("what", ""))
    if full and r1 <= full:
        return max(0.0, full - now_left)
    if info.get("from_start"):
        return max(0.0, rd[0][1] - now_left)
    return None


def minutes_gathering(info: dict, now: float):
    """How long he's been on this point, from what he's taken and his rate."""
    g, rate = gathered(info, now), rate_per_min((info or {}).get("readings"))
    return g / rate if g is not None and rate else None


def minutes_to_full(info: dict, now: float, load):
    """Minutes until his army is full (it then heads home): his typical load minus
    what it has gathered here, at his rate."""
    rate = rate_per_min((info or {}).get("readings"))
    g = gathered(info, now)
    if not rate or not load or g is None:
        return None
    return max(0.0, (load - g) / rate)
