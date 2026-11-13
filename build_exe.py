"""Builds RoKWatcher (an .exe on Windows) with PyInstaller:  python build_exe.py"""

import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

sep = ";" if os.name == "nt" else ":"
if not Path("rokwatch/version.txt").exists():      # the release build writes the real number
    Path("rokwatch/version.txt").write_text("dev")
PyInstaller.__main__.run([
    "rokwatch_app.py", "--name", "RoKWatcher", "--noconfirm", "--console",
    "--collect-all", "rapidocr_onnxruntime",   # text-reading models
    "--collect-all", "playwright",              # browser control
    "--collect-binaries", "onnxruntime",
    "--add-data", f"rokwatch/panel.html{sep}rokwatch",
    "--add-data", f"rokwatch/version.txt{sep}rokwatch",
    "--add-data", f"rokwatch/badges{sep}rokwatch/badges",
    "--add-data", f"rokwatch/hud{sep}rokwatch/hud",
    *sys.argv[1:],
])
# the updater: a small separate program (one file, no console) that installs new versions
PyInstaller.__main__.run([
    "updater_app.py", "--name", "RoKWatcherUpdater", "--onefile", "--noconsole", "--noconfirm",
    "--distpath", "dist/updater", "--workpath", "build/updater",
])
out = Path("dist/RoKWatcher")
upd = Path("dist/updater") / ("RoKWatcherUpdater.exe" if os.name == "nt" else "RoKWatcherUpdater")
if upd.exists():
    shutil.copy(upd, out / upd.name)
shutil.copy("config.example.yaml", out / "config.example.yaml")
(out / "templates" / "attack").mkdir(parents=True, exist_ok=True)
print(f"Built {out.resolve()}")
