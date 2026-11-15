"""Map pixel -> tile at the searching zoom, perspective-correct.

The map is a tilted 3D view: a flat (linear) conversion is ~1 tile off at the middle of the
screen and up to ~4 tiles off near the edges (measured 2026-11-15 on the user's game against
points whose exact X:Y came from their boxes: median 1.09, 90 % 2.71 tiles). A homography from
the picture (as fractions of its width / height) to the tile offset from the camera reading
gets median 0.49, 90 % 0.92 tiles. With that, one point seen from two scan views lands in one
place (it was listed two or three times) and two real neighbours aren't merged (a full level 5
food 2 tiles from a level 4 was lost that way).

The model starts from the one fitted on the user's game (resources/persp_default.json) and
keeps learning from auto-gather's own box reads (pairs: where a plate was on a scan picture,
and the exact X:Y its box gave). It is only used at the zoom it was fitted at: other axes ->
the flat conversion."""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

DEFAULT = Path(__file__).with_name("resources") / "persp_default.json"
LEARNT = Path("persp.json")
MAX_PAIRS = 400


class Persp:
    def __init__(self, learnt=LEARNT, default=DEFAULT):
        self.learnt = Path(learnt)
        self.model = None
        self.new_pairs = 0
        for p in (self.learnt, Path(default)):
            try:
                self.model = json.loads(p.read_text())
                break
            except (OSError, ValueError):
                continue

    def usable(self, axes, shape) -> bool:
        """Fitted at this zoom (axes within 8 %) and this picture shape."""
        m = self.model
        if not m or not axes:
            return False
        try:
            a0, a1 = np.array(m["axes"], float), np.array(axes, float)
            sx = abs(a1[0][0] / a0[0][0])
            sy = abs(a1[1][1] / a0[1][1])
            ar0 = m["size"][1] / m["size"][0]
            ar1 = shape[1] / shape[0]
        except (KeyError, ZeroDivisionError, IndexError, TypeError):
            return False
        return 0.92 < sx < 1.08 and 0.92 < sy < 1.08 and abs(ar0 - ar1) < 0.03

    def to_tile(self, cam, axes, mark, shape):
        """Tile under this picture point; perspective-correct when the model fits, else flat."""
        if self.usable(axes, shape):
            H, W = shape[:2]
            u, v = mark[0] / W, mark[1] / H
            Hm = np.array(self.model["H"], float)
            p = Hm @ np.array([u, v, 1.0])
            if abs(p[2]) > 1e-9:
                return (cam[0] + p[0] / p[2], cam[1] + p[1] / p[2])
        from .hunter import to_tile
        return to_tile(cam, axes, mark, shape)

    def add_pair(self, mark, shape, cam, xy, axes):
        """A plate seen at `mark` on a scan picture whose camera read `cam` turned out (from
        its box) to be at `xy`. Refit every 20 new pairs."""
        if not self.model or not self.usable(axes, shape):
            return
        H, W = shape[:2]
        pair = [mark[0] / W, mark[1] / H, xy[0] - cam[0], xy[1] - cam[1]]
        pairs = (self.model.get("pairs") or []) + [pair]
        self.model["pairs"] = pairs[-MAX_PAIRS:]
        self.new_pairs += 1
        if self.new_pairs >= 20:
            self.new_pairs = 0
            self.refit()

    def refit(self):
        try:
            import cv2
            P = np.array(self.model["pairs"], np.float32)
            if len(P) < 30:
                return
            Hm, inl = cv2.findHomography(P[:, :2], P[:, 2:], cv2.RANSAC, 1.0)
            if Hm is None or inl.sum() < 0.7 * len(P):
                return                                  # too noisy: keep the model it has
            pred = cv2.perspectiveTransform(P[None, :, :2], Hm)[0]
            err = np.hypot(*(pred - P[:, 2:]).T)[inl.ravel() > 0]
            if np.median(err) > 0.8:
                return
            self.model["H"] = Hm.tolist()
            self.learnt.write_text(json.dumps(self.model))
            log.info("gather: map positions refitted from %d box reads (median error %.2f tiles)",
                     int(inl.sum()), float(np.median(err)))
        except Exception:
            log.debug("persp refit failed", exc_info=True)
