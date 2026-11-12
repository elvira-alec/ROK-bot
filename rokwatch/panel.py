"""The control panel: a small web page on this computer (http://localhost:8765).

    python -m rokwatch.panel        (or double-click RoKWatcher.exe)
"""

from __future__ import annotations

import hmac
import json
import logging
import secrets
import socket
import threading
import webbrowser
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config
from .patrol import activity_report
from .worker import MemoryLog, Worker

log = logging.getLogger("rokwatch")
PAGE = Path(__file__).with_name("panel.html")

# settings the panel can change (saved to setup.yaml, so config.yaml keeps its comments)
EDITABLE = {
    "game_url": "game_url",
    "game_window": "game_window",
    "game_window_zoom_key": "game_window_zoom_key",
    "ntfy_topic": "notify.ntfy_topic",
    "discord_webhook": "notify.discord_webhook",
    "sites": "patrol.sites",
    "auto_recall": "patrol.auto_recall",
    "recall_scope": "patrol.recall_scope",
    "player_name": "hunt.player_name",
    "panel_on_phone": "panel_on_phone",
    "hunt_radius": "hunt.radius",
}


def _get(cfg, dotted):
    node = cfg
    for part in dotted.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _clean_sites(sites):
    out = []
    for s in sites or []:
        try:
            out.append({"name": str(s.get("name") or "").strip() or f"{s['x']},{s['y']}",
                        "x": int(s["x"]), "y": int(s["y"]),
                        "kind": "enemy" if str(s.get("kind", "")).startswith("enem") else "mine"})
        except (KeyError, TypeError, ValueError):
            continue
    return out


class Panel:
    def __init__(self, cfg_path=config.DEFAULT_PATH):
        self.cfg_path = cfg_path
        self.worker = Worker(cfg_path)
        self.memlog = MemoryLog()
        logging.getLogger("rokwatch").addHandler(self.memlog)
        logging.getLogger("rokwatch").setLevel(logging.INFO)

    def phone_key(self) -> str:
        """Secret that a phone must present (in the link / QR code). Kept in setup.yaml."""
        key = _get(config.load(self.cfg_path), "panel_key")
        if not key:
            key = secrets.token_urlsafe(12)
            config.save_fields({"panel_key": key}, self.cfg_path)
        return key

    def phone_info(self, port, listening_on_lan):
        enabled = bool(_get(config.load(self.cfg_path), "panel_on_phone"))
        info = {"enabled": enabled, "active": listening_on_lan, "url": None, "qr": None}
        if enabled and listening_on_lan:
            url = f"http://{lan_ip()}:{port}/?key={self.phone_key()}"
            info["url"] = url
            try:
                import io
                import segno
                buf = io.BytesIO()
                segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, dark="#000",
                                                  light="#fff")
                info["qr"] = buf.getvalue().decode()
            except Exception as e:     # the link alone still works
                log.debug("QR code failed: %s", e)
        return info

    def settings(self):
        cfg = config.load(self.cfg_path)
        out = {k: _get(cfg, v) for k, v in EDITABLE.items()}
        out["axes_learned"] = bool(_get(cfg, "patrol.axes"))
        out["close_axes_learned"] = bool(_get(cfg, "hunt.axes_close"))
        out["panel_on_phone"] = bool(_get(cfg, "panel_on_phone"))
        return out

    def save(self, data: dict):
        updates = {}
        for k, dotted in EDITABLE.items():
            if k in data:
                v = data[k]
                if k == "sites":
                    v = _clean_sites(v)
                elif k in ("auto_recall", "panel_on_phone"):
                    v = bool(v)
                elif k == "recall_scope":
                    v = "all" if v == "all" else "threatened"
                elif k == "game_window_zoom_key":
                    v = "ctrl" if str(v).lower() in ("ctrl", "control", "true", "1") else ""
                elif k == "hunt_radius":
                    try:
                        v = max(5, min(80, int(v)))
                    except (TypeError, ValueError):
                        v = 25
                else:
                    v = str(v or "").strip()
                updates[dotted] = v
        config.save_fields(updates, self.cfg_path)
        log.info("Settings saved")

    def state(self):
        st = self.worker.status
        cfg = config.load(self.cfg_path)
        return {
            "phase": st.phase, "message": st.message, "round": st.round,
            "last_site": st.last_site, "alerts": st.alerts, "look": st.look,
            "armies": st.armies,
            "log": list(self.memlog.lines)[-80:],
            "report": activity_report(_get(cfg, "patrol.activity_log") or "activity.csv"),
        }


def lan_ip() -> str:
    """This computer's address on the home network (what the phone connects to)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("10.255.255.255", 1))        # no packet is sent
            return sk.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def make_handler(panel: Panel, port_holder=None):
    """port_holder: {"port": int, "lan": bool} filled in once the server is up."""
    port_holder = port_holder if port_holder is not None else {"port": 0, "lan": False}

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, code, body, ctype="application/json", cookie=None):
            data = body if isinstance(body, bytes) else json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            if cookie:
                self.send_header("Set-Cookie", f"rokkey={cookie}; Path=/; SameSite=Strict; HttpOnly")
            self.end_headers()
            self.wfile.write(data)

        def _local(self) -> bool:
            return self.client_address[0] in ("127.0.0.1", "::1", "::ffff:127.0.0.1")

        def _key_given(self):
            q = parse_qs(urlsplit(self.path).query).get("key", [""])[0]
            c = SimpleCookie(self.headers.get("Cookie", "")).get("rokkey")
            return q or (c.value if c else "")

        def _allowed(self) -> bool:
            """This laptop: always. Anything else (the phone): only with the secret key,
            and only when 'panel on phone' is switched on."""
            if self._local():
                return True
            if not _get(config.load(panel.cfg_path), "panel_on_phone"):
                return False
            return hmac.compare_digest(self._key_given(), panel.phone_key())

        def _locked(self):
            self._send(403, b"<h3 style='font-family:sans-serif'>Locked. Open the link or scan the "
                            b"QR code shown in the panel on your laptop.</h3>",
                       "text/html; charset=utf-8")

        def do_GET(self):
            if not self._allowed():
                return self._locked()
            path = urlsplit(self.path).path
            if path in ("/", "/index.html"):
                key = self._key_given() if not self._local() else None
                return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8", cookie=key)
            if path == "/api/state":
                return self._send(200, panel.state())
            if path == "/api/settings":
                return self._send(200, panel.settings())
            if path == "/api/phone":
                if not self._local():
                    return self._send(200, {"enabled": True, "active": True, "url": None,
                                            "qr": None, "on_phone": True})
                return self._send(200, panel.phone_info(port_holder["port"], port_holder["lan"]))
            if path.startswith("/view.png"):
                p = Path(panel.worker.shot_path)
                return self._send(200, p.read_bytes(), "image/png") if p.exists() else \
                    self._send(404, {"error": "no view yet"})
            self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._allowed():
                return self._locked()
            n = int(self.headers.get("Content-Length") or 0)
            # only accept requests from the panel page itself (not other websites)
            origin = self.headers.get("Origin")
            host = self.headers.get("Host", "")
            if origin and not (origin.startswith(("http://127.0.0.1", "http://localhost"))
                               or origin == f"http://{host}"):
                return self._send(403, {"error": "forbidden"})
            try:
                data = json.loads(self.rfile.read(n) or b"{}")
            except ValueError:
                return self._send(400, {"error": "bad json"})
            path = urlsplit(self.path).path
            if path == "/api/settings":
                panel.save(data)
                return self._send(200, {"ok": True})
            cmds = {"/api/open": "open", "/api/close": "close", "/api/start": "start",
                    "/api/stop": "stop", "/api/look": "look", "/api/axes": "axes",
                    "/api/recall": "recall", "/api/test_alert": "test_alert",
                    "/api/hunt": "hunt"}
            if path in cmds:
                panel.worker.send(cmds[path])
                return self._send(200, {"ok": True})
            if path == "/api/goto":
                try:
                    panel.worker.send("goto", x=int(data["x"]), y=int(data["y"]))
                except (KeyError, ValueError):
                    return self._send(400, {"error": "x and y needed"})
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

    return H


def serve(cfg_path=config.DEFAULT_PATH, port=8765, open_browser=True):
    from pathlib import Path as P
    Path("debug").mkdir(exist_ok=True)
    if not P(cfg_path).exists() and P("config.example.yaml").exists():
        P(cfg_path).write_text(P("config.example.yaml").read_text())
    panel = Panel(cfg_path)
    # On the home network too (for the phone) only when switched on; key-protected.
    phone = bool(_get(config.load(cfg_path), "panel_on_phone"))
    host = "0.0.0.0" if phone else "127.0.0.1"
    holder = {"port": port, "lan": phone}
    try:
        srv = ThreadingHTTPServer((host, port), make_handler(panel, holder))
    except OSError:  # port taken (maybe it's already open): use any free one
        srv = ThreadingHTTPServer((host, 0), make_handler(panel, holder))
    holder["port"] = srv.server_port
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(f"RoK watcher control panel: {url}  (keep this window open; Ctrl+C to quit)")
    if phone:
        print(f"On your phone (same Wi-Fi): http://{lan_ip()}:{srv.server_port}/?key={panel.phone_key()}")
        print("If Windows asks whether to allow RoKWatcher on networks, click Allow (private).")
    if open_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        panel.worker.send("quit")
    return srv


if __name__ == "__main__":
    import sys
    from .__main__ import setup_logging
    setup_logging()
    serve(sys.argv[1] if len(sys.argv) > 1 else config.DEFAULT_PATH)
