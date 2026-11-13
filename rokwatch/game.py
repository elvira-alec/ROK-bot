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
        self._cam = None               # last camera position read
        self._fresh = None             # (time, picture, camera) of the last read
        self.stop_check = None         # returns True when Stop was pressed
        if area and hasattr(browser, "set_area"):
            browser.set_area(area)

    # ---------------------------------------------------------------- looking
    def frame(self):
        f = self.b.screenshot(self.area)
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
        for attempt in range(tries):
            self.check_stop()
            f = self.frame()
            c = screen.camera_coords(f) or screen.camera_coords(f, screen.read_text(f))
            if c and abs(c[1] - x) <= 1 and abs(c[2] - y) <= 1:
                return True
            if self._quick_jump(f, x, y) or self._slow_jump(x, y):
                if self._wait_camera(x, y):
                    return True
                log.warning("goto %s,%s: camera is at %s (try %d)", x, y, self.camera_fast(),
                            attempt + 1)
            self.close_popups()
        return False

    def _wait_camera(self, x, y, timeout=4.0) -> bool:
        end = time.time() + timeout
        time.sleep(0.4)
        while time.time() < end:
            self.check_stop()
            cam = self.camera_fast()
            if cam and abs(cam[0] - x) <= 2 and abs(cam[1] - y) <= 2:
                time.sleep(0.25)                   # let the map finish drawing
                return True
            time.sleep(0.25)
        return False

    def _bar_opened(self, timeout=1.5):
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.15)
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
            v = self.look(f2)                      # first time: read the bar once
            bar = screen.find_search_bar(v.frame, v.texts)
            if not bar:
                return self._fill_coord_dialog(x, y, v)
            self._bar_cache = cache = tuple((p[0] / W, p[1] / H) for p in
                                            (bar.x_field, bar.y_field, bar.button))
        xf, yf, go = ((int(p[0] * W), int(p[1] * H)) for p in cache)
        self._type_into(xf, x)
        self._type_into(yf, y)
        self.b.click(*go)
        return True

    def _type_into(self, pos, val):
        self.b.click(*pos)
        time.sleep(0.12)
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

    def _settled(self, timeout=1.2):
        """Wait for the zoom animation to finish; returns the still picture (small)."""
        prev = self._small()
        end = time.time() + timeout
        while time.time() < end:
            time.sleep(0.08)
            cur = self._small()
            if self._changed(cur, prev) < 0.004:
                return cur
            prev = cur
        return prev

    def _notch(self, out: bool):
        self.check_stop()
        H, W = self._size or self.frame().shape[:2]
        self.b.wheel(W // 2, H // 2, 100 if out else -100)
        return self._settled()

    def steps(self, n: int):
        """n notches in (n > 0) or out (n < 0), each one finished before the next."""
        for _ in range(abs(int(n))):
            self._notch(out=n < 0)

    def level(self) -> str:
        return screen.view_level(self.frame())

    def to_close(self) -> bool:
        """From the searching zoom to the close zoom (resource points can be tapped,
        names show over armies): the remembered number of notches in, then checked."""
        if self._at == "close":
            if self.level() == "close":
                return True
            self._at = None
        if self.zoom_gap and self._at == "sweep":
            self.steps(self.zoom_gap)
        for _ in range(8):                         # not there yet: one more at a time
            if self.level() == "close":
                self._at = "close"
                return True
            self._notch(out=False)
        return False

    def to_sweep(self) -> bool:
        """Back to the searching zoom: the same number of notches out, then checked
        (never further out than where the map icons still show)."""
        if self._at == "sweep":
            if self.level() == "patrol":
                return True
            self._at = None
        if self._at == "close" and self.zoom_gap:
            self.steps(-self.zoom_gap)
        for _ in range(10):
            lv = self.level()
            if lv == "patrol":
                self._at = "sweep"
                return True
            self._notch(out=(lv == "close"))       # too close: out; too far: in
        return False

    def calibrate_zoom(self):
        """Call at the zoom you want for searching (simplified map, icons showing).
        Counts the notches in to the close zoom, comes back the same way and checks
        it landed where it started. Returns (gap, ok)."""
        if self.level() != "patrol":
            return None, False
        ref = self._settled()
        H, W = self.frame().shape[:2]
        self._size = (H, W)
        n = 0
        while n < 30 and self.level() != "close":
            self._notch(out=False)
            n += 1
        if n >= 30:
            return None, False
        before = self._settled()
        after = self._notch(out=False)             # one more: resource points load in
        gap = n + 1 if self._changed(after, before) > 0.004 else n   # (unless that's the end)
        self.steps(-gap)
        ok = self._changed(self._settled(), ref) < 0.08 and self.level() == "patrol"
        self.zoom_gap, self._at = gap, "sweep"
        return gap, ok

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

    def camera_fast(self):
        """Camera (x, y) from the coordinate readout only (much quicker than look())."""
        f = self.frame()
        c = screen.camera_coords(f)
        if not c:                                  # quick read failed: read the whole screen
            c = screen.camera_coords(f, screen.read_text(f))
        self._cam = (c[1], c[2]) if c else None
        self._fresh = (time.time(), f, c)
        return self._cam

    def pan_to(self, x, y, axes, max_drags=3, close_enough=2.0) -> bool:
        """Drag the map to (x, y) instead of typing coordinates: fast for nearby spots.
        Uses the camera position it already knows, so a nearby spot is one drag.
        axes: tiles-per-pixel matrix learned at this zoom. Far spots: typed in."""
        self.check_stop()
        if not axes:
            return self.goto(x, y)
        try:
            Ainv = np.linalg.inv(np.array(axes, float))
        except np.linalg.LinAlgError:
            return self.goto(x, y)
        H, W = self._size or self.frame().shape[:2]
        self._size = (H, W)
        cam = self._cam or self.camera_fast()
        for _ in range(max_drags):
            if cam is None:
                break
            d = np.array([x - cam[0], y - cam[1]], float)
            if np.hypot(*d) <= close_enough:
                return True
            p = -(Ainv @ d)                       # drag the map this many pixels
            far = max(abs(p[0]) / (0.38 * W), abs(p[1]) / (0.38 * H))
            if far > max_drags:                   # too far to drag: type it in
                break
            if far > 1:
                p = p / far
            cx, cy = W // 2 - int(p[0]) // 2, H // 2 - int(p[1]) // 2
            self.b.drag(cx, cy, cx + int(p[0]), cy + int(p[1]))
            time.sleep(0.15)
            cam = self.camera_fast()
        return self.goto(x, y)

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
                self.b.drag(x, y, x, y + int(0.03 * H))
                time.sleep(0.35)
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
        self.wait()
        v = self.look()
        for t in v.texts:
            if t.box[1] < v.frame.shape[0] * 0.12:
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
