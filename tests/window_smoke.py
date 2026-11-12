"""Windows only: open the test game as its own window (an Edge app window, no tabs)
and drive it with the built exe through window mode (screenshots + real mouse).
    python tests/window_smoke.py dist/RoKWatcher"""

import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
app_dir = Path(sys.argv[1]).resolve()
exe = app_dir / "RoKWatcher.exe"
edge = next(p for p in (Path(os.environ.get("ProgramFiles(x86)", "")) / "Microsoft/Edge/Application/msedge.exe",
                        Path(os.environ.get("ProgramFiles", "")) / "Microsoft/Edge/Application/msedge.exe")
            if p.exists())
page = (ROOT / "tests/fake_game/rok.html").as_uri()
prof = tempfile.mkdtemp()
win = subprocess.Popen([str(edge), f"--app={page}", "--window-size=1500,800",
                        "--window-position=0,0", f"--user-data-dir={prof}", "--no-first-run"])
try:
    time.sleep(8)
    out = subprocess.run([str(exe), "--window-selftest", "Fake RoK UI"], cwd=app_dir,
                         capture_output=True, text=True, timeout=300)
    print(out.stdout[-3000:], out.stderr[-3000:])
    res = json.loads([ln for ln in out.stdout.splitlines() if ln.startswith("{")][-1])
    assert res["before"] == [4183, 209, 418], res
    assert res["jumped"] and res["after"] == [4183, 162, 374], res
    print("OK")
finally:
    win.kill()
    subprocess.run(["taskkill", "/F", "/IM", "msedge.exe"], capture_output=True)
