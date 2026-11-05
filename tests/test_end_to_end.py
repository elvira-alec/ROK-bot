"""Runs the real watcher against the fake game page in a real (headless) browser."""

import copy
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

pytest.importorskip("playwright")

from rokwatch.browser import GameBrowser  # noqa: E402
from rokwatch.watcher import Watcher  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
PAGE = (ROOT / "tests/fake_game/index.html").as_uri()
EXE = os.environ.get("ROKWATCH_TEST_CHROME") or (
    "/opt/pw-browsers/chromium" if Path("/opt/pw-browsers/chromium").exists() else None)
VIEW = (1200, 740)
AREA = [0, 40, 1200, 700]
BASE_CFG = yaml.safe_load((ROOT / "config.example.yaml").read_text())


@pytest.fixture(scope="module")
def browser(tmp_path_factory):
    prof = tmp_path_factory.mktemp("profile")
    with GameBrowser(PAGE, str(prof), VIEW, headless=True, executable_path=EXE) as gb:
        gb.page.wait_for_function("window.sim !== undefined")
        yield gb


@pytest.fixture
def game(browser):
    browser.page.evaluate("sim.clearMarches(); sim.attack(false); sim.menu(false); "
                          "sim.freeze(false)")
    browser.page.reload()
    browser.page.wait_for_function("window.sim !== undefined")
    time.sleep(0.3)
    return browser


def make_watcher(gb, tmp_path, **overrides):
    pos = gb.page.evaluate("[sim.screenPos('enemy'), sim.screenPos('home'), sim.screenPos('farm')]")
    cfg = copy.deepcopy(BASE_CFG)
    cfg["game_area"] = AREA
    cfg["enemy_watch"]["enemy_city"] = pos[0]
    cfg["enemy_watch"]["protect_points"] = [{"name": "your city", "pos": pos[1]},
                                            {"name": "farm", "pos": pos[2]}]
    for k, v in overrides.items():
        cfg[k] = {**cfg.get(k, {}), **v} if isinstance(v, dict) else v
    ref = gb.screenshot(AREA)
    return Watcher(cfg, ref, debug_dir=str(tmp_path / "debug"))


def run_steps(gb, watcher, n, every=0.3):
    out = []
    for _ in range(n):
        out += watcher.step(gb.screenshot(AREA))
        time.sleep(every)
    return out


def titles(alerts):
    return [a.title for a in alerts]


def test_quiet_map_no_alerts(game, tmp_path):
    w = make_watcher(game, tmp_path)
    alerts = run_steps(game, w, 25)
    assert alerts == [], titles(alerts)


def test_march_toward_city_alerts_once(game, tmp_path):
    w = make_watcher(game, tmp_path)
    assert run_steps(game, w, 7) == []
    game.page.evaluate("sim.march('enemy', 'home')")
    alerts = run_steps(game, w, 12)
    toward = [a for a in alerts if "YOUR WAY" in a.title]
    assert len(toward) == 1, titles(alerts)
    assert "your city" in toward[0].message
    assert Path(toward[0].image).exists()
    assert not [a for a in alerts if "Enemy sent" in a.title], titles(alerts)


def test_march_toward_farm_named(game, tmp_path):
    w = make_watcher(game, tmp_path)
    run_steps(game, w, 7)
    game.page.evaluate("sim.march('enemy', 'farm', '#f0f0f0')")
    alerts = run_steps(game, w, 8)
    toward = [a for a in alerts if "YOUR WAY" in a.title]
    assert toward and "farm" in toward[0].message, titles(alerts)


def test_march_elsewhere_is_low_priority(game, tmp_path):
    w = make_watcher(game, tmp_path)
    run_steps(game, w, 7)
    game.page.evaluate("sim.marchAngle(250, 380)")  # up and left, away from both spots
    alerts = run_steps(game, w, 8)
    assert [a for a in alerts if a.title == "Enemy sent a march"], titles(alerts)
    assert not [a for a in alerts if "YOUR WAY" in a.title], titles(alerts)


def test_march_passing_through_his_city_ignored(game, tmp_path):
    w = make_watcher(game, tmp_path)
    run_steps(game, w, 7)
    game.page.evaluate("sim.passBy(30, 400)")  # goes right over his city toward you
    alerts = run_steps(game, w, 8)
    assert alerts == [], titles(alerts)


def test_two_marches_two_alerts(game, tmp_path):
    w = make_watcher(game, tmp_path)
    run_steps(game, w, 7)
    game.page.evaluate("sim.march('enemy', 'home')")
    run_steps(game, w, 4)
    game.page.evaluate("sim.march('enemy', 'farm')")
    alerts = run_steps(game, w, 6)
    toward = [a for a in alerts if "YOUR WAY" in a.title]
    assert len(toward) == 1 and "farm" in toward[0].message, titles(alerts)


def test_attack_flash(game, tmp_path):
    w = make_watcher(game, tmp_path)
    run_steps(game, w, 5)
    game.page.evaluate("sim.attack(true)")
    alerts = run_steps(game, w, 6)
    attack = [a for a in alerts if a.key == "attack"]
    assert attack and attack[0].urgent, titles(alerts)


def test_follows_nudged_map(game, tmp_path):
    w = make_watcher(game, tmp_path)
    run_steps(game, w, 6)
    game.page.evaluate("sim.pan(45, -30)")
    run_steps(game, w, 8)
    assert abs(w.offset[0] + 45) < 4 and abs(w.offset[1] - 30) < 4, w.offset
    game.page.evaluate("sim.march('enemy', 'home')")
    alerts = run_steps(game, w, 10)
    toward = [a for a in alerts if "YOUR WAY" in a.title]
    assert toward and "your city" in toward[0].message, titles(alerts)


def test_menu_open_warns_lost(game, tmp_path):
    w = make_watcher(game, tmp_path, map_tracking={"lost_after_checks": 5})
    run_steps(game, w, 6)
    game.page.evaluate("sim.menu(true)")
    alerts = run_steps(game, w, 8, every=0.1)
    assert [a for a in alerts if a.key == "lost"], titles(alerts)
    game.page.evaluate("sim.menu(false)")
    alerts = run_steps(game, w, 4, every=0.1)
    assert [a for a in alerts if a.key == "found"], titles(alerts)


def test_frozen_stream(game, tmp_path):
    w = make_watcher(game, tmp_path, frozen_alert_minutes=0.02)  # ~1 second
    game.page.evaluate("sim.freeze(true)")
    time.sleep(0.3)
    alerts = run_steps(game, w, 8)
    assert [a for a in alerts if a.key == "frozen"], titles(alerts)


# ---------------------------------------------------------------- full program

class _Inbox(BaseHTTPRequestHandler):
    got = []

    def _record(self):
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        _Inbox.got.append({"path": self.path, "title": self.headers.get("Title", ""),
                           "file": self.headers.get("Filename"), "size": len(body)})
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    do_POST = do_PUT = _record

    def log_message(self, *a):
        pass


def test_command_line_sends_alerts(tmp_path):
    server = HTTPServer(("127.0.0.1", 0), _Inbox)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    _Inbox.got = []
    cfg = copy.deepcopy(BASE_CFG)
    cfg.update(game_url=PAGE + "#scenario=attack&after=3000", headless=True,
               viewport=list(VIEW), browser_profile=str(tmp_path / "prof"),
               browser_executable=EXE or "", browser_channel="", startup_wait_seconds=1, check_every_seconds=0.5,
               game_area=AREA)
    cfg["notify"].update(ntfy_server=f"http://127.0.0.1:{server.server_port}",
                         ntfy_topic="test-topic")
    cfg["enemy_watch"]["enabled"] = False
    (tmp_path / "config.yaml").write_text(yaml.safe_dump(cfg))
    proc = subprocess.run([sys.executable, "-m", "rokwatch", "--config",
                           str(tmp_path / "config.yaml"), "--no-prompt", "--max-checks", "14"],
                          cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(ROOT)},
                          capture_output=True, text=True, timeout=120)
    time.sleep(1.5)  # alerts go out in the background
    server.shutdown()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    got_titles = [g["title"] for g in _Inbox.got]
    assert "RoK watcher started" in got_titles, (got_titles, proc.stdout)
    attack = [g for g in _Inbox.got if g["title"] == "YOU ARE UNDER ATTACK"]
    assert attack and attack[0]["file"] == "screen.png" and attack[0]["size"] > 10000, \
        (_Inbox.got, proc.stdout)
    assert all(g["path"] == "/test-topic" for g in _Inbox.got)

