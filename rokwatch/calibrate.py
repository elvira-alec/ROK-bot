"""One-time setup: tell the watcher where the game, his city and your city are.

    Runs automatically the first time, or with:  python -m rokwatch --calibrate
"""

from __future__ import annotations

import cv2

from . import config

WINDOW = "RoK calibrate"


def pick_point(img, prompt: str):
    print(prompt)
    shown = img.copy()
    cv2.putText(shown, prompt, (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    clicked = []

    def on_mouse(event, x, y, *_):
        if event == cv2.EVENT_LBUTTONDOWN:
            clicked.append((x, y))

    cv2.imshow(WINDOW, shown)
    cv2.setMouseCallback(WINDOW, on_mouse)
    while not clicked:
        if cv2.waitKey(30) == 27:
            raise SystemExit("Cancelled.")
    return [int(clicked[0][0]), int(clicked[0][1])]


def calibrate(gb, cfg_path: str) -> dict:
    """Ask the user to set up positions using the already-open browser."""
    input("\nIn the browser window: log in, open Rise of Kingdoms, go to the MAP and\n"
          "move it so you can see his city AND your city (or where you gather).\n"
          "Then come back here and press Enter... ")
    full = gb.screenshot()

    print("Drag a box around the game picture, then press Enter (or Esc for whole page).")
    x, y, w, h = cv2.selectROI(WINDOW, full, showCrosshair=False)
    area = [int(x), int(y), int(w), int(h)] if w and h else None
    game = full[y:y + h, x:x + w] if area else full

    enemy = pick_point(game, "Click the CENTER of HIS city")
    home = pick_point(game, "Click YOUR city (or your gathering area)")
    cv2.destroyAllWindows()
    cv2.imwrite("calibration.png", game)

    config.save_fields({"game_area": area, "enemy_watch.enemy_city": enemy,
                        "enemy_watch.home": home}, cfg_path)
    print(f"Saved: game_area={area}, enemy_city={enemy}, home={home}")
    print("Don't move the map or resize the window from now on.")
    return config.load(cfg_path)
