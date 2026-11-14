"""Dev tool: go to the searching zoom and save full-size pictures at a few spots around a
place (default his city), with what the detectors find in each. For collecting real test
pictures (day / night) without running a whole hunt.

    python tools/capture_sweep.py OUTDIR [X Y] [--spots N]
"""
import json
import sys
from pathlib import Path

import cv2
import yaml

from rokwatch import screen
from rokwatch.desktop import DesktopWindow
from rokwatch.game import Game
from rokwatch.hunter import Hunter, sweep_points


def main():
    out = Path(sys.argv[1])
    out.mkdir(parents=True, exist_ok=True)
    cfg = yaml.safe_load(open("setup.yaml", encoding="utf-8"))
    args = sys.argv[2:]
    if "--at" in args:
        args = args[:args.index("--at")]
    n = 5
    if "--spots" in args:
        i = args.index("--spots")
        n = int(args[i + 1])
        del args[i:i + 2]
    x, y = (int(args[0]), int(args[1])) if len(args) >= 2 else tuple(cfg["hunt"]["city"])
    with DesktopWindow(cfg.get("game_window") or "Rise of Kingdoms") as w:
        g = Game(w, settle=0.6)
        g.zoom_cfg = dict(cfg["hunt"]["zoom"])
        print("reanchor:", g.reanchor())
        axes = cfg["patrol"]["axes"]
        pts = sweep_points((x, y), 12, axes, g.frame().shape)[:n]
        if "--at" in sys.argv:                       # exact spots: --at x,y x,y ...
            pts = [tuple(int(v) for v in q.split(",")) for q in sys.argv[sys.argv.index("--at") + 1:]]
        for i, (px, py) in enumerate(pts):
            g.pan_to(px, py, axes)
            f = g.fresh_frame(0.8)
            import time as _t
            end = _t.time() + 2.5                      # wait for the icons, as the hunt does
            while not screen.icons_drawn(f) and _t.time() < end:
                _t.sleep(0.1)
                f = g.frame()
            _t.sleep(0.2)
            f = g.frame()
            cam = screen.camera_coords(f)
            found = [(k, [int(d[0]), int(d[1])]) for d, k in screen.find_map_icons(f)]
            badges = [[int(b[0]), int(b[1])] for b in screen.find_gather_badges(f)]
            name = f"spot{i:02d}_{cam[1] if cam else 'x'}_{cam[2] if cam else 'y'}"
            cv2.imwrite(str(out / f"{name}.png"), f)
            info = {"camera": cam[:3] if cam else None, "icons": found, "badges": badges,
                    "tags": screen.count_level_tags(f), "level": g.level()}
            (out / f"{name}.json").write_text(json.dumps(info))
            print(name, info)


if __name__ == "__main__":
    main()
