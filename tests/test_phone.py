"""The control panel on a phone: over the network, only with the secret key."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest
import yaml

from rokwatch import config
from rokwatch.panel import Panel, lan_ip, make_handler


def start(tmp_path, phone):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"panel_on_phone": phone, "notify": {}}))
    p = Panel(str(cfg))
    holder = {"port": 0, "lan": phone}
    srv = ThreadingHTTPServer(("0.0.0.0", 0), make_handler(p, holder))
    holder["port"] = srv.server_port
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return p, srv


def get(url, cookie=None, data=None, origin=None):
    req = urllib.request.Request(url, data=data)
    if cookie:
        req.add_header("Cookie", cookie)
    if origin:
        req.add_header("Origin", origin)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        r = urllib.request.urlopen(req, timeout=5)
        return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


@pytest.fixture
def ip():
    a = lan_ip()
    if a.startswith("127."):
        pytest.skip("no network address on this machine")
    return a


def test_phone_needs_the_key(tmp_path, ip):
    p, srv = start(tmp_path, phone=True)
    base = f"http://{ip}:{srv.server_port}"
    try:
        assert get(base + "/")[0] == 403                              # no key: locked
        assert get(base + "/?key=wrong")[0] == 403
        key = p.phone_key()
        code, body, headers = get(base + f"/?key={key}")
        assert code == 200 and b"RoK Watcher" in body
        cookie = headers["Set-Cookie"].split(";")[0]                  # the phone keeps the key
        assert get(base + "/api/state", cookie=cookie)[0] == 200
        assert get(base + "/api/state")[0] == 403
        # buttons work from the phone page
        code, body, _ = get(base + "/api/settings", cookie=cookie, data=json.dumps(
            {"ntfy_topic": "from-phone"}).encode(), origin=base)
        assert code == 200 and config.load(p.cfg_path)["notify"]["ntfy_topic"] == "from-phone"
        # another website can't press them, even with the cookie
        code, _, _ = get(base + "/api/stop", cookie=cookie, data=b"{}", origin="http://evil.example")
        assert code == 403
        # the laptop itself never needs the key, and gets the QR code
        code, body, _ = get(f"http://127.0.0.1:{srv.server_port}/api/phone")
        info = json.loads(body)
        assert info["url"].endswith(f"?key={key}") and info["qr"].startswith("<?xml")
        # the phone isn't shown the QR / key again
        assert json.loads(get(base + "/api/phone", cookie=cookie)[1])["on_phone"] is True
    finally:
        srv.shutdown()


def test_phone_switched_off_means_locked(tmp_path, ip):
    p, srv = start(tmp_path, phone=False)
    base = f"http://{ip}:{srv.server_port}"
    try:
        assert get(base + f"/?key={p.phone_key()}")[0] == 403
        assert get(f"http://127.0.0.1:{srv.server_port}/")[0] == 200
    finally:
        srv.shutdown()
