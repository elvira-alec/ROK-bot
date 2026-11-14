"""The user now has 4 troop slots (user's phone screenshot, 2026-11-14): all four must be
read from the troop list, not just three."""

from pathlib import Path

import cv2
import pytest

from rokwatch import screen

P = Path(__file__).parent / "real_screens" / "troops4" / "four_marching_phone.png"


def test_four_troops_read():
    f = cv2.imread(str(P))
    used, total, slots = screen.troop_panel(f, screen.read_text(f))
    assert (used, total) == (4, 4)
    assert [s.state for s in slots][:4] == ["marching"] * 4
    ys = [s.badge[1] for s in slots[:4]]
    assert all(b - a > 0.1 * f.shape[0] for a, b in zip(ys, ys[1:]))


def test_four_gathering_on_pc():
    # live 2026-11-14: troops 3 and 4 were placed a badge too low and read as "other"
    f = cv2.imread(str(P.parent / "four_gathering_pc.png"))
    used, total, slots = screen.troop_panel(f, screen.read_text(f))
    assert (used, total) == (4, 4)
    assert [s.state for s in slots][:4] == ["gathering"] * 4
    assert [s.badge[1] for s in slots[:4]] == pytest.approx([236, 314, 391, 468], abs=6)
