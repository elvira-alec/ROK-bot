"""Opens the web version of the game in a real browser window and takes screenshots."""

from __future__ import annotations

import cv2
import numpy as np
from playwright.sync_api import sync_playwright


class GameBrowser:
    def __init__(self, url: str, profile_dir: str, viewport=(1600, 900)):
        self.url = url
        self.profile_dir = profile_dir
        self.viewport = {"width": viewport[0], "height": viewport[1]}
        self._pw = self._ctx = self.page = None

    def __enter__(self):
        self._pw = sync_playwright().start()
        # A visible window with a saved profile, so your login is remembered.
        self._ctx = self._pw.chromium.launch_persistent_context(
            self.profile_dir, headless=False, viewport=self.viewport,
            args=["--disable-background-timer-throttling",
                  "--disable-renderer-backgrounding",
                  "--disable-backgrounding-occluded-windows"])
        self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
        if self.page.url in ("", "about:blank"):
            self.page.goto(self.url)
        return self

    def __exit__(self, *exc):
        try:
            self._ctx.close()
        finally:
            self._pw.stop()

    def screenshot(self, area=None) -> np.ndarray:
        png = self.page.screenshot()
        frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if area:
            x, y, w, h = area
            frame = frame[y:y + h, x:x + w]
        return frame
