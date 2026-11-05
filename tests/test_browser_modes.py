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
        if channel == "chrome":
            raise RuntimeError("Chromium distribution 'chrome' is not found")
        self.executable_path = EXE
        return real(self, None)

    monkeypatch.setattr(GameBrowser, "_launch", fake_launch)
    with GameBrowser(PAGE, str(tmp_path / "p"), VIEW, headless=True, channel="chrome") as gb:
        assert gb.screenshot() is not None
    assert calls == ["chrome", None]


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
