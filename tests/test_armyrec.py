"""Army recording against the test game: films the army through the zoom steps, ends
where it started, saves full-size pictures, a video and what was detected."""

import json
import time
import zipfile
import io
from pathlib import Path

import cv2
import pytest

pytest.importorskip("playwright")
pytest.importorskip("rapidocr_onnxruntime")

from rokwatch.armyrec import ArmyRecorder, newest_zip  # noqa: E402
from test_patrol import browser, game, js  # noqa: E402,F401


def test_records_an_army_through_the_zooms(game, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    js(game, "sim.state.zoom = 1; sim.state.simple = true")
    js(game, "sim.march('enemy', 'camp', 'white', 0.3, 0.002, '[P83L]JONH WICK')")
    assert game.goto(170, 386)                                        # the army on screen
    time.sleep(0.4)
    rec = ArmyRecorder(game, "[P83L]JONH WICK")
    folder, n = rec.run(cycles=1)
    assert n >= 15
    ev = [json.loads(l) for l in (folder / "events.jsonl").read_text().splitlines()]
    kinds = [e["kind"] for e in ev]
    assert "notch" in kinds and "close_text" in kinds and kinds[-1] == "done"
    frames = [e for e in ev if e["kind"] == "frame"]
    assert any(e.get("level") == "close" for e in frames)            # got to the close view
    assert any(i["kind"] == "army" for e in frames for i in e.get("icons", []))
    assert js(game, "sim.state.zoom") == 1                           # back where it started
    f = cv2.imread(str(folder / "frames" / frames[0]["file"]))
    assert f.shape[1] == frames[0]["size"][0]                        # full size
    assert (folder / "video.mp4").stat().st_size > 10000
    with zipfile.ZipFile(io.BytesIO(newest_zip())) as z:
        assert any(p.endswith("video.mp4") for p in z.namelist())
