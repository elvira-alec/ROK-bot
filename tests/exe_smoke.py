"""Smoke test for the built app (used by the Windows build on GitHub):
    python tests/exe_smoke.py dist/RoKWatcher [browser_channel]
Runs the self-test, then starts the control panel, opens the test game page
and checks the bot reads it."""

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
app_dir = Path(sys.argv[1]).resolve()
channel = sys.argv[2] if len(sys.argv) > 2 else "msedge"
exe = next(p for p in (app_dir / "RoKWatcher.exe", app_dir / "RoKWatcher") if p.exists())

out = subprocess.run([str(exe), "--selftest", str(ROOT / "tests/real_screens/search_bar_marching.png")],
                     capture_output=True, text=True, timeout=300)
last = [ln for ln in out.stdout.splitlines() if ln.startswith("{")]
assert last, out.stdout + out.stderr
res = json.loads(last[-1])
print("selftest:", res)
assert res["camera"] == [4183, 173, 378] and res["troops"][:3] == ["marching"] * 3, res

cfg = yaml.safe_load((app_dir / "config.example.yaml").read_text())
cfg.update(game_url=(ROOT / "tests/fake_game/rok.html").as_uri(), headless=True,
           viewport=[1434, 660], browser_channel=channel)
if not channel:  # e.g. on Linux: a chromium binary given by path
    import os
    cfg["browser_executable"] = os.environ.get("ROKWATCH_TEST_CHROME", "/opt/pw-browsers/chromium")
(app_dir / "config.yaml").write_text(yaml.safe_dump(cfg))
proc = subprocess.Popen([str(exe)], cwd=app_dir)


def call(path, post=False):
    req = urllib.request.Request("http://127.0.0.1:8765" + path, data=b"{}" if post else None,
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=10).read())


try:
    for _ in range(60):
        try:
            call("/api/settings")
            break
        except OSError:
            time.sleep(1)
    page = urllib.request.urlopen("http://127.0.0.1:8765/", timeout=10).read()
    assert b"RoK Watcher" in page
    call("/api/open", post=True)
    for _ in range(60):
        if call("/api/state")["phase"] == "ready":
            break
        time.sleep(1)
    call("/api/look", post=True)
    look = None
    for _ in range(120):
        look = call("/api/state")["look"]
        if look:
            break
        time.sleep(1)
    print("panel look:", look, call("/api/state")["message"])
    assert look and look["camera"] == [4183, 209, 418], call("/api/state")
    assert look["troops"][:3] == ["gathering"] * 3, look
    print("OK")
finally:
    proc.kill()
