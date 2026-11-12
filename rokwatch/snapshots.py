"""Pictures of the game the bot saves so you can send them in to improve it.

Saved to the 'snapshots' folder next to the program:
  - when you press 'Save snapshot' in the panel (exactly what the bot sees),
  - with every alert (the picture the alert was based on),
  - every few minutes while patrolling / hunting (setting: snapshots.every_minutes).
Each picture gets a .json next to it with what the bot read from it, so a wrong
reading can be matched to the picture. Old ones are deleted (keeps the newest
snapshots.keep, default 300).
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from datetime import datetime
from pathlib import Path

import cv2

SNAP_DIR = Path("snapshots")
_NAME = re.compile(r"^[\w.-]+\.(png|jpg|json)$")


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(s).lower()).strip("-")[:40] or "snap"


def describe(view) -> dict:
    """What the bot read from the picture (kept next to it)."""
    from . import screen
    info = {"camera": list(view.camera) if view.camera else None,
            "size": [int(view.frame.shape[1]), int(view.frame.shape[0])]}
    try:
        info["zoom"] = screen.zoom_level(view.frame, view.texts)
        panel = screen.troop_panel(view.frame, view.texts)
        info["troops"] = [s.state for s in panel[2]] if panel else None
        info["march_lines"] = [f"{ln.color} {ln.p1}->{ln.p2}" for ln in screen.find_march_lines(view.frame)]
        info["map_icons"] = [f"{k} at {tuple(int(v) for v in c)}"
                             for c, k in screen.find_map_icons(view.frame)][:40]
    except Exception as e:                 # a reading that fails is worth knowing about too
        info["error"] = f"{type(e).__name__}: {e}"
    info["texts"] = [t.text for t in view.texts][:60]
    return info


def save(frame, tag: str, info: dict | None = None, folder: Path | str = SNAP_DIR,
         keep: int = 300, png: bool = False) -> str:
    """Write the picture (+ .json); returns its file name."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(tag)}"
    n = 1
    while (folder / f"{stem}.json").exists():
        n += 1
        stem = f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(tag)}-{n}"
    name = stem + (".png" if png else ".jpg")
    params = [] if png else [cv2.IMWRITE_JPEG_QUALITY, 95]
    cv2.imwrite(str(folder / name), frame, params)
    meta = {"tag": tag, "time": time.strftime("%Y-%m-%d %H:%M:%S"), "picture": name, **(info or {})}
    (folder / f"{stem}.json").write_text(json.dumps(meta, indent=1, default=str))
    prune(folder, keep)
    return name


def prune(folder: Path | str = SNAP_DIR, keep: int = 300):
    pics = sorted(p for p in Path(folder).glob("*") if p.suffix in (".png", ".jpg"))
    for p in pics[:max(0, len(pics) - keep)]:
        p.unlink(missing_ok=True)
        p.with_suffix(".json").unlink(missing_ok=True)


def listing(folder: Path | str = SNAP_DIR, limit: int = 60) -> list[dict]:
    """Newest first: [{name, tag, time}]"""
    out = []
    pics = sorted((p for p in Path(folder).glob("*") if p.suffix in (".png", ".jpg")), reverse=True)
    for p in pics[:limit]:
        meta = {}
        try:
            meta = json.loads(p.with_suffix(".json").read_text())
        except (OSError, ValueError):
            pass
        out.append({"name": p.name, "tag": meta.get("tag", ""), "time": meta.get("time", "")})
    return out


def path_of(name: str, folder: Path | str = SNAP_DIR) -> Path | None:
    """The file for a name from listing(), or None (no paths from outside the folder)."""
    if not _NAME.match(name or ""):
        return None
    p = Path(folder) / name
    return p if p.is_file() else None


def zip_bytes(folder: Path | str = SNAP_DIR, newest: int = 40) -> bytes:
    """The newest pictures and their notes in one .zip (to send in)."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for item in listing(folder, newest):
            p = Path(folder) / item["name"]
            z.write(p, p.name)
            if p.with_suffix(".json").exists():
                z.write(p.with_suffix(".json"), p.with_suffix(".json").name)
    return buf.getvalue()
