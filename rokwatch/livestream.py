"""The live view's pictures, up to 24 a second (the user: "smoother stream, 24fps").

While at least one page is watching (/live.mjpg), a background thread keeps grabbing the
game window and compressing it for each picture size asked for; every viewer just sends the
newest one. Measured on the user's laptop: a plain screen grab of the window 34 ms, shrinking
straight from the grabbed BGRA picture 6 ms (going through a BGR view first: 23-32 ms), so
~24 a second. When the game isn't the front window (covered), the slower covered-window
capture (54 ms) is used instead. Stops by itself 5 s after the last viewer leaves.
"""

from __future__ import annotations

import threading
import time

import cv2
import numpy as np


class LiveStream:
    FPS = 24

    def __init__(self, get_window):
        self.get_window = get_window          # -> the DesktopWindow (or None)
        self._lock = threading.Lock()
        self._widths: dict[int, float] = {}  # picture width -> last asked for
        self._jpeg: dict[int, tuple[int, bytes]] = {}
        self._seq = 0
        self._thread = None

    def want(self, width: int):
        with self._lock:
            self._widths[width] = time.time()
            if not self._thread or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def latest(self, width: int):
        """(sequence number, JPEG bytes) of the newest picture at this width, or (0, None)."""
        with self._lock:
            return self._jpeg.get(width, (0, None))

    def _grab(self, sct):
        w = self.get_window()
        if w is None or not getattr(w, "hwnd", None):
            return None
        try:
            import ctypes
            front = ctypes.windll.user32.GetForegroundWindow() == w.hwnd
        except Exception:
            front = False
        if front and sct is not None:
            x, y, ww, hh = w.rect()
            g = sct.grab({"left": x, "top": y, "width": ww, "height": hh})
            return np.frombuffer(g.raw, np.uint8).reshape(g.height, g.width, 4)     # BGRA
        return w.screenshot()                                                      # BGR

    def _run(self):
        try:
            import mss
            sct = mss.mss()
        except Exception:
            sct = None
        try:
            while True:
                t0 = time.time()
                with self._lock:
                    widths = [wd for wd, t in self._widths.items() if t0 - t < 5]
                    self._widths = {wd: self._widths[wd] for wd in widths}
                if not widths:
                    return                                  # nobody watching
                try:
                    f = self._grab(sct)
                except Exception:
                    f = None
                if f is not None:
                    h, w = f.shape[:2]
                    self._seq += 1
                    out = {}
                    for wd in widths:
                        small = cv2.resize(f, (wd, max(1, int(h * wd / w))), interpolation=cv2.INTER_LINEAR)
                        if small.shape[2] == 4:
                            small = small[..., :3]
                        ok, buf = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, 60])
                        if ok:
                            out[wd] = (self._seq, buf.tobytes())
                    with self._lock:
                        self._jpeg.update(out)
                time.sleep(max(0.0, 1 / self.FPS - (time.time() - t0)))
        finally:
            if sct is not None:
                try:
                    sct.close()
                except Exception:
                    pass
