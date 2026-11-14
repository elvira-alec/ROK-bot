"""Release helper: talks to GitHub with the sign-in Git Credential Manager already stored -
never a popup (GCM_INTERACTIVE=never; the account is pinned in this repo's git config:
credential.https://github.com.username). Needs the token's `repo` + `workflow` scopes.

    python tools/gh.py runs                     recent workflow runs
    python tools/gh.py cancel <run id>
    python tools/gh.py cancel-running           cancel every queued / running windows-exe run
    python tools/gh.py publish <version>        cancel the plain push builds, start the publish
    python tools/gh.py wait-release <version>   wait until releases/tag/v<version> exists
    python tools/gh.py log <run id>             the failed lines of a run's jobs
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = "elvira-alec/ROK-bot"
try:                                    # the same certificates the app's updater uses
    import ssl
    import certifi
    _CTX = ssl.create_default_context(cafile=certifi.where())
    urllib.request.install_opener(urllib.request.build_opener(urllib.request.HTTPSHandler(context=_CTX)))
except Exception:
    _CTX = None
API = f"https://api.github.com/repos/{REPO}"


def token():
    env = dict(os.environ, GCM_INTERACTIVE="never", GIT_TERMINAL_PROMPT="0")
    user = subprocess.run(["git", "config", "--get", "credential.https://github.com.username"],
                          capture_output=True, text=True).stdout.strip() or "elvira-alec"
    out = subprocess.run(["git", "credential", "fill"], capture_output=True, text=True, timeout=30, env=env,
                         input=f"protocol=https\nhost=github.com\nusername={user}\n\n").stdout
    t = next((l.split("=", 1)[1] for l in out.splitlines() if l.startswith("password=")), None)
    if not t:
        sys.exit("No stored GitHub sign-in for this repo (run one `git push` by hand once).")
    return t


H = None


def call(method, path, body=None):
    global H
    H = H or {"Authorization": f"Bearer {token()}", "Accept": "application/vnd.github+json",
              "User-Agent": "rokwatch-release"}
    url = path if path.startswith("http") else API + path
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers=H, method=method)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt.strip().startswith(("{", "[")) else txt)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:400]


def runs(n=8):
    return call("GET", f"/actions/runs?per_page={n}")[1]["workflow_runs"]


def main(argv):
    cmd = argv[0] if argv else "runs"
    if cmd == "runs":
        for r in runs():
            print(r["id"], r["name"], r["event"], r["status"], r["conclusion"], r["head_sha"][:7], r["created_at"])
    elif cmd == "cancel":
        print(call("POST", f"/actions/runs/{argv[1]}/cancel")[0])
    elif cmd == "cancel-running":
        for r in runs(20):
            if r["name"] == "windows-exe" and r["status"] in ("queued", "in_progress", "waiting"):
                print("cancel", r["id"], r["event"], r["head_sha"][:7], call("POST", f"/actions/runs/{r['id']}/cancel")[0])
    elif cmd == "publish":
        ver = argv[1]
        for r in runs(20):     # plain push builds would hold up the publish (one build at a time)
            if r["name"] == "windows-exe" and r["event"] == "push" and r["status"] in ("queued", "in_progress"):
                print("cancel push build", r["id"], call("POST", f"/actions/runs/{r['id']}/cancel")[0])
        st, body = call("POST", "/actions/workflows/windows-exe.yml/dispatches",
                        {"ref": "main", "inputs": {"publish": "true", "version": ver}})
        print("publish build started" if st == 204 else f"failed: {st} {body}")
    elif cmd == "wait-release":
        ver, end = argv[1], time.time() + float(argv[2] if len(argv) > 2 else 2400)
        while time.time() < end:
            st, body = call("GET", f"/releases/tags/v{ver}")
            if st == 200:
                print("released:", body.get("html_url"), [a["name"] for a in body.get("assets", [])])
                return
            time.sleep(30)
        sys.exit(f"v{ver} not released yet")
    elif cmd == "log":
        jobs = call("GET", f"/actions/runs/{argv[1]}/jobs")[1]["jobs"]

        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        for j in jobs:
            print("JOB", j["name"], j["conclusion"])
            if j["conclusion"] != "failure":
                continue
            try:
                urllib.request.build_opener(NoRedirect, urllib.request.HTTPSHandler(context=_CTX)).open(
                    urllib.request.Request(f"{API}/actions/jobs/{j['id']}/logs", headers=H), timeout=60)
                continue
            except urllib.error.HTTPError as e:
                loc = e.headers.get("Location")
            log = urllib.request.urlopen(loc, timeout=60).read().decode(errors="replace")
            for line in log.splitlines():
                line = re.sub(r"^\S+Z ", "", line)
                if re.search(r"FAILED|ERROR |[0-9]+ (passed|failed)|^E  |Error:", line):
                    print("  ", line[:300])


if __name__ == "__main__":
    main(sys.argv[1:])
