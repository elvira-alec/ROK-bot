"""Builds RoKWatcher (an .exe on Windows) with PyInstaller:  python build_exe.py"""

import os
import shutil
import sys
from pathlib import Path

import PyInstaller.__main__

sep = ";" if os.name == "nt" else ":"
PyInstaller.__main__.run([
    "rokwatch_app.py", "--name", "RoKWatcher", "--noconfirm", "--console",
    "--collect-all", "rapidocr_onnxruntime",   # text-reading models
    "--collect-all", "playwright",              # browser control
    "--collect-binaries", "onnxruntime",
    "--add-data", f"rokwatch/panel.html{sep}rokwatch",
    "--add-data", f"rokwatch/badges{sep}rokwatch/badges",
    "--add-data", f"rokwatch/hud{sep}rokwatch/hud",
    *sys.argv[1:],
])
out = Path("dist/RoKWatcher")
shutil.copy("config.example.yaml", out / "config.example.yaml")
(out / "templates" / "attack").mkdir(parents=True, exist_ok=True)
print(f"Built {out.resolve()}")
