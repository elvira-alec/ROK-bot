"""Windows build check: a real update, the way the panel does it.

    python tests/updater_smoke.py RoKWatcher-windows.zip

An "old" RoKWatcher (with your settings and snapshots) runs `--test-update URL`:
that starts the separate updater program and closes itself, exactly like pressing
Update now. Checks: the updater survived RoKWatcher closing, replaced the program
files, kept your files, and RoKWatcher came back (the panel answers with the new
version) - then quits it."""

import http.server
import json
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import zipfile
from pathlib import Path

zip_path = Path(sys.argv[1]).resolve()
work = Path(tempfile.mkdtemp())
app = work / "RoKWatcher"
with zipfile.ZipFile(zip_path) as z:
    z.extractall(work)
vfile = app / "_internal" / "rokwatch" / "version.txt"
new_version = vfile.read_text().strip()
assert (app / "RoKWatcherUpdater.exe").exists(), "the updater program isn't in the download"
vfile.write_text("0.9")                                   # pretend it's an old install
(app / "config.yaml").write_text("notify: {ntfy_topic: keep-me}\n")
(app / "snapshots").mkdir()
(app / "snapshots" / "mine.jpg").write_bytes(b"x")

handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(zip_path.parent), **k)
srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/{zip_path.name}"

t0 = time.time()
r = subprocess.run([str(app / "RoKWatcher.exe"), "--test-update", url, new_version], cwd=app, timeout=60)
print("old RoKWatcher handed over and closed, exit", r.returncode)


def state():
    try:
        with urllib.request.urlopen("http://127.0.0.1:8765/api/state", timeout=2) as resp:
            return json.loads(resp.read())
    except OSError:
        return None


st = None
for _ in range(240):                                      # up to 4 minutes
    if vfile.read_text().strip() == new_version:
        st = state()
        if st and st.get("version") == new_version:
            break
    time.sleep(1)
log = Path(tempfile.gettempdir()) / "rokwatch-update.log"
print("update log:\n" + (log.read_text(errors="replace") if log.exists() else "(none)"))
assert vfile.read_text().strip() == new_version, "program files weren't replaced"
assert "keep-me" in (app / "config.yaml").read_text(), "settings were lost"
assert (app / "snapshots" / "mine.jpg").exists(), "snapshots were lost"
assert st and st["version"] == new_version, ("the new version didn't start", st)
print(f"OK: 0.9 -> {new_version} in {time.time() - t0:.0f} s, panel back")
try:
    urllib.request.urlopen(urllib.request.Request("http://127.0.0.1:8765/api/quit", data=b"{}",
                                                  headers={"Content-Type": "application/json"}), timeout=5)
except OSError:
    pass
srv.shutdown()
time.sleep(2)
shutil.rmtree(work, ignore_errors=True)
