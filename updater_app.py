"""RoKWatcherUpdater.exe: installs a new version of RoK Watcher.

RoKWatcher starts a copy of this program (from the temp folder, so it can replace
the files in the RoKWatcher folder, this one included) and then closes itself.
This window then:
  1. waits for RoKWatcher to close,
  2. downloads the new version (progress bar),
  3. copies the new program files over the old ones (your settings, logs and
     snapshots aren't in the download, so they stay as they are),
  4. starts RoKWatcher again.
If anything goes wrong it says what, and starts the old version again.
Every step is written to %TEMP%\\rokwatch-update.log.

    RoKWatcherUpdater.exe --pid 1234 --url https://.../RoKWatcher-windows.zip
                          --appdir "C:\\...\\RoKWatcher" --version 1.3
"""

import argparse
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.request
import zipfile
from pathlib import Path

LOG = Path(tempfile.gettempdir()) / "rokwatch-update.log"


def log(msg):
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(time.strftime("%H:%M:%S ") + msg + "\n")
    except OSError:
        pass


def wait_for_exit(pid, timeout=40):
    """Wait until the process is gone (Windows: wait on its handle)."""
    if sys.platform == "win32":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x00100000 | 0x0001, False, int(pid))     # SYNCHRONIZE | TERMINATE
        if not h:
            return True                                          # already gone
        try:
            if k.WaitForSingleObject(h, int(timeout * 1000)) == 0:
                return True
            k.TerminateProcess(h, 1)                             # didn't close: end it
            k.WaitForSingleObject(h, 5000)
            return True
        finally:
            k.CloseHandle(h)
    end = time.time() + timeout
    while time.time() < end:
        try:
            os.kill(int(pid), 0)
        except OSError:
            return True
        time.sleep(0.2)
    return False


def download(url, dest, progress):
    req = urllib.request.Request(url, headers={"User-Agent": "RoKWatcher-updater"})
    with urllib.request.urlopen(req, timeout=60) as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        got = 0
        while True:
            chunk = r.read(1 << 16)
            if not chunk:
                break
            f.write(chunk)
            got += len(chunk)
            progress(got, total)
    return got


def copy_tree(src: Path, dst: Path, progress):
    files = [p for p in src.rglob("*") if p.is_file()]
    for i, p in enumerate(files):
        target = dst / p.relative_to(src)
        target.parent.mkdir(parents=True, exist_ok=True)
        for attempt in range(20):                # a file can stay locked for a moment
            try:
                shutil.copy2(p, target)
                break
            except PermissionError:
                time.sleep(0.5)
        else:
            raise RuntimeError(f"Couldn't replace {target.name}: it's still in use. "
                               "Close RoK Watcher (and its browser windows) and try again.")
        if i % 25 == 0:
            progress(i + 1, len(files))
    progress(len(files), len(files))


def start_app(appdir: Path):
    exe = appdir / "RoKWatcher.exe"
    flags = 0x00000008 | 0x00000200 if sys.platform == "win32" else 0   # detached, own group
    subprocess.Popen([str(exe), "--updated"], cwd=str(appdir), creationflags=flags, close_fds=True)
    log(f"started {exe}")


def run(args, ui):
    appdir = Path(args.appdir)
    work = Path(tempfile.mkdtemp(prefix="rokwatch-update-"))
    try:
        ui("Closing RoK Watcher...", None)
        wait_for_exit(args.pid)
        log("old version closed")
        time.sleep(0.5)

        zip_path = work / "update.zip"

        def dl(got, total):
            mb = got / 1e6
            ui(f"Downloading version {args.version}... {mb:.0f} MB" + (f" of {total / 1e6:.0f} MB" if total else ""),
               got / total if total else None)
        size = download(args.url, zip_path, dl)
        log(f"downloaded {size} bytes")

        ui("Unpacking...", None)
        out = work / "unzipped"
        with zipfile.ZipFile(zip_path) as z:
            z.extractall(out)
        src = out / "RoKWatcher" if (out / "RoKWatcher" / "RoKWatcher.exe").exists() else out
        if not (src / "RoKWatcher.exe").exists():
            raise RuntimeError("The download doesn't contain RoKWatcher.exe")

        copy_tree(src, appdir, lambda i, n: ui(f"Installing... {i} of {n} files", i / max(n, 1)))
        log("files replaced")
        ui(f"Starting RoK Watcher {args.version}...", 1.0)
        start_app(appdir)
        time.sleep(1.5)
        ui(None, "done")
    except Exception as e:
        log("FAILED: " + "".join(traceback.format_exception(e)))
        ui(None, ("error", str(e)))
        try:
            start_app(appdir)                        # the old version again
        except Exception as e2:
            log(f"couldn't restart: {e2}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pid", type=int, required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--appdir", required=True)
    ap.add_argument("--version", default="")
    ap.add_argument("--no-window", action="store_true", help="(tests) no window")
    args = ap.parse_args()
    log(f"updater started: {args}")

    if args.no_window:
        run(args, lambda text, frac: log(f"ui: {text} {frac}") if text else None)
        return

    import tkinter as tk
    from tkinter import messagebox, ttk
    root = tk.Tk()
    root.title("RoK Watcher update")
    root.geometry("460x170")
    root.resizable(False, False)
    root.attributes("-topmost", True)
    root.configure(bg="#0f1420")
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure("bar.Horizontal.TProgressbar", troughcolor="#1e2739", background="#3aa0ff",
                    bordercolor="#1e2739", lightcolor="#3aa0ff", darkcolor="#3aa0ff")
    tk.Label(root, text=f"Updating RoK Watcher to version {args.version}", bg="#0f1420", fg="#e8ecf4",
             font=("Segoe UI", 12, "bold")).pack(anchor="w", padx=20, pady=(18, 4))
    status = tk.Label(root, text="Starting...", bg="#0f1420", fg="#95a1b8", font=("Segoe UI", 10))
    status.pack(anchor="w", padx=20)
    bar = ttk.Progressbar(root, style="bar.Horizontal.TProgressbar", length=420, mode="indeterminate")
    bar.pack(padx=20, pady=12)
    bar.start(12)
    tk.Label(root, text="Your settings, logs and snapshots are kept. RoK Watcher starts again by itself.",
             bg="#0f1420", fg="#6c7891", font=("Segoe UI", 8)).pack(anchor="w", padx=20)

    q: queue.Queue = queue.Queue()

    def ui(text, frac):
        q.put((text, frac))

    def poll():
        try:
            while True:
                text, frac = q.get_nowait()
                if frac == "done":
                    root.destroy()
                    return
                if isinstance(frac, tuple):
                    messagebox.showerror("RoK Watcher update",
                                         f"The update didn't work:\n\n{frac[1]}\n\nRoK Watcher starts again as "
                                         f"it was. You can also download the new version from GitHub.\n\n"
                                         f"Details: {LOG}")
                    root.destroy()
                    return
                if text:
                    status.config(text=text)
                if frac is None:
                    if str(bar.cget("mode")) != "indeterminate":
                        bar.config(mode="indeterminate")
                        bar.start(12)
                else:
                    if str(bar.cget("mode")) != "determinate":
                        bar.stop()
                        bar.config(mode="determinate", maximum=1000)
                    bar["value"] = int(frac * 1000)
        except queue.Empty:
            pass
        root.after(100, poll)

    threading.Thread(target=run, args=(args, ui), daemon=True).start()
    root.after(100, poll)
    root.mainloop()


if __name__ == "__main__":
    main()
