"""Runs the game browser and the patrol on one background thread.

The browser automation library has to be driven from the thread that started
it, so every game action goes through this worker's command queue.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import cv2

from . import config
from .notify import Notifier

log = logging.getLogger("rokwatch")


class MemoryLog(logging.Handler):
    """Keeps the last lines of the log for the control panel."""

    def __init__(self, n=300):
        super().__init__()
        self.lines = deque(maxlen=n)
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record):
        self.lines.append(self.format(record))


@dataclass
class Status:
    phase: str = "stopped"        # stopped | opening | ready | patrolling | busy | error
    message: str = "Not started"
    round: int = 0
    last_site: str = ""
    alerts: list = field(default_factory=list)   # recent alerts for the panel
    look: dict | None = None                     # result of the last "what do you see"


class Worker:
    def __init__(self, cfg_path=config.DEFAULT_PATH):
        self.cfg_path = cfg_path
        self.q: queue.Queue = queue.Queue()
        self.status = Status()
        self.stop_patrol = threading.Event()
        self.thread = None
        self.gb = None
        self.shot_path = "debug/panel_view.png"

    # ------------------------------------------------------------ from the panel
    def send(self, cmd, **kw):
        if not self.thread or not self.thread.is_alive():
            self.thread = threading.Thread(target=self._run, daemon=True)
            self.thread.start()
        if cmd == "stop":
            self.stop_patrol.set()
            if self.status.phase == "patrolling":
                self.status.message = "Stopping after the place it's checking now..."
        self.q.put((cmd, kw))

    # ------------------------------------------------------------ worker thread
    def _set(self, phase, message):
        self.status.phase, self.status.message = phase, message
        log.info(message)

    def _cfg(self):
        return config.load(self.cfg_path)

    def _alert(self, notifier, a):
        notifier.send(a.key, a.title, a.message, a.urgent, a.image, a.cooldown)
        self.status.alerts.insert(0, {"time": time.strftime("%H:%M:%S"), "title": a.title,
                                      "message": a.message, "urgent": a.urgent})
        del self.status.alerts[30:]

    def _run(self):
        while True:
            cmd, kw = self.q.get()
            try:
                if cmd == "quit":
                    self._close()
                    return
                getattr(self, f"_do_{cmd}")(**kw)
            except Exception as e:  # never let one failed action kill the worker
                log.debug("%s failed", cmd, exc_info=True)
                first = (str(e).strip().splitlines() or [type(e).__name__])[0].strip(" :")
                if cmd == "open" and ("Executable doesn't exist" in str(e) or "is not found" in str(e)):
                    first = "Couldn't open a browser. Install Google Chrome or Microsoft Edge."
                self._set("error" if not self.gb else "ready", f"{cmd} failed: {first[:200]}")

    def _close(self):
        if self.gb:
            try:
                self.gb.__exit__(None, None, None)
            finally:
                self.gb = None

    def _game(self):
        from .commands import make_game
        if not self.gb:
            raise RuntimeError("Open the game first")
        return make_game(self.gb, self._cfg())

    def _do_open(self):
        from .browser import GameBrowser
        if self.gb and self.gb.alive:
            self._set("ready", "Game window is already open")
            return
        self._close()
        self._set("opening", "Opening the game window...")
        self.gb = GameBrowser.from_config(self._cfg()).__enter__()
        self._set("ready", "Game window open. Log in, go to the map, close menus, "
                           "then press Start patrol.")

    def _area_warning(self) -> str:
        """A message if the game picture can't be found or doesn't fit, else ''."""
        try:
            found, clipped = self.gb.find_game_area()
        except Exception:
            return ""
        if found is None:
            return ("Can't find the game picture on the page (still loading?). "
                    "Using the whole window.")
        if clipped:
            return ("The game picture doesn't fit in the window: make the browser window "
                    "bigger (or maximise it), then try again.")
        return ""

    def _do_close(self):
        self._close()
        self._set("stopped", "Game window closed")

    def _do_look(self):
        game = self._game()
        warn = self._area_warning()
        self._set("busy", "Looking at the screen...")
        from .screen import find_army_icons, find_march_lines
        v = game.look()
        panel = game.troops(v)
        lines = find_march_lines(v.frame)
        shot = v.frame.copy()
        for ln in lines:
            cv2.line(shot, ln.p1, ln.p2, (255, 0, 255), 4)
        cv2.imwrite(self.shot_path, shot)
        self.status.look = {
            "camera": list(v.camera) if v.camera else None,
            "zoom": game.zoom(v),
            "troops": [s.state for s in panel[2]] if panel else None,
            "marches": [f"{ln.color} line, {ln.dots} dots" for ln in lines],
            "armies": len(find_army_icons(v.frame)),
            "time": time.strftime("%H:%M:%S"),
            "area": game.area,
        }
        self._set("ready", warn or "Here's what I see")

    def _do_goto(self, x, y):
        game = self._game()
        self._set("busy", f"Jumping to {x},{y}...")
        ok = game.goto(int(x), int(y))
        self._set("ready", f"Jumped to {x},{y}" if ok else
                  f"Couldn't jump to {x},{y} (camera at {game.look().camera})")

    def _do_axes(self):
        from .patrol import learn_axes
        game = self._game()
        self._set("busy", "Learning map directions (dragging the map a little)...")
        m = learn_axes(game)
        if m is None:
            self._set("ready", "Couldn't learn map directions. Is the coordinate readout visible?")
            return
        config.save_fields({"patrol.axes": m}, self.cfg_path)
        self._set("ready", "Learned the map directions. Patrol can now tell where marches go.")

    def _do_recall(self):
        game = self._game()
        self._set("busy", "Recalling gathering troops...")
        report = game.recall_all()
        self._set("ready", "Recall: " + "; ".join(report))

    def _do_test_alert(self):
        n = Notifier.from_config(self._cfg().get("notify", {}), background=False)
        if not n.has_target:
            self._set(self.status.phase, "Set your ntfy topic (or Discord / Telegram) first")
            return
        n.send(f"test-{time.time()}", "RoK watcher test", "If you see this, alerts work.",
               cooldown=False)
        self._set(self.status.phase, "Test alert sent. Check your phone.")

    def _do_stop(self):
        self.stop_patrol.clear()
        if self.status.phase == "patrolling":
            self._set("ready", "Patrol stopped")

    def _do_start(self, max_rounds=None):
        from .patrol import Patrol
        from .commands import sites_from
        cfg = self._cfg()
        game = self._game()
        p = cfg.get("patrol", {})
        patrol = Patrol(game, sites_from(cfg), p, p.get("activity_log", "activity.csv"))
        notifier = Notifier.from_config(cfg.get("notify", {}))
        self.stop_patrol.clear()
        if (warn := self._area_warning()):
            log.warning(warn)
        self._set("patrolling", "Getting the right zoom...")
        if p.get("zoom_out", True) and not game.zoom_to_patrol_level():
            log.warning("Couldn't get to the patrol zoom level; carrying on as it is")
        self._set("patrolling", "Patrolling " + ", ".join(s.name for s in patrol.sites))
        self.status.round = 0
        while not self.stop_patrol.is_set() and (max_rounds is None or self.status.round < max_rounds):
            for site in patrol.sites:
                if self.stop_patrol.is_set():
                    break
                self.status.last_site = site.name
                for a in patrol.visit(site):
                    self._alert(notifier, a)
            self.status.round += 1
            log.info("Round %d done", self.status.round)
            end = time.time() + float(p.get("pause_between_rounds", 5))
            while time.time() < end and not self.stop_patrol.is_set():
                time.sleep(0.2)
        self.stop_patrol.clear()
        self._set("ready", f"Patrol stopped after {self.status.round} round(s)")
