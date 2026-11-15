"""A marching troop has no RECALL text: two blue hexagon icons under it and then a pale U-turn
arrow. The camera follows the troop with a lag, so the icons move about (live 2026-11-15)."""

import glob
from pathlib import Path

import cv2

from rokwatch import screen

D = Path(__file__).parent / "real_screens"


def test_uturn_found_next_to_the_blue_icons():
    for name, exp in (("uturn_marching.jpg", (664, 372)), ("uturn_marching_2.jpg", (637, 350))):
        f = cv2.resize(cv2.imread(str(D / "recall" / name)), (1821, 1024))
        x, y = screen.find_uturn_icon(f)
        assert abs(x - exp[0] * 1821 / 1200) < 15 and abs(y - exp[1] * 1024 / 675) < 15


def test_no_uturn_on_other_screens():
    ps = glob.glob(str(D / "*.png")) + glob.glob(str(D / "zoom" / "*.png")) + glob.glob(str(D / "resources" / "view_*.jpg"))
    assert [p for p in ps if screen.find_uturn_icon(cv2.imread(p))] == []
