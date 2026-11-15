"""When is a player online? Logs everything the watched player's armies do (only they can
do those things, so each one means they were active) and when the bot was watching, then
works out their habits: busy hours, likely sleep, sessions, farms out while they're away.

Per player (the user, 2026-11-15: "make it by players"): every row says whose it is.

Files (next to the program):
  his_activity.csv  time, kind, x, y, detail, active, player   one row per thing they did
  his_watch.csv     minute, armies_out, farms_out, player       one row per minute watched
Rows from before players were recorded are all [P83L]JONH WICK's (the only target then).
"""

from __future__ import annotations

import csv
import time
from pathlib import Path

ACTIONS = Path("his_activity.csv")
WATCH = Path("his_watch.csv")
LEGACY_PLAYER = "[P83L]JONH WICK"           # whose the rows without a player are


def _with_player_column(path, header):
    """An older file without the 'player' column: add it (those rows are LEGACY_PLAYER's)."""
    path = Path(path)
    try:
        with path.open(newline="") as f:
            rows = list(csv.reader(f))
    except OSError:
        return
    if not rows or "player" in rows[0]:
        return
    out = [rows[0] + ["player"]] + [r + [LEGACY_PLAYER] for r in rows[1:] if r]
    try:
        with path.open("w", newline="") as f:
            csv.writer(f).writerows(out)
    except OSError:
        pass


class ActivityLog:
    def __init__(self, actions=ACTIONS, watch=WATCH, player=None):
        self.actions, self.watch = Path(actions), Path(watch)
        # player "" = no target (guard mode): nothing is logged. None (old callers): rows
        # without a player, read as LEGACY_PLAYER's.
        self.off = player == ""
        self.player = player or ""
        _with_player_column(self.actions, None)
        _with_player_column(self.watch, None)
        self._last_minute = None
        self._prev = None            # {army id: state} from the last round

    # what counts as him being online: things only a player does on purpose. Not his
    # farms gathering, nor an army walking home by itself after gathering.
    ACTIVE = {"farm_started", "scout_sent", "recalled_farm", "scouted_you", "attacked_you",
              "sent_out", "recalled"}

    def action(self, kind, detail="", x=None, y=None, now=None, active=None):
        if self.off:
            return
        now = now or time.time()
        if active is None:
            active = kind in self.ACTIVE
        new = not self.actions.exists()
        with self.actions.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["time", "kind", "x", "y", "detail", "active", "player"])
            w.writerow([int(now), kind, "" if x is None else int(x), "" if y is None else int(y), detail,
                        1 if active else 0, self.player])

    def watched(self, armies_out, farms_out, now=None):
        """Called every round: the bot was watching this minute."""
        if self.off:
            return
        minute = int((now or time.time()) // 60)
        if minute == self._last_minute:
            return
        self._last_minute = minute
        new = not self.watch.exists()
        with self.watch.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["minute", "armies_out", "farms_out", "player"])
            w.writerow([minute, armies_out, farms_out, self.player])

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
                    st = a["state"]
                    kind = "farm_started" if st == "gathering" else "scout_sent" if st == "scout" else \
                        "sent_out" if st in ("marching out", "marching") else "army_seen"
                    self.action(kind, st, a["x"], a["y"], now)
                elif before != a["state"]:
                    after = a["state"]
                    marching = ("marching out", "marching")
                    # sent somewhere new, or turned round mid-march: on purpose. Walking home
                    # after gathering happens by itself.
                    kind = "sent_out" if after == "marching out" else \
                        "recalled" if before in marching and after == "going home" else "changed"
                    self.action(kind, f"{before} -> {after}", a["x"], a["y"], now)
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


def data(days=14, actions=ACTIONS, watch=WATCH, now=None, player=None):
    """For the panel (it bins by your local time): one player's actions, and per hour how
    many minutes they were watched and how many of those had their farms out. Also the list
    of players there's anything on (most recent first). player None = the most recent one."""
    now = now or time.time()
    since = now - days * 86400
    _with_player_column(actions, None)
    _with_player_column(watch, None)
    rows_a, rows_w = _read(actions), _read(watch)
    last_seen = {}
    for r in rows_a:
        who = r.get("player") or LEGACY_PLAYER
        try:
            last_seen[who] = max(last_seen.get(who, 0), int(r["time"]))
        except (KeyError, ValueError):
            pass
    for r in rows_w:
        who = r.get("player") or LEGACY_PLAYER
        try:
            last_seen[who] = max(last_seen.get(who, 0), int(r["minute"]) * 60)
        except (KeyError, ValueError):
            pass
    players = [p for p, _ in sorted(last_seen.items(), key=lambda pt: -pt[1]) if p]
    if player is None:
        player = players[0] if players else ""
    acts = []
    for r in rows_a:
        if (r.get("player") or LEGACY_PLAYER) != player:
            continue
        try:
            t = int(r["time"])
        except (KeyError, ValueError):
            continue
        if t >= since:
            active = r.get("active")
            active = (r.get("kind") in ActivityLog.ACTIVE) if active in (None, "") else active == "1"
            acts.append([t, r.get("kind", ""), r.get("detail", ""), r.get("x", ""), r.get("y", ""),
                         1 if active else 0])
    hours = {}
    for r in rows_w:
        if (r.get("player") or LEGACY_PLAYER) != player:
            continue
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
            "now": now, "player": player, "players": players}
