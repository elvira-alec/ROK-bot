"""level_marks misses the level plate on a stone deposit (it merges with the pale rocks;
live 2026-11-15: the bot tapped round a level 6 stone for 25 s)."""
import math
import os

import cv2

from rokwatch import screen

HERE = os.path.join(os.path.dirname(__file__), "real_screens", "gather")


def test_white_level6_plate_found():
    f = cv2.resize(cv2.imread(os.path.join(HERE, "white_plate_l6_close.jpg")), (1821, 1024))
    want = (858 * 1821 / 1200, 370 * 1024 / 675)
    found = [p for p in screen.white_plates(f) if math.dist(p, want) < 20]
    assert found and screen.plate_level(f, found[0]) == 6
