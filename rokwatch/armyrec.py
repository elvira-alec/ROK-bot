"""Army recording: film a marching army through the zoom steps, at full quality, to
improve how the bot recognises and follows armies.

You put the game at your searching zoom with a marching army on screen and press
"Record a marching army" (panel: More > Snapshots). It then, twice:
  - films ~3 s at that zoom,
  - drags the army nearest the middle to the middle,
  - zooms in one step at a time, filming every step as it happens, until the close
    (3D) view, plus one more step,
  - films ~4 s close up and reads all the text on screen once (names over armies),
  - zooms back out the same number of steps, filming.
Everything goes to army_rec/<time>/: full-size frames (frames/0001.jpg ...), a small
video (video.mp4) and events.jsonl with what the bot detected on every frame. Download
it as a .zip from the panel and send it in.
"""

from __future__ import annotations

import io
import json
import math
import time
import zipfile
from datetime import datetime
from pathlib import Path

import cv2

from . import screen

ROOT = Path("army_rec")


def newest_zip():
    runs = sorted(p for p in ROOT.glob("*") if p.is_dir()) if ROOT.exists() else []
    if not runs:
        return None
    run = runs[-1]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for p in sorted(run.rglob("*")):
            if p.is_file():
                z.write(p, str(p.relative_to(run.parent)))
    return buf.getvalue()


class ArmyRecorder:
    def __init__(self, game, target_name="", progress=None, root=ROOT):
        self.game = game
        self.name = target_name
        self.say = progress or (lambda m: None)
        self.dir = Path(root) / datetime.now().strftime("%Y%m%d-%H%M%S")
        (self.dir / "frames").mkdir(parents=True, exist_ok=True)
        self._ev = (self.dir / "events.jsonl").open("w", encoding="utf-8")
        self._video = None
        self.n = 0
        self.t0 = time.time()

    # ------------------------------------------------------------ saving
    def event(self, kind, **data):
        self._ev.write(json.dumps({"t": round(time.time() - self.t0, 3), "kind": kind, **data},
                                  default=str) + "\n")
        self._ev.flush()

    def shot(self, phase, detect=True):
        """Grab one full-size picture, save it, note what the detectors see on it."""
        f = self.game.frame()
        self.n += 1
        name = f"{self.n:04d}.jpg"
        cv2.imwrite(str(self.dir / "frames" / name), f, [cv2.IMWRITE_JPEG_QUALITY, 92])
        self._add_to_video(f, phase)
        info = {"file": name, "phase": phase, "size": [f.shape[1], f.shape[0]]}
        if detect:
            try:
                info["icons"] = [{"px": [int(d[0]), int(d[1])], "kind": k} for d, k in screen.find_map_icons(f)]
                info["lines"] = [{"color": ln.color, "p1": [int(v) for v in ln.p1], "p2": [int(v) for v in ln.p2]}
                                 for ln in screen.find_march_lines(f)]
                info["badges"] = [[int(x), int(y)] for x, y in screen.find_gather_badges(f)]
                info["tags"] = screen.count_level_tags(f)
                info["level"] = screen.view_level(f)
                info["camera"] = (screen.camera_coords(f) or [None])[:3]
            except Exception as e:                   # never lose the picture over a detector
                info["error"] = str(e)
        self.event("frame", **info)
        return f, info

    def _add_to_video(self, f, phase):
        h, w = f.shape[:2]
        vw = min(1280, w)
        vh = int(h * vw / w) // 2 * 2
        small = cv2.resize(f, (vw, vh), interpolation=cv2.INTER_AREA)
        cv2.putText(small, f"{self.n:04d} {phase}", (10, vh - 12), cv2.FONT_HERSHEY_SIMPLEX, 0.7,
                    (0, 0, 255), 2)
        if self._video is None:
            self._video = cv2.VideoWriter(str(self.dir / "video.mp4"), cv2.VideoWriter_fourcc(*"mp4v"),
                                          6, (vw, vh))
        self._video.write(small)

    def film(self, seconds, phase, every=0.25):
        end = time.time() + seconds
        last = None
        while time.time() < end:
            self.game.check_stop()
            t = time.time()
            last = self.shot(phase)
            time.sleep(max(0.0, every - (time.time() - t)))
        return last

    def close(self):
        self._ev.close()
        if self._video is not None:
            self._video.release()
            self._video = None

    # ------------------------------------------------------------ the steps
    def _army_near_middle(self, info, shape):
        H, W = shape[:2]
        mid = (W / 2, H / 2)
        icons = [i for i in info.get("icons", []) if i["kind"] in ("army", "hostile")] or info.get("icons", [])
        if not icons:
            return None
        return min(icons, key=lambda i: math.dist(i["px"], mid))["px"]

    def _centre_on(self, px, shape):
        H, W = shape[:2]
        dx, dy = W / 2 - px[0], H / 2 - px[1]
        if math.hypot(dx, dy) < 0.12 * H:
            return
        x0, y0 = int(W / 2 - dx / 2), int(H / 2 - dy / 2)
        self.game.b.drag(x0, y0, int(x0 + dx), int(y0 + dy))
        self.event("centre", army_px=px, drag=[x0, y0, int(x0 + dx), int(y0 + dy)])

    def _notch_filmed(self, out, phase):
        """One zoom step, filming it as it happens (~1 s)."""
        H, W = self.game._size or self.game.frame().shape[:2]
        self.game.check_stop()
        f0 = self.game.frame()
        before, lv0 = self.game._small(f0), screen.view_level(f0)
        self.game.b.wheel(W // 2, H // 2, 100 if out else -100)
        end = time.time() + 0.9
        while time.time() < end:
            self.shot(phase, detect=False)
            time.sleep(0.08)
        f, info = self.shot(phase + " settled")
        moved = (info.get("level") != lv0                              # did the zoom change?
                 or self.game._changed(self.game._small(f), before) > 0.02)
        self.event("notch", out=out, changed=moved)
        return f, info, moved

    def run(self, cycles=2):
        g = self.game
        g._size = g.frame().shape[:2]
        self.event("start", target=self.name, zoom=g.zoom_cfg)
        try:
            for c in range(cycles):
                self.say(f"Army recording {c + 1}/{cycles}: filming at this zoom")
                f, info = self.film(3, f"search {c + 1}")
                px = self._army_near_middle(info, f.shape)
                if px:
                    self.say(f"Army recording {c + 1}/{cycles}: putting the army in the middle")
                    self._centre_on(px, f.shape)
                    self.film(1, f"centred {c + 1}")
                else:
                    self.event("no_army_seen")
                self.say(f"Army recording {c + 1}/{cycles}: zooming in step by step")
                steps, extra = 0, 0
                tries = 0
                while steps < 12 and tries < 14:
                    tries += 1
                    _, info, moved = self._notch_filmed(False, f"in {c + 1}.{steps + 1}")
                    if not moved:
                        break                          # all the way in already
                    steps += 1
                    if info.get("level") == "close":
                        extra += 1
                        if extra > 1:                  # the close view, and one step past it
                            break
                self.say(f"Army recording {c + 1}/{cycles}: filming close up")
                f, _ = self.film(4, f"close {c + 1}")
                texts = screen.read_text(f)
                self.event("close_text", texts=[{"text": t.text, "box": list(t.box), "conf": round(t.conf, 2),
                                                 "kind": screen.label_kind(f, t)} for t in texts],
                           his=[{"text": p.text, "pos": [int(v) for v in p.pos], "kind": p.kind}
                                for p in screen.find_player(f, texts, self.name)] if self.name else [])
                self.say(f"Army recording {c + 1}/{cycles}: zooming back out")
                for i in range(steps):
                    self._notch_filmed(True, f"out {c + 1}.{i + 1}")
                self.film(2, f"back {c + 1}")
            self.event("done", frames=self.n)
        finally:
            self.close()
        return self.dir, self.n
