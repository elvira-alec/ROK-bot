"""Use the game when it runs as its own program (the Rise of Kingdoms PC game, or
BlueStacks) instead of in a browser tab. Windows only.

The bot finds the window by (part of) its title, screenshots just that window and
clicks inside it with the real mouse and keyboard. While it does, you can't use the
laptop's mouse yourself. Safety switch: shove the mouse into a corner of the
screen and the bot stops at once (pyautogui's fail-safe).

Same methods as GameBrowser, so everything else works unchanged.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import time

import numpy as np

log = logging.getLogger("rokwatch")

if sys.platform == "win32":
    from ctypes import wintypes
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    try:  # real pixels, not Windows' display scaling
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


def list_windows():
    """[(handle, title)] of visible top-level windows that have a title."""
    if sys.platform != "win32":
        return []
    out = []
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                out.append((hwnd, buf.value))
        return True
    user32.EnumWindows(proto(cb), 0)
    return out


def find_window(title_part: str):
    """The window whose title contains title_part (case doesn't matter).
    Skips our own control panel and console."""
    want = title_part.lower()
    skip = ("rok watcher", "rokwatcher.exe", "rokwatcher")
    hits = [(h, t) for h, t in list_windows()
            if want in t.lower() and not any(s in t.lower() for s in skip)]
    # prefer the biggest match (the game, not a small dialog)
    best, best_area = None, 0
    for h, t in hits:
        r = _client_rect(h)
        area = r[2] * r[3] if r else 0
        if area > best_area:
            best, best_area = (h, t), area
    return best


def _client_rect(hwnd):
    """[x, y, w, h] of the window's inside (no title bar / borders), screen pixels."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        return None
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return [pt.x, pt.y, rect.right - rect.left, rect.bottom - rect.top]


# keys as the rest of the bot names them -> pyautogui names
_KEYS = {"Control": "ctrl", "Escape": "esc", "Enter": "enter", "Backspace": "backspace",
         "Tab": "tab", "Delete": "delete"}


class DesktopWindow:
    def __init__(self, title: str, zoom_modifier: str = "", url: str = ""):
        self.title = title
        self.zoom_modifier = zoom_modifier or ""   # e.g. "ctrl" for BlueStacks zoom
        self.url = url
        self.hwnd = None
        self.page = None                           # (no browser page)
        self._area = None

    @classmethod
    def from_config(cls, cfg: dict):
        return cls(cfg["game_window"], cfg.get("game_window_zoom_key", ""), cfg.get("game_url", ""))

    def __enter__(self):
        if sys.platform != "win32":
            raise RuntimeError("Using a game window (not a browser) only works on Windows")
        import mss          # loaded here, so systems without them can still run the rest
        import pyautogui
        self._mss = mss
        pyautogui.FAILSAFE = True                  # mouse into a corner = stop
        pyautogui.PAUSE = 0.05
        self._pg = pyautogui
        self._attach()
        return self

    def _attach(self):
        found = find_window(self.title)
        if not found:
            titles = [t for _, t in list_windows()][:15]
            raise RuntimeError(f"Couldn't find a window called '{self.title}'. Open the game "
                               f"first. Windows I can see: {titles}")
        self.hwnd, real_title = found
        log.info("Using the game window '%s'", real_title)

    def __exit__(self, *exc):
        self.hwnd = None

    @property
    def alive(self) -> bool:
        return bool(self.hwnd) and bool(user32.IsWindow(self.hwnd))

    def _front(self):
        """Bring the game to the front (needed for the screenshot and for clicks)."""
        if not self.alive:
            self._attach()
        if user32.GetForegroundWindow() != self.hwnd:
            if user32.IsIconic(self.hwnd):
                user32.ShowWindow(self.hwnd, 9)       # SW_RESTORE
            # Windows only lets the active app change focus; a key tap makes us it
            user32.keybd_event(0x12, 0, 0, 0)         # Alt down
            user32.SetForegroundWindow(self.hwnd)
            user32.keybd_event(0x12, 0, 2, 0)         # Alt up
            time.sleep(0.25)

    def rect(self):
        r = _client_rect(self.hwnd)
        if not r or r[2] < 50 or r[3] < 50:
            raise RuntimeError("The game window is minimised or too small")
        return r

    # ---- what GameBrowser offers
    def find_game_area(self):
        return None, False          # the whole window is the game

    def set_area(self, area):
        self._area = area

    def _abs(self, x, y):
        wx, wy, _, _ = self.rect()
        ax, ay = (self._area[0], self._area[1]) if self._area else (0, 0)
        return int(wx + ax + x), int(wy + ay + y)

    def screenshot(self, area=None) -> np.ndarray:
        import mss
        self._front()
        x, y, w, h = self.rect()
        with mss.mss() as sct:
            shot = np.array(sct.grab({"left": x, "top": y, "width": w, "height": h}))
        frame = shot[..., :3].copy()               # BGRA -> BGR
        if area:
            ax, ay, aw, ah = area
            frame = frame[ay:ay + ah, ax:ax + aw]
        return frame

    def click(self, x, y):
        self._front()
        self._pg.click(*self._abs(x, y))

    def press(self, key):
        self._front()
        keys = [_KEYS.get(k, k.lower()) for k in key.split("+")]
        if len(keys) > 1:
            self._pg.hotkey(*keys)
        else:
            self._pg.press(keys[0])

    def type(self, text):
        self._front()
        self._pg.typewrite(str(text), interval=0.06)

    def wheel(self, x, y, dy):
        """Browser-style wheel: positive dy scrolls down (zooms out)."""
        self._front()
        self._pg.moveTo(*self._abs(x, y))
        notches = max(1, round(abs(dy) / 100))
        clicks = -notches if dy > 0 else notches
        if self.zoom_modifier:
            self._pg.keyDown(self.zoom_modifier)
        try:
            for _ in range(notches):
                self._pg.scroll(int(np.sign(clicks)) * 120)
                time.sleep(0.05)
        finally:
            if self.zoom_modifier:
                self._pg.keyUp(self.zoom_modifier)

    def drag(self, x0, y0, x1, y1, steps=15):
        self._front()
        self._pg.moveTo(*self._abs(x0, y0))
        self._pg.mouseDown()
        self._pg.moveTo(*self._abs(x1, y1), duration=0.4)
        self._pg.mouseUp()
