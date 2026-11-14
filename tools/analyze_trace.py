"""Check a debug recording (debug_trace/<run>/trace.jsonl) for the problems we've hit.

    python tools/analyze_trace.py debug_trace/20261114-011536 [--setup setup.yaml]

Prints: the rounds (mode, armies, owners), every army id's track (a farm whose position
spans more than a few tiles, or whose state flips, is flagged - that's what showed as "his
troops moved"), owner checks and how many came back unclear, warnings (failed jumps,
farms that wouldn't open, wrong box tapped, zoom changes), round timings, what each look
found, and a fit of the map scale from the drags and camera readings (if it's far from
the saved patrol.axes, positions are off).
"""

from __future__ import annotations

import argparse
import collections
import json
import math
import re
from pathlib import Path

import numpy as np


def load(run):
    p = Path(run)
    p = p / "trace.jsonl" if p.is_dir() else p
    return [json.loads(line) for line in p.open(encoding="utf-8")]


def fit_axes(rows):
    """Tiles per pixel at the searching zoom, from drags followed by a camera reading."""
    X, Y, acc, last, zoom = [], [], np.zeros(2), None, "sweep"
    for r in rows:
        k = r["kind"]
        if k == "zoom":
            zoom, last = r.get("to"), None
            acc[:] = 0
            continue
        if k in ("goto", "notch"):
            last = None
            acc[:] = 0
            continue
        if k == "action" and r.get("what") == "drag" and zoom == "sweep":
            a = [float(v) for v in r["args"][:4]]
            acc += (a[2] - a[0], a[3] - a[1])
        if k == "look" and r.get("camera") and zoom == "sweep":
            cam = np.array(r["camera"][1:3], float)
            if last is not None and np.any(acc != 0):
                X.append(-acc.copy())
                Y.append(cam - last)
            last = cam
            acc[:] = 0
    if len(X) < 4:
        return None, len(X), None
    X, Y = np.array(X), np.array(Y)
    A = np.linalg.lstsq(X, Y, rcond=None)[0]
    return A.T, len(X), float(np.median(np.abs(X @ A - Y)))


def main(argv=None):
    import sys
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("run")
    ap.add_argument("--setup", default="setup.yaml")
    ap.add_argument("--tracks", action="store_true", help="print every army's full track")
    a = ap.parse_args(argv)
    rows = load(a.run)
    kinds = collections.Counter(r["kind"] for r in rows)
    span = rows[-1]["t"] - rows[0]["t"] if rows else 0
    print(f"{a.run}: {span:.0f} s, {len(rows)} events, {dict(kinds.most_common(8))}")

    # ---- rounds and per-army tracks
    rounds = [r for r in rows if r["kind"] == "round"]
    tracks = collections.defaultdict(list)
    for r in rounds:
        for x in r["armies"]:
            tracks[x["id"]].append((r["t"], x.get("tile"), x.get("kind"), x.get("owner"),
                                    bool(x.get("moving")), x.get("missed", 0)))
    print(f"\nROUNDS: {len(rounds)}  modes={dict(collections.Counter(r['mode'] for r in rounds))}")
    for r in rounds[:4] + (rounds[-2:] if len(rounds) > 6 else []):
        ow = collections.Counter(x.get("owner") for x in r["armies"])
        print(f"  R{r['n']:>3} {r['mode']:6s} t={r['t']:6.1f} n={len(r['armies'])} owners={dict(ow)} "
              f"moving={[x['id'] for x in r['armies'] if x.get('moving')]}")
    print("\nARMY TRACKS (flag = what would show as moved / changed when it hadn't):")
    flagged = 0
    for aid, tr in sorted(tracks.items()):
        pts = [t[1] for t in tr if t[1]]
        sp = max((math.dist(p, q) for p in pts for q in pts), default=0)
        kinds_ = {t[2] for t in tr}
        mov = [t[4] for t in tr]
        flips = sum(1 for i in range(1, len(mov)) if mov[i] != mov[i - 1])
        owners = {t[3] for t in tr}
        bad = []
        if "farm" in kinds_ and sp > 3:
            bad.append(f"farm drifts {sp:.0f} tiles")
        if len(kinds_) > 1:
            bad.append(f"kind changes {kinds_}")
        if flips >= 2:
            bad.append(f"moving flips {flips}x")
        if {"him", "other"} <= owners:
            bad.append("owner changed him<->other")
        flagged += bool(bad)
        if bad or a.tracks:
            print(f"  #{aid:<3} {'/'.join(sorted(kinds_)):10s} seen {len(tr):3d}x span {sp:4.1f} owners {owners} "
                  f"{'  <-- ' + '; '.join(bad) if bad else ''}")
    print(f"  {len(tracks)} ids, {flagged} flagged")

    # ---- owner checks
    owners = [r for r in rows if r["kind"] == "owner"]
    res = collections.Counter((r.get("item"), r.get("owner")) for r in owners)
    print(f"\nOWNER CHECKS: {len(owners)}  {dict(res)}")
    tiles = collections.Counter((r.get("item"), round(r["tile"][0]), round(r["tile"][1])) for r in owners)
    rep = {k: v for k, v in tiles.items() if v > 1}
    if rep:
        print(f"  checked more than once: {rep}")
    for r in owners:
        if r.get("owner") is None:
            print(f"  unclear: t={r['t']:.1f} {r.get('item')} {r.get('tile')} read={str(r.get('read'))[:120]}")
    cr = [r for r in rows if r["kind"] == "close_read"]
    for r in cr:
        print(f"  close_read t={r['t']:.1f} {r.get('item')} moving={r.get('moving')} clusters={r.get('clusters')}")

    # ---- farm coverage: every resource point with a badge seen vs what was checked
    looks = [r for r in rows if r["kind"] == "look" and r.get("camera")]
    spots = []
    axes = None
    try:
        import yaml
        axes = (yaml.safe_load(open(a.setup, encoding="utf-8")) or {}).get("patrol", {}).get("axes")
    except Exception:
        pass
    if axes:
        A = np.array(axes, float)
        for r in looks:
            cam = np.array(r["camera"][1:3], float)
            for x in r.get("found", []):
                if x["kind"] != "farm":
                    continue
                d = np.array(x["px"], float) - (926, 521)
                t = cam + A @ d
                if not any(math.dist(t, q) <= 2.5 for q in spots):
                    spots.append(tuple(t))
        checked = [tuple(r["tile"]) for r in owners if r.get("item") == "farm"]
        print(f"\nFARM SPOTS SEEN (approx, saved scale): {len(spots)}")
        for q in spots:
            near = [c for c in checked if math.dist(c, q) <= 3.5]
            box = [r for r in owners if r.get("item") == "farm" and math.dist(tuple(r["tile"]), q) <= 3.5]
            xys = sorted({str(re.search(r"X:?(\d+)\s*Y:?(\d+)", " ".join(map(str, b.get("read") or []))).groups())
                          for b in box if re.search(r"X:?(\d+)\s*Y:?(\d+)", " ".join(map(str, b.get("read") or [])))})
            print(f"  ~{q[0]:.0f},{q[1]:.0f}: checked {len(near)}x  boxes {xys}  owners {[b.get('owner') for b in box]}")

    # ---- warnings
    pat = re.compile(r"couldn't|didn't|not that one|vanished|tapped|off \(|unclear|problem|lost track", re.I)
    warns = [r for r in rows if r["kind"] == "log" and pat.search(str(r.get("msg", "")))]
    print(f"\nWARNINGS: {len(warns)}")
    for msg, n in collections.Counter(re.sub(r"\d+", "#", r["msg"]) for r in warns).most_common(12):
        print(f"  {n:3d}x {msg[:140]}")

    # ---- timing
    tl = [r["msg"] for r in rows if r["kind"] == "log" and str(r.get("msg", "")).startswith("Round (")]
    secs = [float(m) for m in re.findall(r"in ([\d.]+) s", " ".join(tl))]
    if secs:
        print(f"\nROUND TIME: n={len(secs)} median {np.median(secs):.1f}s max {max(secs):.1f}s")
    fc = collections.Counter(len(r.get("found", [])) for r in rows if r["kind"] == "look")
    kc = collections.Counter(x.get("kind") for r in rows if r["kind"] == "look" for x in r.get("found", []))
    print(f"LOOKS: found-count {dict(sorted(fc.items()))}  kinds {dict(kc)}")

    # ---- map scale
    A, n, err = fit_axes(rows)
    saved = None
    try:
        import yaml
        saved = (yaml.safe_load(open(a.setup, encoding="utf-8")) or {}).get("patrol", {}).get("axes")
    except Exception:
        pass
    if A is not None:
        print(f"\nMAP SCALE from {n} pans: x {A[0, 0]:.5f}  y {A[1, 1]:.5f} (median miss {err:.2f} tiles)")
        if saved:
            print(f"  saved axes: x {saved[0][0]:.5f}  y {saved[1][1]:.5f}  -> "
                  f"ratio x {A[0, 0] / saved[0][0]:.2f}  y {A[1, 1] / saved[1][1]:.2f} (1.00 = right)")


if __name__ == "__main__":
    main()
