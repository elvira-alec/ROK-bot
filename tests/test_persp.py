"""Perspective-correct map positions (searching zoom): the flat conversion was 1.1 tiles off
(median, up to 4) - points listed twice, a full level 5 food merged into a level 4 next to it."""
import json

import numpy as np

from rokwatch import persp


def test_default_model_fits_its_pairs():
    m = json.loads(persp.DEFAULT.read_text())
    P = np.array(m["pairs"], float)
    Hm = np.array(m["H"], float)
    err = []
    for u, v, dx, dy in P:
        p = Hm @ [u, v, 1.0]
        err.append(np.hypot(p[0] / p[2] - dx, p[1] / p[2] - dy))
    assert len(P) >= 80 and np.median(err) < 0.6 and np.percentile(err, 90) < 1.0


def test_only_used_at_its_zoom(tmp_path):
    p = persp.Persp(learnt=tmp_path / "none.json")
    axes = p.model["axes"]
    assert p.usable(axes, (1024, 1821))
    other = [[axes[0][0] * 1.3, 0], [0, axes[1][1] * 1.3]]
    assert not p.usable(other, (1024, 1821))
    # not usable -> the flat conversion (same as hunter.to_tile)
    from rokwatch.hunter import to_tile
    assert p.to_tile((170, 380), other, (300, 200), (1024, 1821)) == to_tile((170, 380), other, (300, 200), (1024, 1821))


def test_learns_and_refits(tmp_path):
    p = persp.Persp(learnt=tmp_path / "persp.json")
    axes = p.model["axes"]
    Hm = np.array(p.model["H"], float)
    for i in range(20):
        u, v = 0.3 + 0.02 * i, 0.3 + 0.015 * i
        q = Hm @ [u, v, 1.0]
        p.add_pair((u * 1821, v * 1024), (1024, 1821), (170, 380),
                   (170 + q[0] / q[2], 380 + q[1] / q[2]), axes)
    assert (tmp_path / "persp.json").exists()
