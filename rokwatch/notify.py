"""Send alerts to your phone (ntfy), Discord and/or Telegram."""

from __future__ import annotations

import logging
import threading
import time

import requests

log = logging.getLogger("rokwatch")


def _ascii(s: str) -> str:
    # HTTP headers must be plain text.
    return s.encode("ascii", "ignore").decode()


class Notifier:
    def __init__(self, ntfy_topic="", ntfy_server="https://ntfy.sh", discord_webhook="",
                 cooldown_seconds=90, telegram_bot_token="", telegram_chat_id="",
                 background=True):
        self.ntfy_url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}" if ntfy_topic else ""
        self.discord = discord_webhook
        self.tg_token, self.tg_chat = telegram_bot_token, str(telegram_chat_id or "")
        self.cooldown = cooldown_seconds
        self.background = background
        self._last_sent = {}

    @classmethod
    def from_config(cls, n: dict, background=True):
        return cls(n.get("ntfy_topic", ""), n.get("ntfy_server", "https://ntfy.sh"),
                   n.get("discord_webhook", ""), n.get("cooldown_seconds", 90),
                   n.get("telegram_bot_token", ""), n.get("telegram_chat_id", ""), background)

    @property
    def has_target(self) -> bool:
        return bool(self.ntfy_url or self.discord or (self.tg_token and self.tg_chat))

    def send(self, key: str, title: str, message: str, urgent=False,
             image_path: str | None = None, cooldown=True) -> bool:
        """Send unless the same kind of alert went out within the cooldown."""
        now = time.time()
        if cooldown and now - self._last_sent.get(key, -1e9) < self.cooldown:
            return False
        self._last_sent[key] = now
        log.warning("ALERT %s: %s", title, message)
        args = (title, message, urgent, image_path)
        if self.background:  # a slow network shouldn't delay the next check
            threading.Thread(target=self._deliver, args=args, daemon=True).start()
        else:
            self._deliver(*args)
        return True

    def _deliver(self, title, message, urgent, image_path):
        for name, fn in (("ntfy", self._ntfy), ("discord", self._discord),
                         ("telegram", self._telegram)):
            for attempt in range(3):
                try:
                    fn(title, message, urgent, image_path)
                    break
                except Exception as e:
                    log.error("%s send failed (try %d): %s", name, attempt + 1, e)
                    time.sleep(2 * (attempt + 1))

    def _ntfy(self, title, message, urgent, image_path):
        if not self.ntfy_url:
            return
        headers = {"Title": _ascii(title), "Priority": "urgent" if urgent else "high",
                   "Tags": "rotating_light" if urgent else "eyes"}
        if image_path:
            headers.update({"Filename": "screen.png", "Message": _ascii(message)})
            with open(image_path, "rb") as f:
                r = requests.put(self.ntfy_url, data=f, headers=headers, timeout=20)
        else:
            r = requests.post(self.ntfy_url, data=message.encode(), headers=headers, timeout=10)
        r.raise_for_status()

    def _discord(self, title, message, urgent, image_path):
        if not self.discord:
            return
        content = f"{'@here ' if urgent else ''}**{title}**\n{message}"
        if image_path:
            with open(image_path, "rb") as f:
                r = requests.post(self.discord, data={"content": content},
                                  files={"file": ("screen.png", f, "image/png")}, timeout=20)
        else:
            r = requests.post(self.discord, json={"content": content}, timeout=10)
        r.raise_for_status()

    def _telegram(self, title, message, urgent, image_path):
        if not (self.tg_token and self.tg_chat):
            return
        base = f"https://api.telegram.org/bot{self.tg_token}"
        text = f"{title}\n{message}"
        if image_path:
            with open(image_path, "rb") as f:
                r = requests.post(f"{base}/sendPhoto", data={"chat_id": self.tg_chat,
                                  "caption": text}, files={"photo": f}, timeout=20)
        else:
            r = requests.post(f"{base}/sendMessage",
                              data={"chat_id": self.tg_chat, "text": text}, timeout=10)
        r.raise_for_status()
