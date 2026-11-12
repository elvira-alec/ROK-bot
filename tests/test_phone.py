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
    holder = {"port": 0, "lan": True}      # serve() always listens on the network
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


def test_switching_on_needs_no_restart(tmp_path, ip):
    """The panel always listens; switching 'panel on phone' on lets the phone in at once."""
    p, srv = start(tmp_path, phone=False)
    base = f"http://{ip}:{srv.server_port}"
    try:
        key = p.phone_key()
        assert get(base + f"/?key={key}")[0] == 403
        p.save({"panel_on_phone": True})                       # what the checkbox does
        assert get(base + f"/?key={key}")[0] == 200
        info = json.loads(get(f"http://127.0.0.1:{srv.server_port}/api/phone")[1])
        assert info["enabled"] and info["options"][0]["kind"] == "local"
        assert info["options"][0]["url"].endswith(f"?key={key}")
        p.save({"panel_on_phone": False})
        assert get(base + f"/?key={key}")[0] == 403
    finally:
        srv.shutdown()


def test_tailscale_and_local_qr_codes(tmp_path, monkeypatch):
    import rokwatch.panel as panel_mod
    monkeypatch.setattr(panel_mod, "lan_ip", lambda: "192.168.1.20")
    monkeypatch.setattr(panel_mod, "tailscale_address", lambda: "100.101.7.9")
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"panel_on_phone": True}))
    p = Panel(str(cfg))
    info = p.phone_info(8765)
    opts = {o["kind"]: o for o in info["options"]}
    key = p.phone_key()
    assert opts["local"]["url"] == f"http://192.168.1.20:8765/?key={key}"
    assert opts["tailscale"]["url"] == f"http://100.101.7.9:8765/?key={key}"
    assert opts["local"]["qr"].startswith("<?xml") and opts["tailscale"]["qr"].startswith("<?xml")
    assert opts["local"]["qr"] != opts["tailscale"]["qr"]
    # Tailscale not running: the option explains what to do instead of a QR code
    monkeypatch.setattr(panel_mod, "tailscale_address", lambda: None)
    opts = {o["kind"]: o for o in p.phone_info(8765)["options"]}
    assert opts["tailscale"]["url"] is None and "Install Tailscale" in opts["tailscale"]["help"]
    assert opts["local"]["url"]


def test_tailscale_address_detection(monkeypatch):
    import subprocess
    import rokwatch.panel as panel_mod
    assert panel_mod.is_tailscale_ip("100.64.0.1") and panel_mod.is_tailscale_ip("100.127.255.254")
    assert not panel_mod.is_tailscale_ip("100.128.0.1") and not panel_mod.is_tailscale_ip("192.168.0.5")

    class R:
        stdout = "100.88.12.34\n"
    monkeypatch.setattr(panel_mod, "_tailscale_exe", lambda: "tailscale")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: R())
    panel_mod._ts_cache.update(t=0.0, ip=None)
    assert panel_mod.tailscale_address() == "100.88.12.34"
    panel_mod._ts_cache.update(t=0.0, ip=None)


def test_tailscale_phone_gets_in(tmp_path, monkeypatch):
    """A request arriving on the Tailscale address (Host: 100.x) works like the Wi-Fi one,
    buttons included (Origin = that address)."""
    p, srv = start(tmp_path, phone=True)
    try:
        key = p.phone_key()
        # pretend to be a remote client: the handler only sees the address, so use the
        # machine's own non-loopback address when there is one
        a = lan_ip()
        if a.startswith("127."):
            pytest.skip("no network address on this machine")
        base = f"http://{a}:{srv.server_port}"
        code, _, headers = get(base + f"/?key={key}")
        cookie = headers["Set-Cookie"].split(";")[0]
        req = urllib.request.Request(base + "/api/settings", data=b'{"hunt_radius": 30}')
        req.add_header("Cookie", cookie)
        req.add_header("Host", f"100.101.7.9:{srv.server_port}")
        req.add_header("Origin", f"http://100.101.7.9:{srv.server_port}")
        req.add_header("Content-Type", "application/json")
        assert urllib.request.urlopen(req, timeout=5).status == 200
        assert config.load(p.cfg_path)["hunt"]["radius"] == 30
    finally:
        srv.shutdown()


def test_snapshots_need_the_key(tmp_path, ip, monkeypatch):
    import numpy as np
    from rokwatch import snapshots
    monkeypatch.chdir(tmp_path)
    name = snapshots.save(np.full((40, 60, 3), 90, np.uint8), "alert His army", {"x": 1})
    p, srv = start(tmp_path, phone=True)
    base = f"http://{ip}:{srv.server_port}"
    try:
        assert get(base + f"/snap/{name}")[0] == 403
        code, body, _ = get(base + f"/snap/{name}?key={p.phone_key()}")
        assert code == 200 and body[:3] == b"\xff\xd8\xff"            # a jpeg
        local = f"http://127.0.0.1:{srv.server_port}"
        assert get(local + "/snap/..%2Fconfig.yaml")[0] == 404
        assert get(local + "/snap/../config.yaml")[0] == 404
        code, body, _ = get(local + "/api/snapshots.zip")
        assert code == 200 and body[:2] == b"PK"
        st = json.loads(get(local + "/api/state")[1])
        assert st["snaps"][0]["name"] == name and st["snaps"][0]["tag"] == "alert His army"
    finally:
        srv.shutdown()


def test_targets_are_remembered(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(yaml.safe_dump({"patrol": {"sites": [{"name": "his", "x": 162, "y": 374, "kind": "enemy"}]}}))
    p = Panel(str(cfg))
    assert p.settings()["hunt_city"] == [162, 374]           # old setups: from the places list
    p.save({"player_name": "[P83L]JONH WICK", "hunt_city": [162, 374]})
    p.save({"player_name": "[XX]Other Guy", "hunt_city": ["300", "400"]})
    s = p.settings()
    assert [t["name"] for t in s["targets"]] == ["[XX]Other Guy", "[P83L]JONH WICK"]
    assert s["targets"][1]["x"] == 162 and s["hunt_city"] == [300, 400]
    p.save({"player_name": "[P83L]JONH WICK", "hunt_city": [162, 374]})     # picked again
    s = p.settings()
    assert s["targets"][0]["name"] == "[P83L]JONH WICK" and len(s["targets"]) == 2
    assert config.load(p.cfg_path)["hunt"]["city"] == [162, 374]
    p.save({"forget_target": "[XX]Other Guy"})
    assert [t["name"] for t in p.settings()["targets"]] == ["[P83L]JONH WICK"]
