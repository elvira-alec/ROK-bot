"""Entry point for RoKWatcher.exe.

Double-click: opens the control panel in your browser.
With arguments: same as `python -m rokwatch ...` (e.g. RoKWatcher.exe --patrol).
"""

import os
import sys
from pathlib import Path


def main():
    # file names given on the command line are relative to where you ran it from
    sys.argv[1:] = [str(Path(a).resolve()) if not a.startswith("-") and Path(a).exists() else a
                    for a in sys.argv[1:]]
    if getattr(sys, "frozen", False):
        os.chdir(Path(sys.executable).parent)  # keep config, logs and screenshots next to the exe
    if len(sys.argv) > 1:
        from rokwatch.__main__ import main as cli
        sys.exit(cli(sys.argv[1:]))
    from rokwatch.__main__ import setup_logging
    from rokwatch.panel import serve
    setup_logging()
    serve()


if __name__ == "__main__":
    main()
