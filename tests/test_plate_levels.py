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
