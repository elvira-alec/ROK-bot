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
from .game import Stopped
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
    armies: list = field(default_factory=list)   # his armies, from the last hunt sweep
    snaps: int = 0                               # snapshots saved this session
    progress: list | None = None                 # [done, total] of the current job
    doing: str = ""                              # what it's doing right now (short)


class Worker:
    def __init__(self, cfg_path=config.DEFAULT_PATH):
        self.cfg_path = cfg_path
        self.q: queue.Queue = queue.Queue()
        self.status = Status()
        self.stop_patrol = threading.Event()
        self.thread = None
        self.gb = None
        self.shot_path = "debug/panel_view.png"
        self._last_sample = 0.0
        self._snap_wanted = None

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
        if phase in ("ready", "stopped", "error"):
            self.status.progress, self.status.doing = None, ""
        elif phase == "busy":
            self.status.doing = message
        log.info(message)

    def _cfg(self):
        return config.load(self.cfg_path)

    def _alert(self, notifier, a):
        notifier.send(a.key, a.title, a.message, a.urgent, a.image, a.cooldown)
        self.status.alerts.insert(0, {"time": time.strftime("%H:%M:%S"), "title": a.title,
                                      "message": a.message, "urgent": a.urgent})
        del self.status.alerts[30:]
        if self._snap_cfg().get("on_alerts", True):        # the picture the alert came from
            self._auto_snap(f"alert {a.title}", {"alert": f"{a.title}: {a.message}"})

    # ------------------------------------------------------------ snapshots
    def _snap_cfg(self) -> dict:
        return self._cfg().get("snapshots") or {}

    def _auto_snap(self, tag, extra=None):
        v = getattr(self.gb, "last_view", None) if self.gb else None
        if v is None:
            return None
        try:
            from . import snapshots
            info = {**(extra or {}), **snapshots.describe(v)}
            name = snapshots.save(v.frame, tag, info, keep=int(self._snap_cfg().get("keep", 300)))
            self.status.snaps += 1
            return name
        except Exception:
            log.debug("snapshot failed", exc_info=True)
            return None

    def request_snap(self, note=""):
        """From the panel. While patrolling / hunting the worker is busy, so the picture
        is taken at its next step (no extra screenshot, no interruption)."""
        if self.status.phase == "patrolling":
            self._snap_wanted = note or ""
            self.status.message = "Snapshot will be taken at the next step..."
        else:
            self.send("snap", note=note)

    def _maybe_sample_quick(self):
        if self._snap_wanted is not None:
            self._maybe_sample()

    def _maybe_sample(self):
        """Between patrol / hunt steps: a requested snapshot, and every few minutes a
        sample picture (for improving the bot)."""
        if self._snap_wanted is not None:
            note, self._snap_wanted = self._snap_wanted, None
            name = self._auto_snap("manual " + note, {"note": note})
            self.status.message = f"Snapshot saved: {name}" if name else "Snapshot failed (no picture yet)"
        every = float(self._snap_cfg().get("every_minutes", 10) or 0)
        if every > 0 and time.time() - self._last_sample >= every * 60:
            self._last_sample = time.time()
            self._auto_snap("sample")

    def _do_snap(self, note=""):
        from . import snapshots
        game = self._game()
        self._set(self.status.phase if self.status.phase == "patrolling" else "busy",
                  "Taking a snapshot...")
        v = game.look()
        info = {"note": note, **snapshots.describe(v)}
        name = snapshots.save(v.frame, "manual " + (note or ""), info, png=True,
                              keep=int(self._snap_cfg().get("keep", 300)))
        self.status.snaps += 1
        self._set("ready" if self.status.phase == "busy" else self.status.phase,
                  f"Snapshot saved: {name} (see 'Snapshots' below)")

    def _run(self):
        while True:
            cmd, kw = self.q.get()
            try:
                if cmd == "quit":
                    self._close()
                    return
                getattr(self, f"_do_{cmd}")(**kw)
            except Stopped:
                self.stop_patrol.clear()
                self._set("ready", "Stopped")
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
            raise RuntimeError("Connect to the game first")
        cfg = self._cfg()
        g = make_game(self.gb, cfg)
        g.stop_check = self.stop_patrol.is_set           # Stop works even mid-jump / mid-zoom
        gap = (cfg.get("hunt") or {}).get("zoom_gap")
        if gap:
            g.zoom_gap = int(gap)
        return g

    def _do_open(self):
        from .browser import GameBrowser
        cfg = self._cfg()
        if self.gb and self.gb.alive:
            self._set("ready", "Already connected. " + self._where())
            return
        self._close()
        if not cfg.get("game_window") and not cfg.get("game_url"):
            self._set("error", "Nothing to connect to yet: in box 1, type the game's window "
                               "title (PC game / BlueStacks) or the web page where you play, "
                               "then Save.")
            return
        self._set("opening", "Connecting to the game..." if cfg.get("game_window")
                  else "Opening the game page...")
        self.gb = GameBrowser.from_config(cfg).__enter__()
        if cfg.get("game_window"):
            self._set("ready", f"Connected to the game window: {self._where()} "
                               "Now press 'What do you see?'. (Mouse into a corner = stop.)")
        else:
            self._set("ready", "Game page open. Log in, go to the map, close menus, then press "
                               "'What do you see?'.")

    def _where(self) -> str:
        title = getattr(self.gb, "window_title", None)
        if title:
            try:
                x, y, w, h = self.gb.rect()
                return f"'{title}' ({w}x{h})."
            except Exception:
                return f"'{title}'."
        return "browser page " + (getattr(self.gb.page, "url", "") or "") + "."

    def _diagnose(self, frame, v) -> list[str]:
        """Plain reasons when the bot can't make sense of the picture."""
        from .desktop import is_blank, list_windows
        notes = []
        windowed = getattr(self.gb, "window_title", None) is not None
        if is_blank(frame):
            if windowed:
                notes.append("The picture is blank. The game window may be minimised, or Windows "
                             "won't share it: put the game on screen (not minimised) and try again.")
            else:
                notes.append("The picture is blank: the browser page isn't showing the game. If "
                             "you play in the Rise of Kingdoms PC program, type its window title "
                             "in box 1 instead.")
        elif not v.camera:
            notes.append("I can see a picture but not the map's coordinates (#4183 X:… Y:… at the "
                         "top left). Is the game on the map with no menu open?")
        if not windowed and not self._cfg().get("game_window"):
            notes.append("Mode: browser page. For the PC game, fill in the window title in box 1.")
        if windowed:
            notes.append(f"Mode: game window '{self.gb.window_title}', picture taken "
                         f"{'in the background' if self.gb.last_capture == 'background' else 'from the screen'}.")
        if is_blank(frame) or not v.camera:
            titles = [t for _, t in list_windows()][:20]
            if titles:
                notes.append("Windows I can see: " + "; ".join(titles))
        return notes

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
        notes = self._diagnose(v.frame, v)
        self.status.look = {
            "notes": notes,
            "size": [int(v.frame.shape[1]), int(v.frame.shape[0])],
            "camera": list(v.camera) if v.camera else None,
            "zoom": game.zoom(v) if v.camera else "unknown (can't read the map)",
            "troops": [s.state for s in panel[2]] if panel else None,
            "marches": [f"{ln.color} line, {ln.dots} dots" for ln in lines],
            "armies": len(find_army_icons(v.frame)),
            "time": time.strftime("%H:%M:%S"),
            "area": game.area,
        }
        problem = next((n for n in notes if n.startswith(("The picture is blank", "I can see a picture"))), "")
        self._set("ready", warn or problem or "Here's what I see")

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
        zoom = game.zoom()
        m = learn_axes(game)
        if m is None:
            self._set("ready", "Couldn't learn map directions. Is the coordinate readout visible?")
            return
        # directions depend on the zoom level: keep one set per level
        key = "hunt.axes_close" if zoom == "close" else "patrol.axes"
        config.save_fields({key: m}, self.cfg_path)
        what = "hunting (close zoom)" if zoom == "close" else "patrol (zoomed-out map)"
        self._set("ready", f"Learned the map directions for {what}.")

    def _do_remember_zoom(self, kind="sweep"):
        """One-time zoom setup. You zoom the game to where you can see as much map as
        possible with the army icons still showing; the bot counts the scroll notches
        in to the close zoom (where resource points can be tapped and names show) and
        back, and learns the map directions at your zoom. From then on it only ever
        moves between those two zooms."""
        from .patrol import learn_axes
        game = self._game()
        if game.level() != "patrol":
            self._set("ready", "First zoom the game out to the simplified map (army icons still "
                               "showing, no 3D buildings), close any menus, then press this again.")
            return
        self._set("busy", "Setting up the zoom: counting notches to the close zoom and back...")
        gap, ok = game.calibrate_zoom()
        if not gap or not ok:
            self._set("ready", "Couldn't measure the zoom (it didn't come back to the same picture). "
                               "Close any menus and try again. Nothing was saved.")
            return
        self.status.doing = "Learning the map directions at this zoom (two small drags)"
        m = learn_axes(game)
        if m is None:
            self._set("ready", "Couldn't learn the map directions (is the coordinate readout "
                               "visible?). Nothing was saved.")
            return
        config.save_fields({"hunt.zoom_gap": gap, "patrol.axes": m}, self.cfg_path)
        self._set("ready", f"Zoom set up: it searches at this zoom and goes {gap} notches in to "
                           "check names. Ready to watch him.")

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

    def _do_hunt(self, max_rounds=None):
        from .commands import sites_from
        from .hunter import Hunter
        cfg = self._cfg()
        game = self._game()
        h = cfg.get("hunt", {})
        sites = sites_from(cfg)
        if not h.get("city"):
            enemy = next((s for s in sites if s.kind == "enemy"), None)
            if enemy:
                cfg.setdefault("hunt", {})["city"] = [enemy.x, enemy.y]
        mine = [(s.name, (s.x, s.y)) for s in sites if s.kind == "mine"]
        hunter = Hunter(game, cfg, mine, cfg.get("hunt", {}).get("log", "his_armies.csv"))
        problems = hunter.ready()
        if problems:
            self._set("ready", "Can't hunt yet: " + "; ".join(problems))
            return
        self.hunter = hunter

        def prog(done, total, doing):
            self.status.progress = [done, total]
            mode = {"sweep": "Searching", "track": "Tracking", "base": "Watching his city"}.get(hunter.mode, "")
            self.status.doing = (mode + ": " if mode and not doing.startswith(mode) else "") + doing
            self.status.armies = hunter.tracker.summary()
            self._maybe_sample_quick()
        hunter.on_progress = prog
        notifier = Notifier.from_config(cfg.get("notify", {}))
        self.stop_patrol.clear()
        self.status.round = 0
        self._set("patrolling", f"Hunting {hunter.name} around {hunter.city[0]},{hunter.city[1]}")
        while not self.stop_patrol.is_set() and (max_rounds is None or self.status.round < max_rounds):
            for a in hunter.round(stop=self.stop_patrol.is_set):
                self._alert(notifier, a)
            self.status.round += 1
            self._maybe_sample()
            self.status.armies = hunter.tracker.summary()
            self.status.last_site = f"{len(self.status.armies)} of his armies tracked"
            end = time.time() + float(h.get("pause_between_sweeps", 5))
            while time.time() < end and not self.stop_patrol.is_set():
                if self._snap_wanted is not None:
                    self._maybe_sample()
                time.sleep(0.2)
        self.stop_patrol.clear()
        his = hunter.tracker.summary()
        where = "; ".join(f"#{a['id']} at {a['x']},{a['y']} ({a['state']})" for a in his[:6])
        self._set("ready", f"Stopped after {self.status.round} round(s). "
                  + (f"His armies last seen: {where}" if his else "None of his armies are out."))

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
            for i, site in enumerate(patrol.sites):
                if self.stop_patrol.is_set():
                    break
                self.status.last_site = site.name
                self.status.progress = [i, len(patrol.sites)]
                self.status.doing = f"Checking {site.name} ({site.x},{site.y})"
                for a in patrol.visit(site):
                    self._alert(notifier, a)
                self._maybe_sample()
            self.status.round += 1
            log.info("Round %d done", self.status.round)
            end = time.time() + float(p.get("pause_between_rounds", 5))
            while time.time() < end and not self.stop_patrol.is_set():
                if self._snap_wanted is not None:
                    self._maybe_sample()
                time.sleep(0.2)
        self.stop_patrol.clear()
        self._set("ready", f"Patrol stopped after {self.status.round} round(s)")
