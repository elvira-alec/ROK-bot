"""RoK watcher.

    python -m rokwatch                  watch the game (first run does setup)
    python -m rokwatch --calibrate      redo setup
    python -m rokwatch --patrol         visit his city + your spots in turn (full bot)
    python -m rokwatch --look / --goto X Y / --recall-now / --learn-axes
                                        try one action on your screen
    python -m rokwatch --report         when is he active? (from activity.csv)
    python -m rokwatch --test-notify    send a test alert to your phone
    python -m rokwatch --record         also save every screenshot to recordings/
    python -m rokwatch --check recordings      (or a.png b.png ...)
                                        run the checks on saved screenshots
                                        (in order, like a recording)
"""

from __future__ import annotations

import argparse
import glob
import logging
import sys
import time
from pathlib import Path

import cv2

from . import config
from .notify import Notifier
from .watcher import Watcher, protect_points_from

log = logging.getLogger("rokwatch")


def setup_logging(path="rokwatch.log"):
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", "%H:%M:%S")
    log.setLevel(logging.INFO)
    if not log.handlers:
        for h in (logging.StreamHandler(sys.stdout), logging.FileHandler(path, encoding="utf-8")):
            h.setFormatter(fmt)
            log.addHandler(h)


def needs_calibration(cfg) -> bool:
    e = cfg.get("enemy_watch", {})
    if not e.get("enabled", True):
        return False
    return list(e.get("enemy_city", [0, 0])) == [0, 0] or not protect_points_from(e)


def load_reference(cfg_path):
    ref = config.reference_path(cfg_path)
    return cv2.imread(ref) if Path(ref).exists() else None


def deliver(notifier: Notifier, alerts):
    for a in alerts:
        notifier.send(a.key, a.title, a.message, a.urgent, a.image, a.cooldown)


def watch_loop(gb, cfg, cfg_path, notifier, max_checks=None, record_dir=None):
    watcher = Watcher(cfg, load_reference(cfg_path))
    if record_dir:
        Path(record_dir).mkdir(parents=True, exist_ok=True)
        log.info("Recording every screenshot to %s", record_dir)
    area = cfg.get("game_area")
    every = float(cfg.get("check_every_seconds", 3))
    fails = 0
    checks = 0
    log.info("Watching. Leave the game on the map. Ctrl+C to stop.")
    notifier.send("start", "RoK watcher started", "Watching for attacks and enemy marches.")
    while max_checks is None or checks < max_checks:
        started = time.time()
        try:
            frame = gb.screenshot(area)
            fails = 0
        except Exception as ex:
            fails += 1
            log.error("screenshot failed: %s", ex)
            if fails == 3:
                notifier.send("error", "RoK watcher problem", f"Can't screenshot the game: {ex}",
                              urgent=True)
            if not gb.alive:
                raise RuntimeError("browser closed") from ex
            time.sleep(5)
            continue
        if record_dir:
            cv2.imwrite(str(Path(record_dir) / f"{checks:06d}.png"), frame)
        deliver(notifier, watcher.step(frame))
        checks += 1
        time.sleep(max(0.0, every - (time.time() - started)))


def run(cfg, cfg_path, force_calibrate=False, prompt=True, max_checks=None, record_dir=None):
    from .browser import GameBrowser  # needs Playwright; not needed for --check
    notifier = Notifier.from_config(cfg.get("notify", {}))
    if not notifier.has_target:
        log.warning("No ntfy topic / Discord / Telegram set in config: alerts only print here.")
    restarts = 0
    while True:
        try:
            with GameBrowser.from_config(cfg) as gb:
                if force_calibrate or needs_calibration(cfg):
                    from .calibrate import calibrate
                    cfg = calibrate(gb, cfg_path)
                    force_calibrate = False
                elif prompt:
                    input("\nIn the browser: open the game and put the map about where you set it\n"
                          "up (see calibration.png). Press Enter to start watching... ")
                else:
                    wait = float(cfg.get("startup_wait_seconds", 60))
                    log.info("Giving the game %.0fs to load...", wait)
                    time.sleep(wait)
                watch_loop(gb, cfg, cfg_path, notifier, max_checks, record_dir)
                return
        except KeyboardInterrupt:
            raise
        except SystemExit:
            raise
        except Exception as ex:
            restarts += 1
            log.error("Watcher crashed (%s). Restarting in 15s...", ex)
            notifier.send("crash", "RoK watcher restarting",
                          f"Browser problem: {ex}. Restarting. Check the map is set up again.",
                          urgent=True)
            if restarts > 20:
                raise
            time.sleep(15)
            prompt = False  # nobody may be at the PC: come back on our own


def expand_paths(paths):
    """Accept files, folders and wildcards (Windows doesn't expand *.png itself)."""
    out = []
    for p in paths:
        if Path(p).is_dir():
            out += sorted(str(f) for f in Path(p).glob("*.png"))
        elif any(c in p for c in "*?["):
            out += sorted(glob.glob(p))
        else:
            out.append(p)
    return out


def check_images(cfg, cfg_path, paths):
    """Replay screenshots through the checks and print what would alert."""
    watcher = Watcher(cfg, load_reference(cfg_path), debug_dir="debug/check")
    area = cfg.get("game_area")
    t = 0.0
    for p in expand_paths(paths):
        frame = cv2.imread(p)
        if frame is None:
            print(f"{p}: can't read")
            continue
        if area and frame.shape[1] >= area[0] + area[2] and frame.shape[0] >= area[1] + area[3]:
            x, y, w, h = area
            frame = frame[y:y + h, x:x + w]
        alerts = watcher.step(frame, now=t)
        t += float(cfg.get("check_every_seconds", 3))
        status = "; ".join(f"{a.title} ({a.message})" for a in alerts) or "nothing"
        extra = " [learning background]" if watcher.march and watcher.march.learning else ""
        if watcher.last_confidence is not None:
            extra += f" [map line-up {watcher.last_confidence:.2f}]"
        print(f"{p}: {status}{extra}")
    print("Marked-up screenshots for any alerts are in debug/check/")


def selftest(path):
    """Read one screenshot with everything the bot uses and print it as JSON."""
    import json
    from . import screen
    frame = cv2.imread(path)
    if frame is None:
        print(json.dumps({"error": f"can't read {path}"}))
        return 1
    texts = screen.read_text(frame)
    cam = screen.camera_coords(frame, texts)
    panel = screen.troop_panel(frame, texts)
    print(json.dumps({
        "camera": list(cam[:3]) if cam else None,
        "zoom": screen.zoom_level(frame, texts),
        "troops": [s.state for s in panel[2]] if panel else None,
        "lines": [ln.color for ln in screen.find_march_lines(frame)],
        "recall_button": bool(screen.find_text(texts, r"^RECALL$")),
    }))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description="Rise of Kingdoms attack watcher")
    ap.add_argument("--config", default=config.DEFAULT_PATH)
    ap.add_argument("--test-notify", action="store_true", help="send one test alert and exit")
    ap.add_argument("--calibrate", action="store_true", help="redo the screen setup")
    ap.add_argument("--no-prompt", action="store_true",
                    help="don't wait for Enter (for auto-start); waits startup_wait_seconds")
    ap.add_argument("--check", nargs="+", metavar="PNG", help="run checks on screenshots")
    ap.add_argument("--record", nargs="?", const="recordings", metavar="FOLDER",
                    help="save every screenshot (to replay later with --check)")
    ap.add_argument("--patrol", action="store_true",
                    help="visit each site in patrol.sites in turn (the full bot)")
    ap.add_argument("--goto", nargs=2, type=int, metavar=("X", "Y"),
                    help="test: jump the map to X Y")
    ap.add_argument("--recall-now", action="store_true", help="test: recall all gathering troops")
    ap.add_argument("--learn-axes", action="store_true",
                    help="work out the map directions (lets patrol tell where marches go)")
    ap.add_argument("--look", action="store_true", help="test: print what the bot sees now")
    ap.add_argument("--report", action="store_true", help="when is he active? (from the log)")
    ap.add_argument("--panel", action="store_true", help="open the control panel")
    ap.add_argument("--selftest", metavar="PNG", help=argparse.SUPPRESS)  # checks the build
    ap.add_argument("--max-checks", type=int, help=argparse.SUPPRESS)  # for tests
    args = ap.parse_args(argv)
    setup_logging()
    if args.selftest:
        return selftest(args.selftest)
    if args.panel:
        from .panel import serve
        serve(args.config)
        return 0
    cfg = config.load(args.config)
    try:
        if args.test_notify:
            n = Notifier.from_config(cfg.get("notify", {}), background=False)
            if not n.has_target:
                print("Set notify.ntfy_topic (or Discord/Telegram) in config.yaml first.")
                return 1
            n.send("test", "RoK watcher test", "If you see this, alerts work.")
        elif args.check:
            check_images(cfg, args.config, args.check)
        elif args.report:
            from .patrol import activity_report
            print(activity_report(cfg.get("patrol", {}).get("activity_log", "activity.csv")))
        elif args.goto or args.recall_now or args.learn_axes or args.look:
            from .commands import one_off
            what = ("goto" if args.goto else "recall" if args.recall_now else
                    "axes" if args.learn_axes else "look")
            return one_off(cfg, args.config, what, args.goto, not args.no_prompt)
        elif args.patrol or cfg.get("patrol", {}).get("enabled"):
            from .commands import run_patrol
            run_patrol(cfg, not args.no_prompt, args.max_checks)
        else:
            run(cfg, args.config, args.calibrate, not args.no_prompt, args.max_checks,
                args.record)
    except KeyboardInterrupt:
        log.info("Stopped.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
