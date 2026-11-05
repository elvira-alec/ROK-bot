"""One-time setup: tell the watcher where the game, his city and your spots are.

Runs automatically the first time, or with:  python -m rokwatch --calibrate
"""

from __future__ import annotations

import cv2

from . import config

WINDOW = "RoK setup"


def _show(img, lines):
    shown = img.copy()
    for i, text in enumerate(lines):
        y = 30 + 28 * i
        cv2.putText(shown, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 0, 0), 4)
        cv2.putText(shown, text, (10, y), cv2.FONT_HERSHEY_SIMPLEX, 0.75, (0, 255, 255), 2)
    cv2.imshow(WINDOW, shown)


def pick_points(img, prompt: str, many=False):
    """Let the user click one point (or several, finishing with Enter)."""
    print(prompt)
    clicked = []

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append([int(x), int(y)])

    while True:
        marked = img.copy()
        for i, p in enumerate(clicked):
            cv2.circle(marked, tuple(p), 9, (0, 255, 0), 2)
            cv2.putText(marked, str(i + 1), (p[0] + 11, p[1]), cv2.FONT_HERSHEY_SIMPLEX, 0.6,
                        (0, 255, 0), 2)
        hint = "Enter = done, Backspace = undo" if many else ""
        _show(marked, [prompt, hint])
        cv2.setMouseCallback(WINDOW, on_mouse)
        key = cv2.waitKey(30) & 0xFF
        if key == 27:
            raise SystemExit("Setup cancelled.")
        if many:
            if key == 8 and clicked:
                clicked.pop()
            if key in (10, 13) and clicked:
                return clicked
        elif clicked:
            return clicked[:1]


def calibrate(gb, cfg_path: str) -> dict:
    """Set up positions using the already-open browser."""
    input("\nIn the browser window: log in, open Rise of Kingdoms, go to the MAP and\n"
          "move it so you can see his city AND your city / gathering spots.\n"
          "Then come back here and press Enter... ")
    full = gb.screenshot()

    print("Drag a box around the game picture, then press Enter (or Esc for the whole page).")
    x, y, w, h = cv2.selectROI(WINDOW, full, showCrosshair=False)
    area = [int(x), int(y), int(w), int(h)] if w and h else None
    game = full[y:y + h, x:x + w] if area else full

    enemy = pick_points(game, "Click the CENTER of HIS city")[0]
    spots = pick_points(game, "Click YOUR city, then any gathering spots to protect", many=True)
    cv2.destroyAllWindows()
    cv2.waitKey(1)

    names = ["your city"] + [f"spot {i}" for i in range(1, len(spots))]
    points = [{"name": n, "pos": p} for n, p in zip(names, spots)]
    cv2.imwrite(config.reference_path(cfg_path), game)
    config.save_fields({"game_area": area, "enemy_watch.enemy_city": enemy,
                        "enemy_watch.protect_points": points}, cfg_path)
    print(f"Saved: his city at {enemy}, {len(points)} spot(s) to protect.")
    print("Try not to move the map. If it gets nudged a bit, the watcher follows it.")
    return config.load(cfg_path)
