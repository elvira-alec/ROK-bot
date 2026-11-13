"""Debug recording: pictures + every action, downloadable as a zip."""

import io
import json
import zipfile

import numpy as np

from rokwatch import trace as tr


class FakeWindow:
    def __init__(self):
        self.did = []

    def click(self, x, y):
        self.did.append(("click", x, y))

    def drag(self, x0, y0, x1, y1, steps=15):
        self.did.append(("drag",))

    def wheel(self, x, y, dy):
        self.did.append(("wheel",))

    def press(self, k):
        self.did.append(("press", k))

    def type(self, t):
        self.did.append(("type", t))


def test_records_pictures_actions_and_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(tr, "ROOT", tmp_path / "debug_trace")
    t = tr.Trace()
    monkeypatch.setattr(tr, "TRACE", t)
    w = tr.instrument(FakeWindow())
    w.click(10, 20)                              # not recording yet: nothing kept
    t.start()
    t.frame(np.zeros((660, 1434, 3), np.uint8))
    w.click(100, 200)
    w.type("162")
    import logging
    logging.getLogger("rokwatch").warning("something odd")
    t.event("goto", x=162, y=374)
    t.event("owner", kind="farm", owner="him")            # a 'kind' field of its own is fine
    t.stop()
    assert w.did[0] == ("click", 10, 20) and ("type", "162") in w.did   # the real actions still happen
    z = zipfile.ZipFile(io.BytesIO(t.zip_bytes()))
    names = z.namelist()
    assert any(n.endswith("frames/000001.jpg") for n in names)
    lines = [json.loads(x) for x in z.read([n for n in names if n.endswith("trace.jsonl")][0]).decode().splitlines()]
    kinds = [x["kind"] for x in lines]
    assert kinds[0] == "trace_start" and "frame" in kinds and "goto" in kinds and kinds[-1] == "trace_stop"
    acts = [x for x in lines if x["kind"] == "action"]
    assert [a["what"] for a in acts] == ["click", "type"] and acts[0]["args"] == ["100", "200"]
    assert acts[0]["frame"] == "000001.jpg"                              # what was on screen then
    assert any(x["kind"] == "log" and "something odd" in x["msg"] for x in lines)
    assert any(x["kind"] == "owner" and x["item"] == "farm" for x in lines)
