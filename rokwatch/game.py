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
        self.notch_wait = 0.25
        self._zoom_now = None          # notches in from fully out, when known
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
        Checks the readout afterwards; True when the camera is there."""
        for attempt in range(tries):
            v = self.look()
            if v.camera and abs(v.camera[1] - x) <= 1 and abs(v.camera[2] - y) <= 1:
                return True
            bar = screen.camera_coords(v.frame, v.texts)
            if not bar:
                log.warning("goto: can't see the coordinates bar (menu open?)")
                self.close_popups()
                continue
            # tap the magnifier (found by its shape, so never the star next to it).
            # If the search bar doesn't open (e.g. Favorites opened instead), close
            # that and try the next spot.
            if not self._open_search(v, bar[3]):
                continue
            if self._fill_coord_dialog(x, y):
                self.wait(self.settle + 1.0)
                v = self.look()
                if v.camera and abs(v.camera[1] - x) <= 2 and abs(v.camera[2] - y) <= 2:
                    return True
                log.warning("goto %s,%s: camera is at %s (try %d)", x, y, v.camera, attempt + 1)
            self.close_popups()
        return False

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

    # ------------------------------------------------- fixed zoom (wheel notches)
    def _small(self, frame=None):
        import cv2
        f = self.frame() if frame is None else frame
        g = cv2.cvtColor(f, cv2.COLOR_BGR2GRAY)
        return cv2.resize(g, (160, max(8, int(160 * g.shape[0] / g.shape[1]))),
                          interpolation=cv2.INTER_AREA).astype(np.float32)

    def zoom_all_out(self, max_notches=60) -> int:
        """Scroll out until the picture stops changing (fully zoomed out).
        Returns how many notches it took."""
        H, W = self.frame().shape[:2]
        prev = self._small()
        still = 0
        for n in range(1, max_notches + 1):
            self.b.wheel(W // 2, H // 2, 100)
            time.sleep(self.notch_wait)
            cur = self._small()
            # share of the picture that changed (marching icons are only a few pixels)
            if float(np.mean(np.abs(cur - prev) > 12)) < 0.004:
                still += 1
                if still >= 2:
                    return n - 2
            else:
                still = 0
            prev = cur
        return max_notches

    def set_zoom(self, notches_in: int, anchor=False):
        """The same zoom every time: all the way out, then N notches in. When the zoom
        is already known it just scrolls the difference (quicker)."""
        H, W = self.frame().shape[:2]
        if anchor or self._zoom_now is None:
            self.zoom_all_out()
            self._zoom_now = 0
        diff = int(notches_in) - self._zoom_now
        for _ in range(abs(diff)):
            self.b.wheel(W // 2, H // 2, -100 if diff > 0 else 100)
            time.sleep(self.notch_wait)
        self._zoom_now = int(notches_in)
        self.wait(0.6)

    def measure_zoom(self) -> int:
        """How many notches in from fully zoomed out the view is now (and put it back)."""
        n = self.zoom_all_out()
        H, W = self.frame().shape[:2]
        for _ in range(n):
            self.b.wheel(W // 2, H // 2, -100)
            time.sleep(self.notch_wait)
        self.wait(0.6)
        return n

    # ------------------------------------------------------------- panning
    def camera_fast(self):
        """Camera (x, y) from the coordinate readout only (much quicker than look())."""
        f = self.frame()
        c = screen.camera_coords(f)
        return (c[1], c[2]) if c else None

    def pan_to(self, x, y, axes, max_drags=4, close_enough=2.0) -> bool:
        """Drag the map to (x, y) instead of typing coordinates: fast for nearby spots.
        axes: tiles-per-pixel matrix learned at the current zoom. Falls back to goto."""
        if not axes:
            return self.goto(x, y)
        A = np.array(axes, float)
        try:
            Ainv = np.linalg.inv(A)
        except np.linalg.LinAlgError:
            return self.goto(x, y)
        H, W = self.frame().shape[:2]
        cam = self.camera_fast()
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
            time.sleep(0.25)
            cam = self.camera_fast()
        return self.goto(x, y)

    def _fill_coord_dialog(self, x, y) -> bool:
        """Type X and Y into the coordinate search box and confirm.

        Finds the fields by their 'X' / 'Y' labels. If your dialog looks different,
        set goto_dialog in config.yaml (positions as fractions of the game screen).
        """
        v = self.look()
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
            self.wait(0.3)
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

    def close_popups(self):
        self.b.press("Escape")
        self.wait(0.6)

    def zoom_to_patrol_level(self, max_steps=10) -> bool:
        """Get to the simplified map that still shows resource points and armies:
        scroll out from the 3D view, back in if it went too far (globe button shows)."""
        for _ in range(max_steps):
            v = self.look()
            level = screen.zoom_level(v.frame, v.texts)
            if level == "patrol":
                return True
            H, W = v.frame.shape[:2]
            self.b.wheel(W // 2, H // 2, 300 if level == "close" else -200)
            self.wait(0.8)
        v = self.look()
        return screen.zoom_level(v.frame, v.texts) == "patrol"

    zoom_out_to_simple = zoom_to_patrol_level  # old name

    def zoom_to_close(self, max_steps=10) -> bool:
        """Scroll in to the 3D view, where names show over troops."""
        for _ in range(max_steps):
            v = self.look()
            if screen.zoom_level(v.frame, v.texts) == "close":
                return True
            H, W = v.frame.shape[:2]
            self.b.wheel(W // 2, H // 2, -300)
            self.wait(0.8)
        v = self.look()
        return screen.zoom_level(v.frame, v.texts) == "close"

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
        btn = v.find(r"^RECALL$")
        if not btn:
            log.warning("recall: no RECALL button after selecting troop %d", slot.index + 1)
            self.close_popups()
            return False
        self.b.click(*btn[0].center)
        self.wait()
        v = self.look()
        if v.find(r"^RECALL$"):
            # maybe a confirm dialog
            ok = v.find(r"^(CONFIRM|OK|YES)$")
            if ok:
                self.b.click(*ok[0].center)
                self.wait()
                v = self.look()
        done = not v.find(r"^RECALL$")
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
