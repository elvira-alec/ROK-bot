"""Windows build check: the real updater window installs a zip over an old copy.

    python tests/updater_smoke.py RoKWatcher-windows.zip

Pretends an old RoKWatcher (with your settings and snapshots) is installed, runs the
updater against the zip (served locally), and checks: the program files were
replaced, your files are still there, and the new version was started again."""

import http.server
import shutil
import socketserver
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rokwatch import updater  # noqa: E402

zip_path = Path(sys.argv[1]).resolve()
work = Path(tempfile.mkdtemp())
app = work / "RoKWatcher"
with zipfile.ZipFile(zip_path) as z:
    z.extractall(work)
new_version = (app / "_internal" / "rokwatch" / "version.txt").read_text().strip()
# make it look like an old install with the user's own files
(app / "_internal" / "rokwatch" / "version.txt").write_text("0.9")
(app / "config.yaml").write_text("notify: {ntfy_topic: keep-me}\n")
(app / "snapshots").mkdir()
(app / "snapshots" / "mine.jpg").write_bytes(b"x")

# serve the zip
handler = lambda *a, **k: http.server.SimpleHTTPRequestHandler(*a, directory=str(zip_path.parent), **k)
srv = socketserver.TCPServer(("127.0.0.1", 0), handler)
threading.Thread(target=srv.serve_forever, daemon=True).start()
url = f"http://127.0.0.1:{srv.server_address[1]}/{zip_path.name}"

# a stand-in for the running RoKWatcher that closes after a moment
dummy = subprocess.Popen(["ping", "-n", "4", "127.0.0.1"], stdout=subprocess.DEVNULL)
script = work / "upd.ps1"
script.write_text(updater.UPDATER_PS1, encoding="utf-8-sig")
t0 = time.time()
r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script),
                    "-WaitPid", str(dummy.pid), "-Url", url, "-AppDir", str(app),
                    "-Version", new_version], timeout=600)
print("updater finished in", round(time.time() - t0), "s, exit", r.returncode)

got = (app / "_internal" / "rokwatch" / "version.txt").read_text().strip()
assert got == new_version, ("program files not replaced", got, new_version)
assert "keep-me" in (app / "config.yaml").read_text(), "settings were lost"
assert (app / "snapshots" / "mine.jpg").exists(), "snapshots were lost"
# it started the new version again ("--updated")
time.sleep(5)
out = subprocess.run(["powershell", "-NoProfile", "-Command",
                      "Get-CimInstance Win32_Process -Filter \"Name='RoKWatcher.exe'\" | "
                      "Select-Object -ExpandProperty CommandLine"], capture_output=True, text=True).stdout
print("running:", out.strip())
assert "--updated" in out, "the new version wasn't started"
subprocess.run(["taskkill", "/F", "/IM", "RoKWatcher.exe"], capture_output=True)
srv.shutdown()
shutil.rmtree(work, ignore_errors=True)
print("OK: updated", "0.9 ->", new_version)
