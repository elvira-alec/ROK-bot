"""Control panel: settings, and driving the test game through the web API."""

import copy
import json
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import yaml

pytest.importorskip("playwright")
pytest.importorskip("rapidocr_onnxruntime")

from rokwatch.panel import Panel, make_handler  # noqa: E402
from test_end_to_end import BASE_CFG, EXE  # noqa: E402

PAGE = (Path(__file__).resolve().parent / "fake_game/rok.html").as_uri()


@pytest.fixture
def panel(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "debug").mkdir()
    cfg = copy.deepcopy(BASE_CFG)
    cfg.update(game_url=PAGE, headless=True, viewport=[1434, 660],
               browser_executable=EXE or "", browser_channel="")
    (tmp_path / "config.yaml").write_text("# my comments\n" + yaml.safe_dump(cfg))
    p = Panel(str(tmp_path / "config.yaml"))
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(p))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"

    def call(path, body=None):
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
        raw = urllib.request.urlopen(req, timeout=10).read()
        return json.loads(raw) if raw[:1] in (b"{", b"[") else raw

    call.base = base
    yield p, call, tmp_path
    p.worker.send("quit")
    srv.shutdown()


def wait_for(call, cond, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        st = call("/api/state")
        if cond(st):
            return st
        time.sleep(0.5)
    raise AssertionError(call("/api/state"))


def test_page_and_settings(panel):
    p, call, tmp = panel
    assert b"RoK Watcher" in call("/")
    call("/api/settings", {"ntfy_topic": " my-topic ", "auto_recall": True, "recall_scope": "all",
                           "sites": [{"name": "his city", "x": "162", "y": "374", "kind": "enemy"},
                                     {"name": "", "x": "205", "y": "424", "kind": "mine"},
                                     {"name": "bad", "x": "abc", "y": "1"}]})
    s = call("/api/settings")
    assert s["ntfy_topic"] == "my-topic" and s["auto_recall"] is True and s["recall_scope"] == "all"
    assert s["sites"] == [{"name": "his city", "x": 162, "y": 374, "kind": "enemy"},
                          {"name": "205,424", "x": 205, "y": 424, "kind": "mine"}]
    assert "# my comments" in (tmp / "config.yaml").read_text()  # your config file is untouched


def test_rejects_other_websites(panel):
    # a web page on another site must not be able to press the panel's buttons
    import http.client
    p, call, tmp = panel
    conn = http.client.HTTPConnection("127.0.0.1", int(call.base.rsplit(":", 1)[1]), timeout=5)
    conn.request("POST", "/api/recall", body="{}",
                 headers={"Origin": "https://evil.example", "Content-Type": "application/json"})
    assert conn.getresponse().status == 403


def test_open_look_jump_and_patrol(panel):
    p, call, tmp = panel
    call("/api/open", {})
    wait_for(call, lambda s: s["phase"] == "ready")
    call("/api/look", {})
    st = wait_for(call, lambda s: s["look"] is not None)
    assert st["look"]["camera"] == [4183, 209, 418]
    assert st["look"]["troops"][:3] == ["gathering"] * 3
    assert call("/view.png")[:4] == b"\x89PNG"
    call("/api/goto", {"x": 162, "y": 374})
    wait_for(call, lambda s: "Jumped to 162,374" in s["message"])
    call("/api/settings", {"sites": [{"name": "his city", "x": 162, "y": 374, "kind": "enemy"}]})
    call("/api/start", {})
    wait_for(call, lambda s: s["round"] >= 1, timeout=120)
    call("/api/stop", {})
    st = wait_for(call, lambda s: s["phase"] == "ready")
    assert "Patrol stopped" in st["message"]


def test_hunt_from_panel(panel):
    import numpy as np
    p, call, tmp = panel
    axes = np.linalg.inv(np.array([[24, 10], [-8, 20]], float)).tolist()
    from rokwatch import config
    config.save_fields({"hunt.axes_close": axes, "patrol.axes": axes}, str(tmp / "config.yaml"))
    call("/api/settings", {"player_name": "[P83L]JONH WICK", "hunt_radius": "20",
                           "game_url": PAGE + "#farmer=155,368",
                           "sites": [{"name": "his city", "x": 162, "y": 374, "kind": "enemy"},
                                     {"name": "logging camp", "x": 205, "y": 424, "kind": "mine"}]})
    assert call("/api/settings")["axes_learned"]
    call("/api/open", {})
    wait_for(call, lambda s: s["phase"] == "ready")
    call("/api/hunt", {})
    st = wait_for(call, lambda s: s["round"] >= 1 and s["armies"], timeout=120)
    assert st["armies"][0]["state"] == "parked / gathering"
    assert abs(st["armies"][0]["x"] - 155) <= 2 and abs(st["armies"][0]["y"] - 368) <= 2
    call("/api/stop", {})
    wait_for(call, lambda s: s["phase"] == "ready")
