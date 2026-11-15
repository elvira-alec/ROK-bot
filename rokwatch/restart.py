"""Starting the game again after the user played on another device.

When the user opens the game on their phone, the PC game says "You are already logged in on
another device" and closes. The bot then PAUSES (it never starts the game behind the user's
back - that would throw them off their phone). When the user presses Resume (or Watch / Guard)
in the panel, this starts the game itself:

  1. the RoK launcher (opened if it isn't) -> its big start button ("COMENZAR" / "START");
  2. the game's start screen -> "Click to Start" (a tap low in the middle);
  3. whatever comes next is left to the usual recovery: the reconnect dialog (CONFIRM), your
     city (map button) - see Game.recover_connection / leave_city_view.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time

import numpy as np

log = logging.getLogger("rokwatch")

GAME_TITLE = "Rise of Kingdoms"
LAUNCHER_PATHS = [r"C:\Program Files (x86)\Rise of Kingdoms\launcher.exe",
                  r"C:\Program Files\Rise of Kingdoms\launcher.exe"]


def launcher_path(cfg: dict | None = None):
    """The RoK launcher: from the setup (game.launcher), else the usual install folders."""
    p = ((cfg or {}).get("game") or {}).get("launcher")
    for c in ([p] if p else []) + LAUNCHER_PATHS:
        if c and os.path.exists(c):
            return c
    return None


def _windows():
    from .desktop import list_windows
    return list_windows()


def game_window():
    """(hwnd, title) of the game's own window, or None."""
    return next(((h, t) for h, t in _windows() if t.strip() == GAME_TITLE), None)


def launcher_window():
    """(hwnd, title) of the RoK launcher window, or None. Its title is just 'Launcher', so
    the window must belong to the RoK launcher's program (not some other 'launcher')."""
    for h, t in _windows():
        if t.strip().lower() == "launcher" and "rise of kingdoms" in (_exe_of(h) or "").lower():
            return h, t
    return None


def _exe_of(hwnd):
    """Full path of the program that owns a window (Windows only), or None."""
    try:
        import ctypes
        from ctypes import wintypes
        pid = wintypes.DWORD()
        ctypes.windll.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid.value)   # QUERY_LIMITED_INFORMATION
        if not h:
            return None
        try:
            buf = ctypes.create_unicode_buffer(1024)
            n = wintypes.DWORD(1024)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(n)):
                return buf.value
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
    except Exception:
        return None
    return None


def find_start_button(frame):
    """Centre of the launcher's big orange start button (bottom right), or None.
    Measured on the user's launcher: an orange-yellow block about 0.17 x 0.08 of the window
    around (0.865, 0.824)."""
    import cv2
    H, W = frame.shape[:2]
    y0, x0 = int(0.65 * H), int(0.60 * W)
    hsv = cv2.cvtColor(frame[y0:, x0:], cv2.COLOR_BGR2HSV)
    m = ((hsv[..., 0] >= 12) & (hsv[..., 0] <= 32) & (hsv[..., 1] > 170) & (hsv[..., 2] > 200)).astype(np.uint8)
    n, _, st, cent = cv2.connectedComponentsWithStats(m)
    best = None
    for i in range(1, n):
        x, y, w, h, a = st[i]
        if w > 0.08 * W and 0.04 * H < h < 0.15 * H and a > 0.5 * w * h and (best is None or a > best[1]):
            best = (i, a)
    if best is None:
        return None
    cx, cy = cent[best[0]]
    return int(x0 + cx), int(y0 + cy)


def start_game(cfg=None, stop=None, say=None, timeout=240) -> bool:
    """Get the game running and past its start screen. True once the game shows the map,
    your city or a game dialog (the usual recovery takes it from there)."""
    from .desktop import DesktopWindow
    from . import screen
    say = say or (lambda m: None)
    end = time.time() + timeout

    def stopped():
        return bool(stop and stop())

    if not game_window():
        if not launcher_window():
            path = launcher_path(cfg)
            if not path:
                log.warning("restart: can't find the RoK launcher")
                return False
            say("Opening the game's launcher")
            log.info("restart: opening the launcher %s", path)
            subprocess.Popen([path], cwd=os.path.dirname(path), close_fds=True)
            while not launcher_window() and not game_window() and time.time() < end and not stopped():
                time.sleep(1.0)
        pressed = 0.0
        while not game_window() and time.time() < end and not stopped():
            lw = launcher_window()
            # only when the button is really there, and once (again only if the game hasn't
            # opened 30 s later) - the user: "don't spam the clicks"
            if lw and time.time() - pressed > 30:
                with DesktopWindow(lw[1]) as w:
                    f = w.screenshot()
                    b = find_start_button(f)
                    if b:
                        say("Pressing start in the launcher")
                        log.info("restart: pressing the launcher's start button at %d,%d", *b)
                        w.click(*b)
                        pressed = time.time()
            time.sleep(1.0)
    if not game_window():
        log.warning("restart: the game didn't open")
        return False
    # the game's start screen ("Click to Start"): tap low in the middle until it's past it
    say("Getting past the game's start screen")
    tapped = 0.0
    with DesktopWindow(GAME_TITLE) as w:
        while time.time() < end and not stopped():
            if not game_window():
                time.sleep(1.0)
                continue
            f = w.screenshot()
            # up = the map (X:Y readout), your city, or a game dialog (reconnect). Not just
            # "some text": the loading screen's version line counted, and the watch started
            # a minute early, bumping into the loading screen.
            texts = screen.top_texts(f)
            if screen.modal_dialog(f) or screen.camera_coords(f, texts) or screen.home_view(f, texts):
                log.info("restart: the game is up")
                return True
            if time.time() - tapped > 4:
                H, W = f.shape[:2]
                w.click(W // 2, int(0.84 * H))
                tapped = time.time()
            time.sleep(1.0)
    return False
