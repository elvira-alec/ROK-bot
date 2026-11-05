"""Browser modes and the full program with recording. Kept apart from
test_end_to_end.py because only one Playwright instance can run per thread."""

import copy
import os
import subprocess
import sys
import time

import pytest
import yaml

pytest.importorskip("playwright")

from rokwatch.browser import GameBrowser  # noqa: E402
from test_end_to_end import AREA, BASE_CFG, EXE, PAGE, ROOT, VIEW  # noqa: E402


def test_attach_to_own_chrome(tmp_path):
    """connect_to_chrome: watcher uses a Chrome the user started themselves."""
    import socket
    if not EXE:
        pytest.skip("needs a chromium binary path")
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    chrome = subprocess.Popen([EXE, "--headless=new", "--no-sandbox", f"--remote-debugging-port={port}",
                               f"--user-data-dir={tmp_path / 'own'}", "--window-size=1200,740",
                               PAGE], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=0.2).close()
                break
            except OSError:
                time.sleep(0.2)
        gb = GameBrowser(PAGE, "unused", VIEW, connect_url=f"http://127.0.0.1:{port}")
        with gb:
            assert "fake_game" in gb.page.url  # picked the existing game tab
            gb.page.wait_for_function("window.sim !== undefined")
            frame = gb.screenshot()
            assert frame.shape[0] > 100 and frame.shape[1] > 100
        assert chrome.poll() is None  # disconnecting left the user's Chrome running
    finally:
        chrome.kill()


def test_falls_back_when_chrome_missing(tmp_path, monkeypatch):
    calls = []
    real = GameBrowser._launch

    def fake_launch(self, channel):
        calls.append(channel)
        if channel in ("chrome", "msedge"):
            raise RuntimeError(f"Chromium distribution '{channel}' is not found")
        self.executable_path = EXE
        return real(self, None)

    monkeypatch.setattr(GameBrowser, "_launch", fake_launch)
    with GameBrowser(PAGE, str(tmp_path / "p"), VIEW, headless=True, channel="chrome") as gb:
        assert gb.screenshot() is not None
    assert calls == ["chrome", "msedge", None]


def test_command_line_march_record_and_replay(tmp_path):
    """Full program: setup.yaml + calibration.png -> march alert; then --check replay."""
    import cv2
    with GameBrowser(PAGE + "#notraffic", str(tmp_path / "p0"), VIEW, headless=True,
                     executable_path=EXE) as gb:
        gb.page.wait_for_function("window.sim !== undefined")
        time.sleep(0.3)
        pos = gb.page.evaluate("[sim.screenPos('enemy'), sim.screenPos('home')]")
        cv2.imwrite(str(tmp_path / "calibration.png"), gb.screenshot(AREA))
    cfg = copy.deepcopy(BASE_CFG)
    cfg.update(game_url=PAGE + "#scenario=march&after=5000", headless=True,
               viewport=list(VIEW), browser_profile=str(tmp_path / "prof"),
               browser_executable=EXE or "", browser_channel="", startup_wait_seconds=1,
               check_every_seconds=0.5)
    cfg["notify"]["ntfy_topic"] = ""
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    (tmp_path / "setup.yaml").write_text(yaml.safe_dump({
        "game_area": AREA,
        "enemy_watch": {"enemy_city": pos[0],
                        "protect_points": [{"name": "your city", "pos": pos[1]}]}}))
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    live = subprocess.run([sys.executable, "-m", "rokwatch", "--config", "config.yaml",
                           "--no-prompt", "--max-checks", "18", "--record"],
                          cwd=tmp_path, env=env, capture_output=True, text=True, timeout=120)
    assert live.returncode == 0, live.stdout + live.stderr
    assert "ENEMY MARCH HEADING YOUR WAY" in live.stdout, live.stdout
    shots = sorted(str(p) for p in (tmp_path / "recordings").glob("*.png"))
    assert len(shots) == 18
    # recorded frames are already cropped to the game area
    (tmp_path / "setup.yaml").write_text(yaml.safe_dump({
        "game_area": None,
        "enemy_watch": {"enemy_city": pos[0],
                        "protect_points": [{"name": "your city", "pos": pos[1]}]}}))
    replay = subprocess.run([sys.executable, "-m", "rokwatch", "--config", "config.yaml",
                             "--check", *shots], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=120)
    assert replay.returncode == 0, replay.stdout + replay.stderr
    assert "ENEMY MARCH HEADING YOUR WAY" in replay.stdout, replay.stdout
    assert "map line-up" in replay.stdout


def test_command_line_patrol_alerts_and_recalls(tmp_path):
    """--patrol: finds his march, works out it's heading for your camp, alerts, recalls."""
    pytest.importorskip("rapidocr_onnxruntime")
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer
    got = []

    class Inbox(BaseHTTPRequestHandler):
        def _rec(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            got.append(self.headers.get("Title", ""))
            self.send_response(200)
            self.end_headers()
        do_POST = do_PUT = _rec

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), Inbox)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    page = (ROOT / "tests/fake_game/rok.html").as_uri() + "#march=enemy,camp,white"
    cfg = copy.deepcopy(BASE_CFG)
    cfg.update(game_url=page, headless=True, viewport=[1434, 660],
               browser_profile=str(tmp_path / "prof"), browser_executable=EXE or "",
               browser_channel="", startup_wait_seconds=1)
    cfg["notify"].update(ntfy_server=f"http://127.0.0.1:{srv.server_port}", ntfy_topic="t")
    cfg["patrol"].update(auto_recall=True, settle_seconds=0.5, pause_between_rounds=0)
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    import numpy as np
    axes = np.linalg.inv(np.array([[24, 10], [-8, 20]], float)).round(5).tolist()
    (tmp_path / "setup.yaml").write_text(yaml.safe_dump({"patrol": {"axes": axes}}))
    proc = subprocess.run([sys.executable, "-m", "rokwatch", "--config", "config.yaml",
                           "--patrol", "--no-prompt", "--max-checks", "1"], cwd=tmp_path,
                          env={**os.environ, "PYTHONPATH": str(ROOT)},
                          capture_output=True, text=True, timeout=300)
    time.sleep(2)
    srv.shutdown()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "RoK patrol started" in got, (got, proc.stdout)
    assert "ENEMY MARCH HEADING YOUR WAY" in got, (got, proc.stdout)
    assert "Auto-recall" in got, (got, proc.stdout)
    log = (tmp_path / "activity.csv").read_text()
    assert "his city,enemy,march" in log and "auto_recall" in log, log
