"""The control panel: a small web page on this computer (http://localhost:8765).

    python -m rokwatch.panel        (or double-click RoKWatcher.exe)
"""

from __future__ import annotations

import hmac
import ipaddress
import json
import logging
import os
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config, snapshots, updater
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
    "snap_every": "snapshots.every_minutes",
    "hunt_city": "hunt.city",
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
        self.version = updater.current_version()
        self.update = None          # newer release found on GitHub
        self.update_at = None       # when it installs by itself (None = waiting for you)
        self.updating = None        # version being installed
        self.last_request = 0.0

    # ------------------------------------------------------------ updates
    def check_for_update(self):
        try:
            info = updater.latest_release()
        except Exception as e:
            log.debug("update check failed: %s", e)
            return None
        if info and updater.newer(info["version"], self.version):
            if not self.update or self.update["version"] != info["version"]:
                log.info("Version %s is available (you have %s)", info["version"], self.version)
                self.update = info
                busy = self.worker.status.phase in ("patrolling", "busy", "opening")
                # installs by itself in a minute unless you're running something
                self.update_at = None if busy else time.time() + 60
        return self.update

    def update_tick(self):
        if (self.update and self.update_at and time.time() >= self.update_at and not self.updating
                and self.worker.status.phase not in ("patrolling", "busy", "opening")):
            self.install_update()

    def install_update(self):
        if not self.update:
            return "No update to install"
        if not updater.can_self_update():
            return (f"Version {self.update['version']} is out. This copy can't update itself "
                    "(not the Windows .exe): download it from GitHub.")
        self.updating = self.update["version"]
        self.worker.send("stop")
        updater.launch_updater(self.update)
        # close this program so its files can be replaced (the updater waits for it)
        threading.Timer(1.5, lambda: os._exit(0)).start()
        return "Updating"

    def phone_key(self) -> str:
        """Secret that a phone must present (in the link / QR code). Kept in setup.yaml."""
        key = _get(config.load(self.cfg_path), "panel_key")
        if not key:
            key = secrets.token_urlsafe(12)
            config.save_fields({"panel_key": key}, self.cfg_path)
        return key

    def phone_info(self, port, listening_on_lan=True):
        """Links + QR codes for the phone: 'local' (same Wi-Fi) and 'tailscale' (anywhere,
        once Tailscale runs on this laptop and the phone with the same account)."""
        enabled = bool(_get(config.load(self.cfg_path), "panel_on_phone"))
        info = {"enabled": enabled, "active": listening_on_lan, "url": None, "qr": None,
                "options": []}
        if not (enabled and listening_on_lan):
            return info
        key = self.phone_key()
        local = lan_ip()
        ts = tailscale_address()
        routes = [("local", "Home Wi-Fi", local if not is_tailscale_ip(local) else None)]
        routes.append(("tailscale", "Tailscale (from anywhere)", ts))
        for kind, label, host in routes:
            opt = {"kind": kind, "label": label, "url": None, "qr": None}
            if host:
                opt["url"] = f"http://{host}:{port}/?key={key}"
                opt["qr"] = _qr_svg(opt["url"])
            elif kind == "tailscale":
                opt["help"] = ("Tailscale isn't running on this laptop. Install Tailscale on this "
                               "laptop and on your phone, sign in to both with the same account, "
                               "then come back here (no restart needed).")
            else:
                opt["help"] = "This laptop isn't on a home network right now."
            info["options"].append(opt)
        first = next((o for o in info["options"] if o["url"]), None)
        if first:
            info["url"], info["qr"] = first["url"], first["qr"]
        return info

    def live_id(self):
        f = getattr(self.worker.gb, "last_frame", None) if self.worker.gb else None
        return id(f) if f is not None else None

    def live_jpeg(self, width=640, quality=55):
        """Small, low-quality picture of what the bot last saw (for the live view)."""
        import cv2
        f = getattr(self.worker.gb, "last_frame", None) if self.worker.gb else None
        if f is None:
            return None
        key = (id(f), width)
        if getattr(self, "_live_key", None) != key:
            h, w = f.shape[:2]
            small = cv2.resize(f, (width, max(1, int(h * width / w))), interpolation=cv2.INTER_AREA)
            self._live = cv2.imencode(".jpg", small, [cv2.IMWRITE_JPEG_QUALITY, quality])[1].tobytes()
            self._live_key = key
        return self._live

    def settings(self):
        cfg = config.load(self.cfg_path)
        out = {k: _get(cfg, v) for k, v in EDITABLE.items()}
        out["axes_learned"] = bool(_get(cfg, "patrol.axes"))
        out["close_axes_learned"] = bool(_get(cfg, "hunt.axes_close"))
        out["zoom_notches"] = _get(cfg, "hunt.zoom_notches")
        out["close_notches"] = _get(cfg, "hunt.close_notches")
        out["panel_on_phone"] = bool(_get(cfg, "panel_on_phone"))
        out["targets"] = _get(cfg, "targets") or []
        if not out["hunt_city"]:                      # older setups: his city from the places list
            enemy = next((x for x in (_get(cfg, "patrol.sites") or []) if x.get("kind") == "enemy"), None)
            out["hunt_city"] = [enemy["x"], enemy["y"]] if enemy else None
        se = _get(cfg, "snapshots.every_minutes")
        out["snap_every"] = 10 if se is None else se
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
                elif k == "hunt_city":
                    try:
                        v = [int(v[0]), int(v[1])]
                    except (TypeError, ValueError, IndexError, KeyError):
                        continue
                elif k == "snap_every":
                    try:
                        v = max(0, min(240, float(v)))
                    except (TypeError, ValueError):
                        v = 10
                elif k == "hunt_radius":
                    try:
                        v = max(5, min(80, int(v)))
                    except (TypeError, ValueError):
                        v = 25
                else:
                    v = str(v or "").strip()
                updates[dotted] = v
        # remember every target (name + city) so it can be picked from a list later
        cfg = config.load(self.cfg_path)
        targets = [t for t in (_get(cfg, "targets") or []) if isinstance(t, dict) and t.get("name")]
        if "forget_target" in data:
            targets = [t for t in targets if t["name"] != str(data["forget_target"])]
            updates["targets"] = targets
        name = updates.get("hunt.player_name")
        city = updates.get("hunt.city") or _get(cfg, "hunt.city")
        if name:
            targets = [t for t in targets if t["name"] != name]
            targets.insert(0, {"name": name, "x": city[0] if city else None,
                               "y": city[1] if city else None})
            updates["targets"] = targets[:20]
        config.save_fields(updates, self.cfg_path)
        log.info("Settings saved")

    def state(self):
        st = self.worker.status
        cfg = config.load(self.cfg_path)
        self.last_request = time.time()
        return {
            "version": self.version,
            "update": self.update,
            "update_in": max(0, int(self.update_at - time.time())) if self.update_at else None,
            "updating": self.updating,
            "phase": st.phase, "message": st.message, "round": st.round,
            "last_site": st.last_site, "alerts": st.alerts, "look": st.look,
            "armies": st.armies,
            "snaps": snapshots.listing(limit=24),
            "progress": st.progress, "doing": st.doing,
            "live": self.live_id(),
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


TAILSCALE_NET = ipaddress.ip_network("100.64.0.0/10")
_ts_cache = {"t": 0.0, "ip": None}


def is_tailscale_ip(a) -> bool:
    try:
        return ipaddress.ip_address(a) in TAILSCALE_NET
    except ValueError:
        return False


def _tailscale_exe():
    exe = shutil.which("tailscale")
    if exe:
        return exe
    for p in (r"C:\Program Files\Tailscale\tailscale.exe",
              r"C:\Program Files (x86)\Tailscale\tailscale.exe",
              "/Applications/Tailscale.app/Contents/MacOS/Tailscale"):
        if os.path.exists(p):
            return p
    return None


def tailscale_address(max_age=20.0):
    """This laptop's Tailscale address (100.x.y.z), or None when Tailscale isn't up.
    Checked again every max_age seconds, so starting Tailscale needs no restart here."""
    now = time.time()
    if now - _ts_cache["t"] < max_age:
        return _ts_cache["ip"]
    ip = None
    exe = _tailscale_exe()
    if exe:
        try:
            flags = 0x08000000 if sys.platform == "win32" else 0      # no console window
            out = subprocess.run([exe, "ip", "-4"], capture_output=True, text=True,
                                 timeout=4, creationflags=flags).stdout
            ip = next((a.strip() for a in out.split() if is_tailscale_ip(a.strip())), None)
        except (OSError, subprocess.SubprocessError):
            pass
    if not ip:          # no command line tool: look at this computer's own addresses
        try:
            addrs = {i[4][0] for i in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)}
            ip = next((a for a in sorted(addrs) if is_tailscale_ip(a)), None)
        except OSError:
            pass
    _ts_cache.update(t=now, ip=ip)
    return ip


def _qr_svg(url):
    try:
        import io
        import segno
        buf = io.BytesIO()
        segno.make(url, error="m").save(buf, kind="svg", scale=6, border=2, dark="#000", light="#fff")
        return buf.getvalue().decode()
    except Exception as e:     # the link alone still works
        log.debug("QR code failed: %s", e)
        return None


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
            if path.startswith("/snap/"):
                p = snapshots.path_of(path[len("/snap/"):])
                if not p:
                    return self._send(404, {"error": "no such snapshot"})
                ctype = {".png": "image/png", ".jpg": "image/jpeg"}.get(p.suffix, "application/json")
                return self._send(200, p.read_bytes(), ctype)
            if path == "/api/snapshots.zip":
                data = snapshots.zip_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/zip")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="rok-snapshots-{time.strftime("%Y%m%d-%H%M")}.zip"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            if path.startswith("/live.jpg"):
                q = parse_qs(urlsplit(self.path).query)
                try:
                    width = max(240, min(1280, int(q.get("w", ["640"])[0])))
                except ValueError:
                    width = 640
                data = panel.live_jpeg(width)
                return self._send(200, data, "image/jpeg") if data else \
                    self._send(404, {"error": "nothing seen yet"})
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
            if path == "/api/update":
                if data.get("action") == "later":
                    panel.update_at = None
                    return self._send(200, {"ok": True})
                if data.get("action") == "check":
                    info = panel.check_for_update()
                    return self._send(200, {"ok": True, "update": info})
                msg = panel.install_update()
                return self._send(200, {"ok": msg == "Updating", "message": msg,
                                        "version": panel.updating})
            if path == "/api/remember_zoom":
                panel.worker.send("remember_zoom", kind="close" if data.get("kind") == "close" else "sweep")
                return self._send(200, {"ok": True})
            if path in cmds:
                panel.worker.send(cmds[path])
                return self._send(200, {"ok": True})
            if path == "/api/snap":
                panel.worker.request_snap(str(data.get("note") or "")[:60])
                return self._send(200, {"ok": True})
            if path == "/api/goto":
                try:
                    panel.worker.send("goto", x=int(data["x"]), y=int(data["y"]))
                except (KeyError, ValueError):
                    return self._send(400, {"error": "x and y needed"})
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})

    return H


def _update_loop(panel, every=1800):
    time.sleep(5)
    nxt = 0.0
    while True:
        if time.time() >= nxt:
            panel.check_for_update()
            nxt = time.time() + every
        panel.update_tick()
        time.sleep(1)


def serve(cfg_path=config.DEFAULT_PATH, port=8765, open_browser=True):
    """open_browser: True, False, or "if_no_client" (after an update the old tab comes
    back by itself; open a new one only if it doesn't)."""
    from pathlib import Path as P
    Path("debug").mkdir(exist_ok=True)
    if not P(cfg_path).exists() and P("config.example.yaml").exists():
        P(cfg_path).write_text(P("config.example.yaml").read_text())
    panel = Panel(cfg_path)
    # Always listening on the network so 'panel on phone' (home Wi-Fi or Tailscale) can be
    # switched on without a restart. Nothing gets in from outside this laptop unless it's
    # switched on AND the request carries the secret key (see _allowed).
    phone = bool(_get(config.load(cfg_path), "panel_on_phone"))
    holder = {"port": port, "lan": True}
    try:
        srv = ThreadingHTTPServer(("0.0.0.0", port), make_handler(panel, holder))
    except OSError:  # port taken (maybe it's already open): use any free one
        srv = ThreadingHTTPServer(("0.0.0.0", 0), make_handler(panel, holder))
    holder["port"] = srv.server_port
    url = f"http://127.0.0.1:{srv.server_port}/"
    print(f"RoK Watcher {panel.version} control panel: {url}  (keep this window open; Ctrl+C to quit)")
    print("If Windows asks whether to allow RoKWatcher on networks, tick BOTH boxes (Private and "
          "Public) and click Allow: that's what lets your phone in (it still needs the secret key).")
    if phone:
        print(f"On your phone (same Wi-Fi): http://{lan_ip()}:{srv.server_port}/?key={panel.phone_key()}")
        if tailscale_address():
            print(f"On your phone (Tailscale): http://{tailscale_address()}:{srv.server_port}/"
                  f"?key={panel.phone_key()}")
    threading.Thread(target=_update_loop, args=(panel,), daemon=True).start()
    if open_browser == "if_no_client":
        def maybe_open():
            if time.time() - panel.last_request > 10:
                webbrowser.open(url)
        threading.Timer(12, maybe_open).start()
    elif open_browser:
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
