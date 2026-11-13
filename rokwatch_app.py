"""Entry point for RoKWatcher.exe.

Double-click: starts RoK Watcher in the background (no black window) and opens the
control panel in your browser. Started again while it's running: just opens the
panel. Quit it from the panel (More, Program).
With arguments: same as `python -m rokwatch ...` (e.g. RoKWatcher.exe --patrol).
"""

import os
import subprocess
import sys
import urllib.request
import webbrowser
from pathlib import Path

PANEL = "http://127.0.0.1:8765/"
CREATE_NO_WINDOW = 0x08000000


def panel_running() -> bool:
    try:
        with urllib.request.urlopen(PANEL + "api/settings", timeout=1.5) as r:
            return r.status == 200
    except OSError:
        return False


def main():
    # file names given on the command line are relative to where you ran it from
    sys.argv[1:] = [str(Path(a).resolve()) if not a.startswith("-") and Path(a).exists() else a
                    for a in sys.argv[1:]]
    frozen = getattr(sys, "frozen", False)
    if frozen:
        os.chdir(Path(sys.executable).parent)  # keep config, logs and screenshots next to the exe
    flags = {f: f in sys.argv for f in ("--updated", "--background", "--visible")}
    for f, on in flags.items():
        if on:
            sys.argv.remove(f)
    updated = flags["--updated"]

    if len(sys.argv) > 1 and sys.argv[1] == "--test-update":
        # (build check) install the release at URL with the real updater, then quit
        from rokwatch import updater
        updater.launch_updater({"url": sys.argv[2], "version": sys.argv[3]})
        os._exit(0)
    if len(sys.argv) > 1:
        from rokwatch.__main__ import main as cli
        sys.exit(cli(sys.argv[1:]))

    if frozen and sys.platform == "win32" and not flags["--background"] and not flags["--visible"]:
        # already running: just show the panel
        if panel_running():
            webbrowser.open(PANEL)
            return
        # run on without the black window: start a copy with no window, and close this one
        subprocess.Popen([sys.executable, "--background"] + (["--updated"] if updated else []),
                         creationflags=CREATE_NO_WINDOW | 0x00000200, close_fds=True)
        return

    from rokwatch.__main__ import setup_logging
    from rokwatch.panel import serve
    setup_logging()
    serve(open_browser="if_no_client" if updated else True)


if __name__ == "__main__":
    main()
