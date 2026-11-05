"""Send alerts to your phone (ntfy) and/or Discord."""

from __future__ import annotations

import time

import requests


class Notifier:
    def __init__(self, ntfy_topic="", ntfy_server="https://ntfy.sh",
                 discord_webhook="", cooldown_seconds=90):
        self.ntfy_url = f"{ntfy_server.rstrip('/')}/{ntfy_topic}" if ntfy_topic else ""
        self.discord = discord_webhook
        self.cooldown = cooldown_seconds
        self._last_sent = {}

    def send(self, key: str, title: str, message: str, urgent=False,
             image_path: str | None = None) -> bool:
        """Send unless the same kind of alert went out within the cooldown."""
        now = time.time()
        if now - self._last_sent.get(key, 0) < self.cooldown:
            return False
        self._last_sent[key] = now
        print(f"[ALERT] {title}: {message}")
        if self.ntfy_url:
            try:
                headers = {"Title": title, "Priority": "urgent" if urgent else "high",
                           "Tags": "rotating_light" if urgent else "eyes"}
                if image_path:
                    headers["Filename"] = "screen.png"
                    headers["Message"] = message
                    with open(image_path, "rb") as f:
                        requests.put(self.ntfy_url, data=f, headers=headers, timeout=15)
                else:
                    requests.post(self.ntfy_url, data=message.encode(), headers=headers,
                                  timeout=10)
            except Exception as e:  # keep watching even if a send fails
                print(f"  ntfy failed: {e}")
        if self.discord:
            try:
                requests.post(self.discord, json={"content": f"**{title}**\n{message}"},
                              timeout=10)
            except Exception as e:
                print(f"  discord failed: {e}")
        return True
