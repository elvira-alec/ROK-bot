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


def test_my_own_city_is_recognised():
    # after a restart / lost connection the game shows your own city: power + VIP at the
    # top left, no X:Y readout (live 2026-11-14: the bot waited there for the map)
    f = cv2.imread(str(D / "city_view" / "my_city_home.png"))
    assert screen.home_view(f) and not screen.city_view(f)


@pytest.mark.parametrize("p", [str(D / "city_view" / "his_city_3d_map.jpg"),
                               str(D / "zoom" / "close_3d.png"), str(D / "zoom" / "search_with_tags.png")])
def test_the_map_is_not_my_city(p):
    assert not screen.home_view(cv2.imread(p))


def test_readout_read_in_two_pieces_is_joined():
    # live: '#4183 X:211' and ':422' came out as two pieces (the Y lost): it took the map
    # for your city and pressed the city/map button again, back into the city
    f = cv2.imread(str(D / "city_view" / "my_city_map_split_readout.png"))
    assert screen.camera_coords(f)[:3] == (4183, 211, 422)
    assert not screen.home_view(f)
