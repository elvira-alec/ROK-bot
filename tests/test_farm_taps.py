"""2026-11-14 (user, live): 'it checks the same resource point twice and ignores the lower
one'. Two of his farms a few tiles apart both show at the close zoom; the badge nearest where
the farm was expected was the neighbour's, so the known farm was read again and the other
never. Now a box that turns out to be an already-known farm means: tap the next badge."""

import time

import numpy as np
import pytest

from rokwatch import screen
from rokwatch.hunter import Hunter, Army, ArmySeen
from rokwatch.screen import Text

NAME = "[P83L]JONH WICK"
H, W = 1042, 1852


class FakeB:
    def __init__(self, boxes):
        self.boxes, self.clicks, self.open = boxes, [], None

    def click(self, x, y):
        self.clicks.append((x, y))
        self.open = min(self.boxes, key=lambda b: abs(b[0] - x) + abs(b[1] - y))


class FakeGame:
    def __init__(self, b):
        self.b = b
        self.zoom_cfg = {"close_ratio": 0.7}

    def check_stop(self):
        pass

    def frame(self):
        return np.zeros((H, W, 3), np.uint8)

    def read_camera(self, f):
        return (4183, 180, 386)

    def close_popups(self):
        self.b.open = None


def _setup(monkeypatch, badges, box_for):
    b = FakeB(list(box_for))
    g = FakeGame(b)
    monkeypatch.setattr(screen, "find_gather_badges", lambda f: list(badges))
    monkeypatch.setattr(screen, "find_info_box", lambda f: (600, 200, 600, 500) if b.open else None)

    def read_text(f, box=None, strip=False):
        x, y, owner = box_for[b.open]
        return [Text("Resource Point", (0, 0, 1, 1), 1), Text(f"X:{x}Y:{y}", (0, 0, 1, 1), 1),
                Text("Level6Cropland", (0, 0, 1, 1), 1), Text("Gatherer", (0, 0, 1, 1), 1),
                Text(owner, (0, 0, 1, 1), 1)]
    monkeypatch.setattr(screen, "read_text", read_text)
    monkeypatch.setattr(time, "sleep", lambda s: None)
    h = Hunter(g, {"hunt": {"player_name": NAME, "city": [162, 374]},
                   "patrol": {"axes": [[0.0085, 0], [0, -0.012]]}}, [], "nul")
    return h, b


def test_known_farms_badge_tapped_first_then_the_right_one(monkeypatch, tmp_path):
    # badge A (his farm at 180,382, already read) nearest the expected spot; badge B below
    A, B = (926, 500), (930, 640)
    h, b = _setup(monkeypatch, [A, B],
                  {(A[0], A[1] + 20): (180, 382, NAME), (B[0], B[1] + 20): (180, 390, NAME)})
    h.log_path = tmp_path / "h.csv"
    known = Army(1, (180, 382), 0, 0, kind="farm", owner="him")
    known.info = {"xy": [180, 382]}
    h.tracker.armies = [known]
    a = ArmySeen((180, 386), False, None, 0, kind="farm")
    h._identify_farm(a)
    assert a.info.get("xy") == [180, 390] and a.owner == "him"      # the lower one, read
    assert len(b.clicks) == 2


def test_only_the_known_farm_there_means_it_is_that_farm(monkeypatch, tmp_path):
    A = (926, 500)
    h, b = _setup(monkeypatch, [A], {(A[0], A[1] + 20): (180, 382, NAME)})
    known = Army(1, (180, 382), 0, 0, kind="farm", owner="him")
    known.info = {"xy": [180, 382]}
    h.tracker.armies = [known]
    a = ArmySeen((181, 385), False, None, 0, kind="farm")
    h._identify_farm(a)
    assert a.owner == "him" and a.info["xy"] == [180, 382]          # merged with it later
    assert len(b.clicks) == 1


def test_two_farms_four_tiles_apart_are_two_farms():
    from rokwatch.hunter import same_spot
    f1 = ArmySeen((180, 386), False, None, 0, kind="farm")
    f2 = ArmySeen((180, 390), False, None, 0, kind="farm")
    f1b = ArmySeen((181, 386.5), False, None, 0, kind="farm")      # f1 seen from another stop
    assert not same_spot(f1, f2)
    assert same_spot(f1, f1b)


def test_a_farm_read_before_is_not_zoomed_in_on_again(tmp_path):
    """Live round 2: the neighbour's farm at 160,376 dropped out of the tracker and, found
    again, was zoomed in on again 26 s after its box had been read."""
    h = Hunter(FakeGame(FakeB([(0, 0)])), {"hunt": {"player_name": NAME, "city": [162, 374]},
                                         "patrol": {"axes": [[0.0085, 0], [0, -0.012]]}}, [],
               str(tmp_path / "h.csv"))
    h._farm_memory[(180, 382)] = ("other", time.time() - 26)
    s = ArmySeen((179.4, 381.5), False, None, 0, kind="farm")
    assert h._unknown([s]) == [] and s.owner == "other" and s.info["xy"] == [180, 382]
    h._farm_memory[(180, 382)] = ("other", time.time() - 11 * 60)        # due for a re-check
    s2 = ArmySeen((179.4, 381.5), False, None, 0, kind="farm")
    assert h._unknown([s2]) == [s2]
