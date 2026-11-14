"""Zooming in over a city enters it ("Viewing the city of [P83L]JONH WICK."), and the
wheel never leaves it: the zoom setup got stuck there (live, 2026-11-14). The bot must
recognise that view - and nothing else - and leave it with the map button."""

import glob
from pathlib import Path

import cv2
import pytest

from rokwatch import screen

D = Path(__file__).parent / "real_screens"


def test_inside_his_city_is_a_city_view():
    f = cv2.imread(str(D / "city_view" / "his_city_inside.jpg"))
    assert screen.city_view(f)
    x, y = screen.city_view_exit(f)
    H, W = f.shape[:2]
    assert x > 0.9 * W and y > 0.9 * H


@pytest.mark.parametrize("p", [str(D / "city_view" / "his_city_3d_map.jpg")]
                         + sorted(glob.glob(str(D / "*.png")))
                         + sorted(glob.glob(str(D / "zoom" / "*.png"))))
def test_map_screens_are_not_city_views(p):
    f = cv2.imread(p)
    assert not screen.city_view(f)
