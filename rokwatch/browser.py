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
        if cfg.get("game_window"):
            # the game runs as its own program (PC game / BlueStacks), not in a browser
            from .desktop import DesktopWindow
            return DesktopWindow.from_config(cfg)
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
                # Chrome if you have it, else Edge (comes with Windows), else the
                # browser Playwright downloaded.
                tries = [self.channel] if self.executable_path or not self.channel else \
                    list(dict.fromkeys([self.channel, "msedge", None]))
                for i, ch in enumerate(tries):
                    try:
                        self._ctx = self._launch(ch)
                        break
                    except Exception as e:
                        if i == len(tries) - 1:
                            raise
                        log.warning("Couldn't start %s (%s). Trying %s.", ch,
                                    str(e).splitlines()[0], tries[i + 1] or "the built-in browser")
                self.page = self._ctx.pages[0] if self._ctx.pages else self._ctx.new_page()
                if self.page.url in ("", "about:blank") and self.url:
                    self.page.goto(self.url)
        except Exception:
            self._pw.stop()
            raise
        return self

    def _find_game_tab(self):
        """In your own Chrome: use the tab showing the game, or open one."""
        pages = self._ctx.pages
        want = self.url.split("#")[0]
        host = self.url.split("/")[2] if "//" in self.url else self.url
        for p in pages:
            if p.url.split("#")[0] == want or (host and host in p.url):
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

    def find_game_area(self):
        """Where the game picture is on the page: the biggest <canvas> or <video>
        (also inside frames, as cloud-gaming sites use). Returns
        ([x, y, w, h], clipped) in page pixels, or (None, False) if not found.
        clipped = the picture doesn't fully fit in the window."""
        best, best_area = None, 0
        for frame in self.page.frames:
            try:
                els = frame.query_selector_all("canvas, video")
            except Exception:
                continue
            for el in els:
                try:
                    box = el.bounding_box()   # page coordinates, even inside frames
                except Exception:
                    box = None
                if not box or box["width"] < 300 or box["height"] < 200:
                    continue
                area = box["width"] * box["height"]
                if area > best_area:
                    best, best_area = box, area
        if not best:
            return None, False
        vw, vh = self.page.evaluate("[innerWidth, innerHeight]")
        x0, y0 = max(0, best["x"]), max(0, best["y"])
        x1, y1 = min(vw, best["x"] + best["width"]), min(vh, best["y"] + best["height"])
        clipped = (x0 > best["x"] + 1 or y0 > best["y"] + 1 or
                   x1 < best["x"] + best["width"] - 1 or y1 < best["y"] + best["height"] - 1)
        return [int(x0), int(y0), int(x1 - x0), int(y1 - y0)], clipped

    @property
    def alive(self) -> bool:
        try:
            return not self.page.is_closed() and bool(self._ctx.pages)
        except Exception:
            return False

    # ---- input. Positions are in screenshot pixels of the game area.
    def set_area(self, area):
        self._area = area

    def _abs(self, x, y):
        ax, ay = (self._area[0], self._area[1]) if getattr(self, "_area", None) else (0, 0)
        return x + ax, y + ay

    def click(self, x, y):
        self.page.mouse.click(*self._abs(x, y))

    def press(self, key):
        self.page.keyboard.press(key)

    def type(self, text):
        self.page.keyboard.type(str(text), delay=15)

    def wheel(self, x, y, dy):
        self.page.mouse.move(*self._abs(x, y))
        self.page.mouse.wheel(0, dy)

    def drag(self, x0, y0, x1, y1, steps=15):
        self.page.mouse.move(*self._abs(x0, y0))
        self.page.mouse.down()
        self.page.mouse.move(*self._abs(x1, y1), steps=steps)
        self.page.mouse.up()

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
