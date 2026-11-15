"""Every level plate cut from the user's game (hand-checked: day, night, desert, borders,
2026-11-15) reads right. The drawn 5 read every real 5 as 6 or 3; a misread level 3 sent a
troop to a 3 with 4s and 5s around."""
import json
from pathlib import Path

import cv2
import numpy as np

from rokwatch import screen

D = Path(__file__).parent / "real_screens" / "plates"


def test_every_real_plate_reads_right():
    labels = json.loads((D / "labels.json").read_text())
    wrong = []
    for l in labels:
        c = cv2.imread(str(D / l["file"]))
        H, W = l["size"]
        f = np.zeros((H, W, 3), np.uint8)                   # the crop back in its place
        x0, y0 = l["origin"]
        f[y0:y0 + c.shape[0], x0:x0 + c.shape[1]] = c
        mark = (l["mark"][0] + x0, l["mark"][1] + y0)
        got = screen.plate_level(f, mark)
        if got != l["level"]:
            wrong.append((l["file"], got))
    assert not wrong, wrong
    assert len(labels) >= 200 and sum(1 for l in labels if l["level"] == 5) >= 4


def test_night_stone_plate_on_its_top_edge():
    """At night the stone icon merges with the plate's top edge: the plate was found on the
    edge and read as nothing (an unread level 3 stone went first, 2026-11-15)."""
    import math
    d = Path(__file__).parent / "real_screens" / "plates_night_edge"
    for l in json.loads((d / "labels.json").read_text()):
        c = cv2.imread(str(d / l["file"]))
        H, W = l["size"]
        f = np.zeros((H, W, 3), np.uint8)
        x0, y0 = l["origin"]
        f[y0:y0 + c.shape[0], x0:x0 + c.shape[1]] = c
        pts = [p for p in screen.find_resource_points(f) if math.dist(p["mark"], l["mark"]) < 12]
        assert pts and pts[0]["kind"] == l["kind"] and pts[0]["level"] == l["level"], (l, pts)
