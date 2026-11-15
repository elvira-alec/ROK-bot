"""Every GET the page makes must get an answer. v1.33 added an import inside do_GET for the
activity request, which made the live view's request crash ('empty reply') from then on:
the live view was blank on the user's panel."""

import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np

from rokwatch import panel as pm


def test_live_view_and_activity_answer(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("{}\n")
    p = pm.Panel(str(tmp_path / "config.yaml"))

    class GB:
        hwnd, last_frame, last_frame_t = 1, None, 0.0

        def screenshot(self, area=None):
            return np.full((1024, 1821, 3), 90, np.uint8)
    p.worker.gb = GB()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), pm.make_handler(p, {"port": 0, "lan": False}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    try:
        r = urllib.request.urlopen(base + "/live.jpg?w=640&have=&t=1", timeout=10)
        assert r.status == 200 and r.read()[:2] == b"\xff\xd8"          # a JPEG
        r = urllib.request.urlopen(base + "/api/activity?player=%5BX%5DSomeone", timeout=10)
        assert r.status == 200 and b"players" in r.read()
    finally:
        srv.shutdown()


def test_live_stream_sends_pictures(tmp_path, monkeypatch):
    # the live view is a stream (MJPEG) now, up to 24 pictures a second
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("{}\n")
    p = pm.Panel(str(tmp_path / "config.yaml"))
    n = {"i": 0}

    class GB:
        hwnd, last_frame, last_frame_t = 1, None, 0.0

        def screenshot(self, area=None):
            n["i"] += 1
            return np.full((1024, 1821, 3), n["i"] % 250, np.uint8)
    p.worker.gb = GB()
    srv = ThreadingHTTPServer(("127.0.0.1", 0), pm.make_handler(p, {"port": 0, "lan": False}))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        r = urllib.request.urlopen(f"http://127.0.0.1:{srv.server_port}/live.mjpg?w=640", timeout=10)
        assert "multipart/x-mixed-replace" in r.headers["Content-Type"]
        buf = b""
        while buf.count(b"\xff\xd8") < 3:                      # three pictures arrive
            buf += r.read1(65536)
        r.close()
    finally:
        srv.shutdown()
