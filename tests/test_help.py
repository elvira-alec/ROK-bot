"""'There is an icon I want you to click whenever it shows up' (user, 2026-11-15): the alliance
help bubble - a white bubble with a green ring and a handshake over the Alliance button."""

import glob
from pathlib import Path

import cv2
import pytest

from rokwatch import screen

D = Path(__file__).parent / "real_screens" / "help"


@pytest.mark.parametrize("p", sorted(glob.glob(str(D / "with_*.jpg"))))
def test_bubble_found(p):
    f = cv2.resize(cv2.imread(p), (1821, 1024))
    x, y = screen.find_help_bubble(f)
    assert abs(x - 1018 * 1821 / 1200) < 25 and abs(y - 591 * 1024 / 675) < 25


@pytest.mark.parametrize("p", sorted(glob.glob(str(D / "without_*.jpg")))
                         + [str(Path(__file__).parent / "real_screens" / "zoom" / "search_with_tags.png")])
def test_no_bubble(p):
    assert screen.find_help_bubble(cv2.imread(p)) is None
