"""Opens the web version of the game in a browser window and takes screenshots.

Two ways to get a browser:
  * launch one (default): uses your installed Chrome if possible, else the
    browser Playwright downloaded. Your login is kept in browser_profile.
  * attach to a Chrome you started yourself (connect_to_chrome), for when the
    game site or Google won't let you log in from an automated browser.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np
from playwright.sync_api import sync_playwright

log = logging.getLogger("rokwatch")

ARGS = ["--disable-background-timer-throttling", "--disable-renderer-backgrounding",
        "--disable-backgrounding-occluded-windows", "--autoplay-policy=no-user-gesture-required"]


class GameBrowser:
    def __init__(self, url: str, profile_dir: str, viewport=(1600, 900), headless=False,
                 executable_path: str | None = None, channel: str | None = None,
                 connect_url: str | None = None):
        self.url = url
        self.profile_dir = profile_dir
        self.viewport = {"width": int(viewport[0]), "height": int(viewport[1])}
        self.headless = headless
        self.executable_path = executable_path or None
        self.channel = channel or None
        self.connect_url = connect_url or None
        self._pw = self._browser = self._ctx = self.page = None

    @classmethod
    def from_config(cls, cfg: dict):
        return cls(cfg["game_url"], cfg.get("browser_profile", "./browser-profile"),
                   cfg.get("viewport", (1600, 900)), cfg.get("headless", False),
                   cfg.get("browser_executable"), cfg.get("browser_channel"),
                   cfg.get("connect_to_chrome"))

    def _launch(self, channel):
        return self._pw.chromium.launch_persistent_context(
            self.profile_dir, headless=self.headless, viewport=self.viewport,
            executable_path=self.executable_path, channel=channel, args=ARGS,
            ignore_default_args=["--enable-automation"])

    def __enter__(self):
        self._pw = sync_playwright().start()
        try:
            if self.connect_url:
                self._browser = self._pw.chromium.connect_over_cdp(self.connect_url)
                self._ctx = self._browser.contexts[0]
                self.page = self._find_game_tab()
            else:
                try:
                    self._ctx = self._launch(self.channel)
                except Exception as e:
                    if not self.channel or self.executable_path:
                        raise
                    log.warning("Couldn't start %s (%s). Using the built-in browser.",
                                self.channel, str(e).splitlines()[0])
                    self._ctx = self._launch(None)
                self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
                if self.page.url in ("", "about:blank"):
                    self.page.goto(self.url)
        except Exception:
            self._pw.stop()
            raise
        return self

    def _find_game_tab(self):
        """In your own Chrome: use the tab showing the game, or open one."""
        pages = self._ctx.pages
        host = self.url.split("/")[2] if "//" in self.url else self.url
        for p in pages:
            if host and host in p.url:
                return p
        page = self._ctx.new_page()
        page.goto(self.url)
        return page

    def __exit__(self, *exc):
        try:
            if self.connect_url:
                self._browser.close()  # only disconnects; your Chrome stays open
            else:
                self._ctx.close()
        except Exception:
            pass
        finally:
            self._pw.stop()

    @property
    def alive(self) -> bool:
        try:
            return not self.page.is_closed() and bool(self._ctx.pages)
        except Exception:
            return False

    def screenshot(self, area=None) -> np.ndarray:
        if self.page.is_closed():
            # The tab was closed: use another one (or reopen the game).
            self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
            if self.page.url in ("", "about:blank"):
                self.page.goto(self.url)
        png = self.page.screenshot(timeout=15000)
        frame = cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR)
        if area:
            x, y, w, h = area
            frame = frame[y:y + h, x:x + w]
        return frame
