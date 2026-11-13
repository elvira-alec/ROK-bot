"""Debug recording: everything the bot sees and does, to replay a problem exactly.

When on (panel: More > Snapshots > Debug recording), it saves into debug_trace/<time>/:
  - frames/000123.jpg  every picture the bot looks at (smaller, at most ~5 a second)
  - trace.jsonl        one line per event: every click / drag / scroll / key / typed text
                       with its position, every look (camera, what was found), zoom steps,
                       jumps, owner checks, round summaries and all log messages - each
                       with the time, and the frame shown at that moment.
Download it as a .zip from the panel and send it in.
"""

from __future__ import annotations

import io
import json
import logging
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path

import cv2

ROOT = Path("debug_trace")


class Trace:
    def __init__(self):
        self.on = False
        self.dir: Path | None = None
        self._f = None
        self._lock = threading.Lock()
        self._n = 0
        self._last_frame = 0.0
        self._frame_name = None
        self._handler = None
        self.t0 = 0.0

    # ------------------------------------------------------------ on / off
    def start(self):
        self.stop()
        self.dir = ROOT / datetime.now().strftime("%Y%m%d-%H%M%S")
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        self._f = (self.dir / "trace.jsonl").open("a", encoding="utf-8")
        self._n, self.t0, self.on = 0, time.time(), True
        self._handler = _LogToTrace(self)
        logging.getLogger("rokwatch").addHandler(self._handler)
        self.event("trace_start")

    def stop(self):
        if self.on:
            self.event("trace_stop")
        self.on = False
        if self._handler:
            logging.getLogger("rokwatch").removeHandler(self._handler)
            self._handler = None
        with self._lock:
            if self._f:
                self._f.close()
                self._f = None

    # ------------------------------------------------------------ recording
    def event(self, _kind, **data):
        if not self.on:
            return
        if "kind" in data:                      # e.g. the kind of army an event is about
            data["item"] = data.pop("kind")
        rec = {"t": round(time.time() - self.t0, 3), "kind": _kind, "frame": self._frame_name, **data}
        with self._lock:
            if self._f:
                self._f.write(json.dumps(rec, default=str) + "\n")
                self._f.flush()

    def frame(self, img, tag="look"):
        """Keep this picture (not more than ~5 a second)."""
        if not self.on or img is None:
            return
        now = time.time()
        if now - self._last_frame < 0.2:
            return
        self._last_frame = now
        self._n += 1
        name = f"{self._n:06d}.jpg"
        h, w = img.shape[:2]
        small = cv2.resize(img, (960, max(1, int(h * 960 / w)))) if w > 960 else img
        cv2.imwrite(str(self.dir / "frames" / name), small, [cv2.IMWRITE_JPEG_QUALITY, 70])
        self._frame_name = name
        self.event("frame", file=name, tag=tag, size=[w, h])

    def zip_bytes(self):
        """The newest recording as a .zip."""
        runs = sorted(ROOT.glob("*")) if ROOT.exists() else []
        if not runs:
            return None
        run = runs[-1]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
            for p in sorted(run.rglob("*")):
                if p.is_file():
                    z.write(p, str(p.relative_to(run.parent)))
        return buf.getvalue()

    def status(self):
        return {"on": self.on, "folder": str(self.dir) if self.dir else None, "frames": self._n}


class _LogToTrace(logging.Handler):
    def __init__(self, trace):
        super().__init__(logging.INFO)
        self.trace = trace

    def emit(self, record):
        try:
            self.trace.event("log", level=record.levelname, msg=record.getMessage())
        except Exception:
            pass


TRACE = Trace()


def instrument(gb):
    """Log every action on the game window (positions are in game-window pixels)."""
    if getattr(gb, "_traced", False):
        return gb
    for name in ("click", "drag", "wheel", "press", "type"):
        orig = getattr(gb, name, None)
        if orig is None:
            continue

        def wrap(*a, _orig=orig, _name=name, **k):
            TRACE.event("action", what=_name, args=[str(x) for x in a])
            return _orig(*a, **k)
        setattr(gb, name, wrap)
    gb._traced = True
    return gb
