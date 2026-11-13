"""When is he online? Logs everything his armies do (only he can do those things, so
each one means he was active) and when the bot was watching, then works out his
habits: busy hours, likely sleep, sessions, and when his farms are out while he's away.

Files (next to the program):
  his_activity.csv  time_utc_epoch, kind, x, y, detail     one row per thing he did
  his_watch.csv     minute_epoch, armies_out, farms_out      one row per minute watched
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

ACTIONS = Path("his_activity.csv")
WATCH = Path("his_watch.csv")


class ActivityLog:
    def __init__(self, actions=ACTIONS, watch=WATCH):
        self.actions, self.watch = Path(actions), Path(watch)
        self._last_minute = None
        self._prev = None            # {army id: state} from the last round

    def action(self, kind, detail="", x=None, y=None, now=None):
        now = now or time.time()
        new = not self.actions.exists()
        with self.actions.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "kind", "x", "y", "detail"])
            w.writerow([int(now), kind, "" if x is None else int(x), "" if y is None else int(y), detail])

    def watched(self, armies_out, farms_out, now=None):
        """Called every round: the bot was watching this minute."""
        minute = int((now or time.time()) // 60)
        if minute == self._last_minute:
            return
        self._last_minute = minute
        new = not self.watch.exists()
        with self.watch.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["minute", "armies_out", "farms_out"])
            w.writerow([minute, armies_out, farms_out])

    def round(self, summary, now=None):
        """Compare his armies with the last round and log what he did."""
        now = now or time.time()
        his = {a["id"]: a for a in summary if a.get("his")}
        cur = {i: a["state"] for i, a in his.items()}
        changes = []
        if self._prev is not None:
            for i, a in his.items():
                before = self._prev.get(i)
                if before != a["state"]:
                    changes.append((i, before, a["state"]))
                if before is None:
                    kind = "farm_started" if a["state"] == "gathering" else \
                        "scout_sent" if a["state"] == "scout" else "army_seen"
                    self.action(kind, a["state"], a["x"], a["y"], now)
                elif before != a["state"]:
                    self.action("changed", f"{before} -> {a['state']}", a["x"], a["y"], now)
            for i, st in self._prev.items():
                if i not in cur:
                    changes.append((i, st, None))
                    self.action("farm_ended" if st == "gathering" else "army_gone", st, now=now)
        self._prev = cur
        self.watched(sum(1 for a in his.values() if a["state"] != "at his city"),
                     sum(1 for a in his.values() if a["state"] == "gathering"), now)
        return changes


def _read(path):
    try:
        with Path(path).open(newline="") as f:
            return list(csv.DictReader(f))
    except OSError:
        return []


def data(days=14, actions=ACTIONS, watch=WATCH, now=None):
    """For the panel (it bins by your local time): his actions, and per hour how many
    minutes were watched and how many of those had his farms out."""
    now = now or time.time()
    since = now - days * 86400
    acts = []
    for r in _read(actions):
        try:
            t = int(r["time"])
        except (KeyError, ValueError):
            continue
        if t >= since:
            acts.append([t, r.get("kind", ""), r.get("detail", ""), r.get("x", ""), r.get("y", "")])
    hours = {}
    for r in _read(watch):
        try:
            m, farms = int(r["minute"]), int(r.get("farms_out") or 0)
        except (KeyError, ValueError):
            continue
        if m * 60 < since:
            continue
        h = m // 60
        w = hours.setdefault(h, [0, 0])
        w[0] += 1
        w[1] += 1 if farms else 0
    return {"actions": acts[-3000:], "watch": [[h * 3600, v[0], v[1]] for h, v in sorted(hours.items())],
            "now": now}
