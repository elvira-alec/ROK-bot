"""Live view: about 3 pictures a second while the bot's picture changes, none sent while
it doesn't."""

import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pytest

pytest.importorskip("playwright")

from rokwatch.panel import Panel, make_handler  # noqa: E402


def test_live_view_updates_a_few_times_a_second(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "config.yaml").write_text("{}\n")
    p = Panel(str(tmp_path / "config.yaml"))

    class GB:
        last_frame = np.zeros((540, 960, 3), np.uint8)
    p.worker.gb = GB()
    stop = threading.Event()

    def bot():                                     # the bot grabs a new picture every 0.1 s
        i = 0
        while not stop.is_set():
            f = np.full((540, 960, 3), i % 250, np.uint8)
            GB.last_frame = f
            i += 1
            time.sleep(0.1)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(p))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_port}"
    # unchanged picture: nothing sent
    lid = str(p.live_id())
    r = urllib.request.urlopen(f"{base}/live.jpg?w=960&have={lid}")
    assert r.status == 204
    r = urllib.request.urlopen(f"{base}/live.jpg?w=960")
    assert r.status == 200 and r.headers["X-Live"] == lid
    threading.Thread(target=bot, daemon=True).start()
    from playwright.sync_api import sync_playwright
    with sync_playwright() as pw:
        from test_end_to_end import EXE
        b = pw.chromium.launch(executable_path=EXE or None)
        page = b.new_page(viewport={"width": 1300, "height": 900})
        page.goto(base + "/")
        page.wait_for_function("document.getElementById('live-img').src.startsWith('blob:')", timeout=10000)
        seen = set()
        t0 = time.time()
        while time.time() - t0 < 3:
            seen.add(page.eval_on_selector("#live-img", "e => e.src"))
            time.sleep(0.05)
        b.close()
    stop.set()
    srv.shutdown()
    assert 6 <= len(seen) <= 14, len(seen)         # ~3 a second
