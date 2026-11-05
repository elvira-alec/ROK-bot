"""Main watcher loop:  python -m rokwatch"""

from __future__ import annotations

import argparse
import time
from datetime import datetime
from pathlib import Path

import cv2

from . import config
from .detectors import AttackAlertDetector, EnemyMarchDetector, FrozenDetector
from .notify import Notifier


def save_debug(frame, tag: str, marches=(), enemy=None, home=None) -> str:
    Path("debug").mkdir(exist_ok=True)
    img = frame.copy()
    if enemy is not None:
        cv2.circle(img, tuple(map(int, enemy)), 8, (0, 0, 255), 2)
    if home is not None:
        cv2.circle(img, tuple(map(int, home)), 8, (0, 255, 0), 2)
    for m in marches:
        cv2.arrowedLine(img, m.start, m.end, (0, 255, 255), 2)
    path = f"debug/{datetime.now():%Y%m%d-%H%M%S}-{tag}.png"
    cv2.imwrite(path, img)
    return path


def build(cfg):
    n = cfg.get("notify", {})
    notifier = Notifier(n.get("ntfy_topic", ""), n.get("ntfy_server", "https://ntfy.sh"),
                        n.get("discord_webhook", ""), n.get("cooldown_seconds", 90))
    a = cfg.get("attack_alert", {})
    attack = AttackAlertDetector(a.get("edge_width", 0.04), a.get("red_fraction", 0.30),
                                 a.get("confirm_frames", 2), a.get("template_dir"),
                                 a.get("template_threshold", 0.82)) if a.get("enabled") else None
    e = cfg.get("enemy_watch", {})
    enemy = None
    if e.get("enabled"):
        enemy = EnemyMarchDetector(e["enemy_city"], e["home"], e["line_colors"],
                                   e.get("start_radius", 70),
                                   e.get("angle_tolerance_degrees", 35),
                                   e.get("min_line_length", 35))
    frozen = FrozenDetector(cfg.get("frozen_alert_minutes", 5))
    return notifier, attack, enemy, frozen


def needs_calibration(cfg) -> bool:
    e = cfg.get("enemy_watch", {})
    return bool(e.get("enabled")) and list(e.get("enemy_city", [0, 0])) == [0, 0]


def run(cfg, cfg_path, test_notify=False, force_calibrate=False):
    if test_notify:
        build(cfg)[0].send("test", "RoK watcher test", "If you see this, alerts work.")
        return
    # Imported here so the image tests don't need Playwright or a screen.
    from .browser import GameBrowser
    from .calibrate import calibrate

    with GameBrowser(cfg["game_url"], cfg["browser_profile"], cfg.get("viewport", (1600, 900))) as gb:
        if force_calibrate or needs_calibration(cfg):
            cfg = calibrate(gb, cfg_path)
        else:
            input("\nIn the browser: open the game and put the map where you calibrated it\n"
                  "(see calibration.png). Press Enter to start watching... ")
        notifier, attack, enemy, frozen = build(cfg)
        e = cfg.get("enemy_watch", {})
        save_shots = cfg.get("save_alert_screenshots", True)
        print("Watching. Leave the game on the map view. Ctrl+C to stop.")
        notifier.send("start", "RoK watcher started", "Watching for attacks and enemy marches.")
        while True:
            try:
                frame = gb.screenshot(cfg.get("game_area"))
            except Exception as ex:
                notifier.send("error", "RoK watcher problem", f"Couldn't screenshot game: {ex}")
                time.sleep(10)
                continue

            if attack and (reason := attack.check(frame)):
                img = save_debug(frame, "attack") if save_shots else None
                notifier.send("attack", "YOU ARE UNDER ATTACK",
                              f"{reason}. Pull your troops back!", urgent=True, image_path=img)

            if enemy:
                marches = enemy.check(frame)
                toward = [m for m in marches if m.toward_home]
                if toward:
                    m = toward[0]
                    img = save_debug(frame, "enemy-toward", toward, e["enemy_city"], e["home"]) \
                        if save_shots else None
                    notifier.send("enemy-toward", "Enemy march heading your way",
                                  f"A {m.color} march left his city pointing at you "
                                  f"({m.angle_off:.0f} deg off).", urgent=True, image_path=img)
                elif marches and e.get("report_other_marches", True):
                    img = save_debug(frame, "enemy-other", marches, e["enemy_city"], e["home"]) \
                        if save_shots else None
                    notifier.send("enemy-other", "Enemy sent a march",
                                  "He sent troops out, not pointed at you.", image_path=img)

            if frozen.check(frame, time.time()):
                notifier.send("frozen", "RoK watcher can't see the game",
                              "The game picture hasn't changed for a while. "
                              "Disconnected or logged out?", urgent=True)

            time.sleep(cfg.get("check_every_seconds", 3))


def main():
    ap = argparse.ArgumentParser(description="Rise of Kingdoms attack watcher")
    ap.add_argument("--config", default=config.DEFAULT_PATH)
    ap.add_argument("--test-notify", action="store_true", help="send one test alert and exit")
    ap.add_argument("--calibrate", action="store_true", help="redo the screen position setup")
    args = ap.parse_args()
    try:
        run(config.load(args.config), args.config, args.test_notify, args.calibrate)
    except KeyboardInterrupt:
        print("Stopped.")


if __name__ == "__main__":
    main()
