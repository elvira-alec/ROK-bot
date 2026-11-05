import yaml

from rokwatch import config
from rokwatch.notify import Notifier
from rokwatch.watcher import protect_points_from


def test_setup_saved_separately_keeps_comments(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text("# my comment\nenemy_watch:\n  enabled: true\n  start_radius: 70\n")
    config.save_fields({"enemy_watch.enemy_city": [5, 6], "game_area": [0, 0, 10, 10]}, str(cfg))
    assert "# my comment" in cfg.read_text()
    loaded = config.load(str(cfg))
    assert loaded["enemy_watch"] == {"enabled": True, "start_radius": 70, "enemy_city": [5, 6]}
    assert loaded["game_area"] == [0, 0, 10, 10]


def test_example_config_loads():
    cfg = yaml.safe_load(open("config.example.yaml"))
    assert cfg["notify"]["ntfy_server"].startswith("https://")
    assert cfg["enemy_watch"]["protect_points"] == []


def test_protect_points_formats():
    assert protect_points_from({"home": [1, 2]}) == [("your city", [1, 2])]
    assert protect_points_from({"home": [0, 0]}) == []
    pts = protect_points_from({"protect_points": [{"name": "farm", "pos": [3, 4]}, [5, 6]]})
    assert pts == [("farm", [3, 4]), ("spot 2", [5, 6])]


def test_cooldown():
    n = Notifier(cooldown_seconds=60, background=False)
    assert n.send("a", "t", "m")
    assert not n.send("a", "t", "m")
    assert n.send("b", "t", "m")
    assert n.send("a", "t", "m", cooldown=False)


import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import numpy as np
import pytest


@pytest.fixture
def inbox():
    got = []

    class H(BaseHTTPRequestHandler):
        def _rec(self):
            body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            got.append((self.command, self.path, dict(self.headers), body))
            self.send_response(200)
            self.end_headers()

        do_POST = do_PUT = _rec

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}", got
    srv.shutdown()


def test_ntfy_and_discord_formats(inbox, tmp_path):
    url, got = inbox
    img = tmp_path / "s.png"
    img.write_bytes(b"\x89PNG fake image bytes")
    n = Notifier("topic1", url, f"{url}/discord", background=False)
    n.send("k", "ENEMY MARCH ⚔", "toward your city", urgent=True, image_path=str(img))
    n.send("k2", "Plain", "no picture")
    ntfy = [g for g in got if g[1] == "/topic1"]
    disc = [g for g in got if g[1] == "/discord"]
    assert ntfy[0][0] == "PUT" and ntfy[0][2]["Filename"] == "screen.png"
    assert ntfy[0][2]["Priority"] == "urgent" and ntfy[0][2]["Title"] == "ENEMY MARCH "
    assert ntfy[0][3] == img.read_bytes()
    assert ntfy[1][0] == "POST" and ntfy[1][3] == b"no picture"
    assert b"@here" in disc[0][3] and b"screen.png" in disc[0][3]
    assert disc[1][2]["Content-Type"].startswith("application/json")


def test_send_failure_does_not_crash(monkeypatch):
    monkeypatch.setattr("time.sleep", lambda s: None)
    n = Notifier("t", "http://127.0.0.1:9", background=False)  # nothing listens there
    assert n.send("k", "t", "m")  # logged, retried, no exception


def test_restarts_after_browser_crash(monkeypatch, tmp_path):
    import rokwatch.__main__ as m
    import rokwatch.browser as b

    opened = []

    class FlakyBrowser:
        def __init__(self):
            opened.append(self)
            self.n = 0

        @classmethod
        def from_config(cls, cfg):
            return cls()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            pass

        alive = property(lambda self: len(opened) > 1)

        def screenshot(self, area=None):
            self.n += 1
            if len(opened) == 1 and self.n > 2:
                raise RuntimeError("Target page, context or browser has been closed")
            return np.full((300, 400, 3), (60, 140, 70), np.uint8)

    monkeypatch.setattr(b, "GameBrowser", FlakyBrowser)
    monkeypatch.setattr(m.time, "sleep", lambda s: None)
    cfg = {"game_url": "x", "enemy_watch": {"enabled": False}, "notify": {},
           "check_every_seconds": 0}
    (tmp_path / "config.yaml").write_text("{}")
    m.run(cfg, str(tmp_path / "config.yaml"), prompt=False, max_checks=5)
    assert len(opened) == 2  # crashed once, came back on its own


def test_expand_paths(tmp_path):
    from rokwatch.__main__ import expand_paths
    for n in ("000002.png", "000001.png", "notes.txt"):
        (tmp_path / n).write_text("x")
    expect = [str(tmp_path / "000001.png"), str(tmp_path / "000002.png")]
    assert expand_paths([str(tmp_path)]) == expect
    assert expand_paths([str(tmp_path / "*.png")]) == expect
    assert expand_paths(["a.png"]) == ["a.png"]
