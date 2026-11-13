"""Doing things in the game: jump to coordinates, find and recall your troops.

Every action checks its own result by reading the screen again, and gives up
cleanly (returns False) instead of clicking blindly.
"""

from __future__ import annotations

import logging
import math
import re
import time
from dataclasses import dataclass

import numpy as np

from . import screen

log = logging.getLogger("rokwatch")


# button words in the game's languages (English, Spanish, ...)
RECALL_RX = r"^(RECALL|RETIRAR|RETIRADA|REGRESAR|VOLVER|LLAMAR|RAPPEL(ER)?|ZUR(U|Ü)CK(RUFEN)?|RICHIAMA)$"
CONFIRM_RX = r"^(CONFIRM|CONFIRMAR|ACEPTAR|OK|YES|S(I|Í)|CONFIRMER|BEST(Ä|A)TIGEN)$"


class Stopped(Exception):
    """Stop was pressed: unwind out of whatever the bot is doing."""


@dataclass
class View:
    frame: np.ndarray
    texts: list
    camera: tuple | None      # (kingdom, x, y)
    simple: bool

    def find(self, pattern):
        return screen.find_text(self.texts, pattern)


class Game:
    def __init__(self, browser, area=None, settle=1.5, goto_cfg: dict | None = None):
        self.b = browser
        self.area = area
        self.settle = settle
        self.goto_cfg = goto_cfg or {}
        self._good_tap = None
        self._bar_cache = None          # where the X / Y boxes and button are (fractions)
        self.notch_wait = 0.25
        self._size = None
        self.zoom_gap = None           # notches between the searching and the close zoom
        self.zoom_cfg = None           # {'gap', 't_out', 't_in'} from the zoom setup
        self._cam = None               # last camera position read
        self._fresh = None             # (time, picture, camera) of the last read
        self.stop_check = None         # returns True when Stop was pressed
        if area and hasattr(browser, "set_area"):
            browser.set_area(area)

    # ---------------------------------------------------------------- looking
    def frame(self):
        f = self.b.screenshot(self.area)
        from .trace import TRACE
        if TRACE.on:
            TRACE.frame(f)
        try:
            self.b.last_frame = f         # for the panel's live view
        except AttributeError:
            pass
        return f

    def look(self, frame=None) -> View:
        frame = self.frame() if frame is None else frame
        texts = screen.read_text(frame)
        cam = screen.camera_coords(frame, texts)
        v = View(frame, texts, cam[:3] if cam else None, screen.is_simple_view(texts, frame))
        try:
            self.b.last_view = v          # kept for snapshots (see snapshots.py)
        except AttributeError:
            pass
        return v

    def look_middle(self, reach=0.42) -> View:
        """Only the text around the middle of the screen (within reach x height of it):
        the name over the army it just jumped to. ~3x quicker than reading everything."""
        frame = self.frame()
        H, W = frame.shape[:2]
        r = int(reach * H)
        x0, y0 = max(0, W // 2 - r), max(0, H // 2 - r)
        texts = screen.read_text(frame, (x0, y0, min(W, W // 2 + r) - x0, min(H, H // 2 + r) - y0))
        v = View(frame, texts, None, False)
        try:
            self.b.last_view = v
        except AttributeError:
            pass
        return v

    def zoom(self, view: View | None = None) -> str:
        v = view or self.look()
        return screen.zoom_level(v.frame, v.texts)

    def wait(self, s=None):
        time.sleep(self.settle if s is None else s)

    # ---------------------------------------------------------------- moving
    def goto(self, x: int, y: int, tries=3) -> bool:
        """Jump the camera to map coordinates using the game's coordinate search.
        Quick path: find the magnifier by its shape, check the bar opened by its blue
        button, type into the remembered boxes (no full text reading)."""
        from .trace import TRACE
        t_start = time.time()
        TRACE.event("goto", x=x, y=y)
        for attempt in range(tries):
            self.check_stop()
            f = self.frame()
            c = screen.camera_coords(f)                # the coordinates strip only (quick)
            if c and abs(c[1] - x) <= 1 and abs(c[2] - y) <= 1:
                return True
            if self._quick_jump(f, x, y) or self._slow_jump(x, y):
                if self._wait_camera(x, y):
                    TRACE.event("goto_done", x=x, y=y, seconds=round(time.time() - t_start, 2))
                    return True
                log.warning("goto %s,%s: camera is at %s (try %d)", x, y, self.camera_fast(),
                            attempt + 1)
            self.close_popups()
        return False

    def _wait_camera(self, x, y, timeout=4.0) -> bool:
        end = time.time() + timeout
        time.sleep(0.05)
        while time.time() < end:
            self.check_stop()
            cam = self.camera_fast(full=False)     # the coordinates strip only: quick
            if cam and abs(cam[0] - x) <= 2 and abs(cam[1] - y) <= 2:
                return True                         # (the next look waits for the icons)
            time.sleep(0.06)
        return False

    def _bar_opened(self, timeout=1.5):
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.06)
            f = self.frame()
            btn = screen.search_go_button(f)
            if btn:
                return f, btn
        return None, None

    def _quick_jump(self, f, x, y) -> bool:
        m = screen.find_magnifier(f)
        if not m:
            return False
        self.b.click(m[0], m[1])
        f2, btn = self._bar_opened()
        if not btn:
            log.warning("goto: tapped the magnifier at %d,%d but the search didn't open", m[0], m[1])
            self.close_popups()
            return False
        H, W = f2.shape[:2]
        cache = self._bar_cache
        if not (cache and abs(cache[2][0] * W - btn[0]) < 0.02 * H
                and abs(cache[2][1] * H - btn[1]) < 0.02 * H):
            # first time: read the bar's row once (just that strip: quick)
            x0, y0 = max(0, int(btn[0] - 0.5 * W)), max(0, int(btn[1] - 0.06 * H))
            region = (x0, y0, int(min(W, btn[0] + 0.05 * H)) - x0, int(0.12 * H))
            bar = screen.find_search_bar(f2, screen.read_text(f2, region, strip=True))
            if not bar:
                v = self.look(f2)                  # the whole screen (slow)
                bar = screen.find_search_bar(v.frame, v.texts)
            if not bar:
                return self._fill_coord_dialog(x, y, self.look(f2))
            self._bar_cache = cache = tuple((p[0] / W, p[1] / H) for p in
                                            (bar.x_field, bar.y_field, bar.button))
        xf, yf, go = ((int(p[0] * W), int(p[1] * H)) for p in cache)
        self._type_into(xf, x)
        self._type_into(yf, y)
        self.b.click(*go)
        return True

    def _type_into(self, pos, val):
        self.b.click(*pos)
        time.sleep(0.06)
        self.b.press("Control+A")
        self.b.press("Backspace")
        self.b.type(val)

    def _slow_jump(self, x, y) -> bool:
        """The old way (full text reading): when the magnifier can't be found."""
        v = self.look()
        bar = screen.camera_coords(v.frame, v.texts)
        if not bar:
            log.warning("goto: can't see the coordinates bar (menu open?)")
            return False
        if not self._open_search(v, bar[3]):
            return False
        return self._fill_coord_dialog(x, y)

    def _open_search(self, v, coords_text) -> bool:
        H = v.frame.shape[0]
        first = screen.search_button(v.frame, coords_text)
        tx, ty, tw, th = coords_text.box
        spots = [first, (int(tx + tw - 0.012 * H), first[1]), (int(tx + tw * 0.92), first[1])]
        if self._good_tap is not None:            # the spot that worked last time goes first
            spots.insert(0, (int(tx + tw + self._good_tap * H), first[1]))
        tried = []
        for sx, sy in spots:
            if any(abs(sx - t) < 0.008 * H for t in tried):
                continue
            tried.append(sx)
            self.b.click(int(sx), int(sy))
            self.wait(0.9)
            v2 = self.look()
            if screen.find_search_bar(v2.frame, v2.texts) or self._find_fields(v2):
                self._good_tap = (sx - tx - tw) / H
                return True
            log.warning("goto: tapped %d,%d but the coordinate search didn't open", sx, sy)
            self.close_popups()
        return False

    # ------------------------------------------------- zoom: two remembered levels
    def _small(self, frame=None):
        import cv2
        f = self.frame() if frame is None else frame
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        return cv2.resize(g, (160, max(8, int(160 * g.shape[0] / g.shape[1]))),
                          interpolation=cv2.INTER_AREA).astype(np.float32)

    @staticmethod
    def _changed(a, b) -> float:
        """Share of the picture that's different (marching icons are only a few pixels)."""
        return float(np.mean(np.abs(a - b) > 12))

    def _settled(self, timeout=0.7, min_wait=0.15):
        """Wait until the zoom step has finished: the picture has (nearly) stopped
        changing. The real map always has something moving (water, flags, marches), so
        'nearly' - a zoom step changes far more. Capped; a step that does get lost is
        caught by the switch-point check."""
        time.sleep(min_wait)
        prev = self._small()
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.07)
            cur = self._small()
            if self._changed(cur, prev) < 0.012:
                return cur
            prev = cur
        return prev

    def _no_popup(self):
        """An open info box makes the game ignore the zoom wheel: close it first."""
        if screen.find_info_box(self.frame()):
            self.close_popups()

    def _notch(self, out: bool):
        self.check_stop()
        from .trace import TRACE
        TRACE.event("notch", out=out)
        H, W = self._size or self.frame().shape[:2]
        self._size = (H, W)
        before = self._small()
        self.b.wheel(W // 2, H // 2, 100 if out else -100)
        after = self._settled()
        if self._changed(after, before) < 0.004 and screen.find_info_box(self.frame()):
            # the zoom did nothing because a box is open: close it and do that step again
            self.close_popups()
            self.b.wheel(W // 2, H // 2, 100 if out else -100)
            after = self._settled()
        return after

    def steps(self, n: int):
        """n notches in (n > 0) or out (n < 0), each one finished before the next."""
        for _ in range(abs(int(n))):
            self._notch(out=n < 0)

    def level(self) -> str:
        return screen.view_level(self.frame())

    # The zoom is remembered as: gap = notches between your close zoom and your searching
    # zoom; t_out = notches out from the close zoom until the simplified map shows;
    # t_in = notches in from the searching zoom until the 3D view shows. Those switch
    # points are checked every time, so a lost notch is corrected straight away.
    def _zoom_numbers(self):
        z = self.zoom_cfg or {}
        gap = int(z.get("gap") or self.zoom_gap or 0)
        return gap, int(z.get("t_out") or 0), int(z.get("t_in") or 0)

    def to_close(self) -> bool:
        """To your close zoom (names show, resource points can be tapped)."""
        from .trace import TRACE
        TRACE.event("zoom", to="close", at=self._at, cfg=self.zoom_cfg)
        self._no_popup()
        if self._at == "close" and self.level() == "close":
            return True
        gap, t_out, t_in = self._zoom_numbers()
        j = 0
        if gap and t_in and self._at == "sweep":
            self.steps(max(0, t_in - 1))            # up to just before the 3D view (no checks)
            j = max(0, t_in - 1)
        while self.level() != "close":              # in, one at a time, until the 3D view
            if j >= max(gap, 4) + 6:
                return False
            self._notch(out=False)
            j += 1
        if gap and t_in:
            self.steps(max(0, gap - t_in))          # the rest of the way to your zoom
        self._at = "close"
        return True

    def to_sweep(self) -> bool:
        """Back to your searching zoom (never further out than the simplified map)."""
        from .trace import TRACE
        TRACE.event("zoom", to="sweep", at=self._at, cfg=self.zoom_cfg)
        self._no_popup()
        lv = self.level()
        if self._at == "sweep" and lv == "patrol":
            return True
        gap, t_out, t_in = self._zoom_numbers()
        if lv == "close":
            j = 0
            if gap and t_out and self._at == "close":
                self.steps(-max(0, t_out - 1))       # up to just before the map shows (no checks)
                j = max(0, t_out - 1)
            while self.level() == "close":           # out, one at a time, until the map shows
                if j >= max(gap, 4) + 6:
                    return False
                self._notch(out=True)
                j += 1
            if gap and t_out:
                self._out_keeping_tags(max(0, gap - t_out))   # the rest of the way to your zoom
        for _ in range(8):                           # too far out (icons gone): back in
            if self.level() != "far":
                break
            self._notch(out=False)
        ok = self.level() == "patrol"
        self._at = "sweep" if ok else None
        return ok

    def _out_keeping_tags(self, n):
        """n notches out, but never past the last zoom where resource points still show
        their level numbers: if the numbers vanish after a notch, one back in and stop
        (and remember the shorter distance for next time)."""
        prev = screen.count_level_tags(self.frame())
        for i in range(n):
            self._notch(out=True)
            cur = screen.count_level_tags(self.frame())
            if prev >= 3 and cur <= 1:
                self._notch(out=False)
                z = self.zoom_cfg
                if z and z.get("gap"):
                    new_gap = int(z["gap"]) - (n - i)
                    if new_gap >= 1:
                        log.info("Zoom: the level numbers vanished %d step(s) early; searching zoom "
                                 "is now %d steps out from the close zoom", n - i, new_gap)
                        z["gap"] = new_gap
                        z["t_in"] = min(int(z.get("t_in") or new_gap), new_gap)
                return
            prev = cur

    def auto_calibrate(self, max_moves=6, progress=None):
        """Work out both zooms by itself:
          - zoom all the way in, then step out one notch at a time;
          - close zoom = the last notch of the 3D view (just before the simplified map);
          - searching zoom = the furthest-out notch where resource points still show their
            level numbers (one more notch and they disappear).
        Needs resource points on screen to see the numbers go: if there are none, it drags
        to other places to find some. Only returns a result it's sure of (it steps back in
        to check the numbers come back). Returns (cfg, message)."""
        say = progress or (lambda m: None)
        H, W = self.frame().shape[:2]
        self._size = (H, W)
        say("Zooming all the way in")
        prev = self._small()
        for _ in range(12):                              # in, in bursts, until it stops changing
            for _ in range(6):
                self.check_stop()
                self.b.wheel(W // 2, H // 2, -100)
                time.sleep(0.04)
            cur = self._settled()
            if self._changed(cur, prev) < 0.01:
                break
            prev = cur
        say("Stepping out to where the 3D view ends")
        k, close_k = 0, None
        while k < 40:
            self._notch(out=True)
            k += 1
            if self.level() != "close":
                close_k = k - 1
                break
        if close_k is None:
            return None, "Couldn't find where the 3D view switches to the map."

        def tags():
            return screen.count_level_tags(self.fresh_frame())
        t = tags()
        moves = 0
        dirs = [(1, 0), (0, 1), (-1, 0), (0, -1), (1, 1), (-1, -1)]
        while t < 3 and moves < max_moves:              # no resource points here: look elsewhere
            say("No resource points here to judge by; moving to find some")
            dx, dy = dirs[moves % len(dirs)]
            self.b.drag(W // 2 + int(0.25 * W * dx), H // 2 + int(0.25 * H * dy),
                        W // 2 - int(0.25 * W * dx), H // 2 - int(0.25 * H * dy))
            moves += 1
            t = tags()
        if t < 3:
            return None, "Couldn't find resource points to calibrate with. Move the map near some and try again."
        say("Stepping out until the level numbers disappear")
        counts = [(k, t)]
        while k < close_k + 25:
            self._notch(out=True)
            k += 1
            if self.level() == "far":
                break
            t = tags()
            counts.append((k, t))
            if t <= 1:
                break
        if counts[-1][1] > 1:
            return None, "The level numbers never clearly disappeared; nothing was saved."
        with_tags = [kk for kk, tt in counts if tt >= 3]
        if not with_tags:
            return None, "Couldn't see the level numbers clearly; nothing was saved."
        search_k = with_tags[-1]
        # check: back in to the searching zoom, the numbers must be there again
        self.steps(k - search_k)
        if tags() < 3:
            return None, "The check didn't match (numbers didn't come back); nothing was saved."
        gap = search_k - close_k
        if gap < 1:
            return None, "The two zooms came out the same; nothing was saved."
        self._at = "sweep"
        cfg = {"gap": gap, "t_out": 1, "t_in": gap}
        self.zoom_cfg = cfg
        return cfg, f"Close zoom = last 3D step; searching zoom = {gap} steps further out (last one with level numbers)."

    def keep_scale(self, search_scale, tries=1):
        """At the searching zoom: has the zoom drifted? Two scale readings must agree
        before it changes anything, and then it moves one notch at most (a bad reading
        used to make it zoom far out). Returns the ratio it saw, or None."""
        if not search_scale:
            return None
        a1, a2 = self.measure_scale(), self.measure_scale()
        if not a1 or not a2 or abs(math.log(a1 / a2)) > 0.06:
            return None                                   # readings disagree: leave it
        ratio = (a1 + a2) / 2 / search_scale
        if ratio > 1.06:                                  # too far out: one in
            self._notch(out=False)
        elif ratio < 0.94:                                # too far in: one out
            self._notch(out=True)
        return ratio

    def zoom_reference(self):
        """Picture of your searching zoom, to find it again when measuring."""
        return self._settled()

    def match_score(self, ref, cur=None) -> float:
        """How well the current picture matches the reference at the same zoom (0-1).
        Small sideways shifts don't matter; a different zoom scores low."""
        import cv2
        cur = self._small() if cur is None else cur
        h, w = ref.shape
        crop = ref[int(h * 0.2):int(h * 0.8), int(w * 0.2):int(w * 0.8)]
        r = cv2.matchTemplate(cur, crop, cv2.TM_CCOEFF_NORMED)
        return float(r.max())

    def measure_scale(self):
        """Map tiles per screen pixel right now: drag the map sideways a known amount,
        read how far the camera moved, drag it back."""
        H, W = self._size or self.frame().shape[:2]
        self._size = (H, W)
        dx = int(0.4 * W)
        c0 = self.camera_fast()
        self.b.drag(W // 2 + dx // 2, H // 2, W // 2 - dx // 2, H // 2)
        time.sleep(0.25)
        c1 = self.camera_fast()
        self.b.drag(W // 2 - dx // 2, H // 2, W // 2 + dx // 2, H // 2)
        time.sleep(0.25)
        c2 = self.camera_fast()
        # only trust it if the readings make sense: real coordinates, a sensible move,
        # and the drag back landed where it started (a misread digit fails this)
        if not (c0 and c1 and c2):
            return None
        if not all(0 <= v <= 1300 for v in (*c0, *c1, *c2)):
            return None
        d = math.dist(c0, c1)
        if d < 1 or d > 300 or math.dist(c0, c2) > max(2.0, 0.08 * d):
            log.info("zoom: scale measurement didn't add up (%s -> %s -> %s); ignored", c0, c1, c2)
            return None
        return d / dx

    def calibrate_close(self, search_scale, max_steps=25):
        """You're at your close zoom; search_scale = tiles per pixel at your searching
        zoom (from the map directions learned there). Steps out one notch at a time,
        measuring the map's scale after each, and stops as soon as it matches - never
        more than one notch past it, never out to where the icons disappear. Then goes
        back in to your close zoom, noting where the 3D view comes back.
        Returns ({'gap', 't_out', 't_in'}, ratio) - ends at your close zoom."""
        if self.level() != "close" or not search_scale:
            return None, 0.0
        close_scale = self.measure_scale()           # for dragging between armies when zoomed in
        res, t_out, k = [], 0, 0
        while k < max_steps:
            self._notch(out=True)
            k += 1
            lv = self.level()
            if not t_out and lv != "close":
                t_out = k
            sc = self.measure_scale()
            if sc:
                res.append((k, sc))
                if sc >= 0.93 * search_scale:            # reached (or just passed) it
                    break
            if lv == "far":
                break
        if not res:
            return None, 0.0
        gap, sc = min(res, key=lambda r: abs(math.log(r[1] / search_scale)))
        self.steps(k - gap)                              # back in to the matching notch
        t_in = 0
        for j in range(1, gap + 1):                      # and on in to your close zoom
            self._notch(out=False)
            if not t_in and self.level() == "close":
                t_in = j
        self._at = "close"
        cfg = {"gap": gap, "t_out": t_out or gap, "t_in": t_in or gap}
        if close_scale:
            cfg["close_ratio"] = round(close_scale / search_scale, 4)
        self.zoom_cfg = cfg
        return cfg, sc / search_scale

    # old names, used by simple patrol
    def zoom_to_patrol_level(self, max_steps=10) -> bool:
        return self.to_sweep()

    zoom_out_to_simple = zoom_to_patrol_level

    def zoom_to_close(self, max_steps=10) -> bool:
        return self.to_close()

    # ------------------------------------------------------------- panning
    def fast_view(self) -> View:
        """Picture + camera only (reads just the coordinates, not the whole screen):
        enough for icons and march lines, several times quicker than look()."""
        if self._fresh and time.time() - self._fresh[0] < 0.3:
            f, c = self._fresh[1], self._fresh[2]           # just read (after a drag)
        else:
            f = self.frame()
            c = screen.camera_coords(f) or screen.camera_coords(f, screen.read_text(f))
        self._fresh = None
        v = View(f, [], c[:3] if c else None, True)
        if c:
            self._cam = (c[1], c[2])
        try:
            self.b.last_view = v
        except AttributeError:
            pass
        return v

    def fresh_frame(self, max_wait=0.4):
        """The picture as soon as it has finished drawing after a move: grab every ~40 ms
        and take the first one that (nearly) matches the previous - icons loaded, map
        still (water and marches always move a little)."""
        f = self.frame()
        prev = self._small(f)
        end = time.time() + max_wait
        while time.time() < end:
            time.sleep(0.04)
            g = self.frame()
            cur = self._small(g)
            if self._changed(cur, prev) < 0.01:
                return g
            f, prev = g, cur
        return f

    def read_camera(self, frame):
        """(kingdom, x, y) from this picture's coordinate readout (thread-safe: no clicks)."""
        c = screen.camera_coords(frame) or screen.camera_coords(frame, screen.read_text(frame))
        if c:
            self._cam = (c[1], c[2])
        return c[:3] if c else None

    def camera_fast(self, full=True):
        """Camera (x, y) from the coordinate readout only (much quicker than look()).
        full: if that fails, read the whole screen (slow; not while waiting for a jump)."""
        f = self.frame()
        c = screen.camera_coords(f)
        if not c and full:                         # quick read failed: read the whole screen
            c = screen.camera_coords(f, screen.read_text(f))
        self._cam = (c[1], c[2]) if c else None
        self._fresh = (time.time(), f, c)
        return self._cam

    def pan_to(self, x, y, axes, max_drags=2, close_enough=4.0) -> bool:
        """Drag the map to (x, y): one drag for a nearby spot, from the camera position it
        already knows (the next look reads the real one). Far spots: typed in."""
        self.check_stop()
        if not axes:
            return self.goto(x, y)
        try:
            A = np.array(axes, float)
            Ainv = np.linalg.inv(A)
        except np.linalg.LinAlgError:
            return self.goto(x, y)
        H, W = self._size or self.frame().shape[:2]
        self._size = (H, W)
        cam = self._cam or self.camera_fast()
        if cam is None:
            return self.goto(x, y)
        d = np.array([x - cam[0], y - cam[1]], float)
        if np.hypot(*d) <= close_enough:
            return True
        p = -(Ainv @ d)                           # drag the map this many pixels
        far = max(abs(p[0]) / (0.6 * W), abs(p[1]) / (0.6 * H))   # one drag covers 60% of the view
        if far > max_drags:                       # too far to drag: type it in
            return self.goto(x, y)
        n = max(1, int(np.ceil(far)))
        step = p / n
        for _ in range(n):
            self.check_stop()
            cx, cy = W // 2 - int(step[0]) // 2, H // 2 - int(step[1]) // 2
            self.b.drag(cx, cy, cx + int(step[0]), cy + int(step[1]))
        moved = -(A @ (step * n))
        self._cam = (cam[0] + moved[0], cam[1] + moved[1])   # where it should be now
        return True

    # ------------------------------------------------------------- stopping
    @property
    def _at(self):
        """'sweep' / 'close': which of the two zooms the game is at, when known (kept on
        the window object, so it survives between commands)."""
        return getattr(self.b, "_zoom_at", None)

    @_at.setter
    def _at(self, v):
        try:
            self.b._zoom_at = v
        except AttributeError:
            pass

    def check_stop(self):
        if self.stop_check and self.stop_check():
            raise Stopped()

    def _fill_coord_dialog(self, x, y, v=None) -> bool:
        """Type X and Y into the coordinate search box and confirm.

        Finds the fields by their 'X' / 'Y' labels. If your dialog looks different,
        set goto_dialog in config.yaml (positions as fractions of the game screen).
        """
        v = v or self.look()
        H, W = v.frame.shape[:2]
        cfg = self.goto_cfg
        if cfg.get("x_field") and cfg.get("y_field"):
            xf = (cfg["x_field"][0] * W, cfg["x_field"][1] * H)
            yf = (cfg["y_field"][0] * W, cfg["y_field"][1] * H)
            go = (cfg["go_button"][0] * W, cfg["go_button"][1] * H) if cfg.get("go_button") else None
        elif (bar := screen.find_search_bar(v.frame, v.texts)):
            xf, yf, go = bar.x_field, bar.y_field, bar.button
        else:
            fields = self._find_fields(v)
            if not fields:
                log.warning("goto: coordinate box not recognised. Texts seen: %s",
                            [t.text for t in v.texts][:20])
                return False
            xf, yf = fields
            btn = [t for t in v.find(r"^(GO|SEARCH|OK|CONFIRM)$") if t.box[1] > H * 0.1]
            go = btn[0].center if btn else None
        for (fx, fy), val in ((xf, x), (yf, y)):
            self.b.click(int(fx), int(fy))
            self.wait(0.12)
            self.b.press("Control+A")
            self.b.press("Backspace")
            self.b.type(val)
            self.wait(0.2)
        if go:
            self.b.click(int(go[0]), int(go[1]))
        else:
            self.b.press("Enter")
        return True

    def _find_fields(self, v: View):
        """Where to type X and Y. The box opens with the current coordinates
        filled in, so look for those numbers; else for 'X' / 'Y' labels."""
        H = v.frame.shape[0]
        body = [t for t in v.texts if t.box[1] > H * 0.1]
        if v.camera:
            xs = [t for t in body if t.text.strip() == str(v.camera[1])]
            ys = [t for t in body if t.text.strip() == str(v.camera[2])]
            if xs and ys:
                return xs[0].center, ys[0].center
        labels = {t.text.strip().upper().rstrip(":："): t for t in body}
        if "X" in labels and "Y" in labels:
            gap = int(0.06 * H)
            lx, ly = labels["X"], labels["Y"]
            return ((lx.box[0] + lx.box[2] + gap, lx.center[1]),
                    (ly.box[0] + ly.box[2] + gap, ly.center[1]))
        return None

    def close_popups(self, tries=3) -> bool:
        """Close whatever is open on top of the map. An info box (tapped resource point,
        army...) is closed with a short drag of the map on its far side - Esc doesn't
        close those on the PC game, and pressing Esc on the plain map opens your
        profile. Esc is only used when something covers the coordinate readout."""
        for _ in range(tries):
            f = self.frame()
            H, W = f.shape[:2]
            box = screen.find_info_box(f)
            if box:
                bx, by, bw, bh = box
                # drag on the side away from the box, a little, on the map
                x = int(bx - 0.12 * W) if bx > W / 2 else int(bx + bw + 0.12 * W)
                x = min(max(x, int(0.12 * W)), int(0.85 * W))
                y = int(min(max(by + bh / 2, 0.25 * H), 0.75 * H))
                # long enough that the game can't take it for a press on whatever is
                # under the mouse (a short drag + hold opened his city's menu)
                self.b.drag(x, y, x, y + int(0.12 * H))
                end = time.time() + 0.5                # gone as soon as it's gone
                while time.time() < end and screen.find_info_box(self.frame()):
                    time.sleep(0.04)
                continue
            if screen.camera_coords(f) or screen.search_go_button(f):
                return True                        # the plain map (or the search bar): fine
            self.b.press("Escape")                 # some window covers the map
            time.sleep(0.5)
        return screen.find_info_box(self.frame()) is None

    # ---------------------------------------------------------------- troops
    def troops(self, view: View | None = None):
        v = view or self.look()
        return screen.troop_panel(v.frame, v.texts)

    def troop_location(self, slot) -> tuple | None:
        """Select a troop; returns (x, y) of where it's gathering (from the info box)."""
        self.b.click(*slot.portrait)
        box, f = None, None
        for _ in range(15):                        # its info box (up to ~0.9 s)
            time.sleep(0.06)
            f = self.frame()
            box = screen.find_info_box(f)
            if box:
                break
        # read just that box (reading the whole screen took 1-3 s a troop)
        texts = screen.read_text(f, box) if box else self.look().texts
        H = f.shape[0]
        for t in texts:
            if t.box[1] < H * 0.12:
                continue  # skip the camera readout at the top
            c = screen.parse_coords(t.text)
            if c:
                return c[1], c[2]
        return None

    def recall(self, slot) -> bool:
        """Select a troop and press RECALL. True if the button was pressed and went away."""
        self.b.click(*slot.portrait)
        self.wait()
        v = self.look()
        btn = v.find(RECALL_RX)
        if not btn:
            log.warning("recall: no RECALL button after selecting troop %d", slot.index + 1)
            self.close_popups()
            return False
        self.b.click(*btn[0].center)
        self.wait()
        v = self.look()
        if v.find(RECALL_RX):
            # maybe a confirm dialog
            ok = v.find(CONFIRM_RX)
            if ok:
                self.b.click(*ok[0].center)
                self.wait()
                v = self.look()
        done = not v.find(RECALL_RX)
        if not done:
            self.close_popups()
        return done

    def recall_all(self, near: list[tuple] | None = None, radius=3) -> list[str]:
        """Recall gathering troops. With `near` = [(x, y), ...], only the ones
        gathering within `radius` tiles of those spots. Returns what happened."""
        report = []
        panel = self.troops()
        if not panel:
            return ["can't see your troop list"]
        _, _, slots = panel
        gathering = [s for s in slots if s.state == "gathering"]
        for s in gathering:
            if near:
                loc = self.troop_location(s)
                self.close_popups()
                if not loc or min(math.dist(loc, p) for p in near) > radius:
                    continue
            ok = self.recall(s)
            report.append(f"troop {s.index + 1}: {'recalled' if ok else 'recall FAILED'}")
        if not report:
            report.append("no gathering troops near the threatened spot")
        return report


def coords_in_chat(texts) -> list[tuple]:
    """Coordinates people posted in chat, e.g. 'Attack (#4183 X:162 Y:374)'."""
    out = []
    for t in texts:
        if re.search(r"attack|enemy|help|rally", t.text, re.I) or "(" in t.text or "（" in t.text:
            c = screen.parse_coords(t.text)
            if c:
                out.append((c[1], c[2]))
    return out
