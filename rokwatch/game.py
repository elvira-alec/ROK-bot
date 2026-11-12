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
        if area and hasattr(browser, "set_area"):
            browser.set_area(area)

    # ---------------------------------------------------------------- looking
    def frame(self):
        return self.b.screenshot(self.area)

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
            # tap the magnifier (found by its shape, so never the star next to it)
            self.b.click(*screen.search_button(v.frame, bar[3]))
            self.wait(1.0)
            if self._fill_coord_dialog(x, y):
                self.wait(self.settle + 1.0)
                v = self.look()
                if v.camera and abs(v.camera[1] - x) <= 2 and abs(v.camera[2] - y) <= 2:
                    return True
                log.warning("goto %s,%s: camera is at %s (try %d)", x, y, v.camera, attempt + 1)
            self.close_popups()
        return False

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
