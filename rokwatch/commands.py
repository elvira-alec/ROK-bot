"""Patrol mode and the one-off game commands (--goto, --recall-now, --learn-axes)."""

from __future__ import annotations

import logging
import time

from . import config
from .game import Game
from .notify import Notifier
from .patrol import Patrol, Site, learn_axes

log = logging.getLogger("rokwatch")


def game_area(gb, cfg):
    """The game picture on the page. Set game_area in config.yaml to override;
    otherwise it's found automatically (the page may have menus around it)."""
    if cfg.get("game_area"):
        return cfg["game_area"]
    finder = getattr(gb, "find_game_area", None)
    if not finder:
        return None
    try:
        area, clipped = finder()
    except Exception as e:
        log.warning("Couldn't look for the game picture: %s", e)
        return None
    if area is None:
        log.warning("Couldn't find the game picture on the page; using the whole window.")
        return None
    if clipped:
        log.warning("The game picture doesn't fit in the browser window. Make the window "
                    "bigger (or maximise it) so the bot can see the whole map.")
    return area


def make_game(gb, cfg) -> Game:
    p = cfg.get("patrol", {})
    return Game(gb, game_area(gb, cfg), float(p.get("settle_seconds", 1.5)),
                cfg.get("goto_dialog") or {})


def sites_from(cfg) -> list[Site]:
    sites = [Site.from_cfg(s) for s in cfg.get("patrol", {}).get("sites") or []]
    if not sites:
        raise SystemExit("Add your sites to patrol.sites in config.yaml first "
                         "(his city, your city, your gathering spots).")
    return sites


def wait_for_game(cfg, prompt):
    if prompt:
        input("\nIn the browser: log in, open Rise of Kingdoms and go to the map.\n"
              "Close any menus, then press Enter... ")
    else:
        wait = float(cfg.get("startup_wait_seconds", 60))
        log.info("Giving the game %.0fs to load...", wait)
        time.sleep(wait)


def patrol_loop(gb, cfg, notifier, max_rounds=None):
    p = cfg.get("patrol", {})
    game = make_game(gb, cfg)
    patrol = Patrol(game, sites_from(cfg), p, p.get("activity_log", "activity.csv"))
    if p.get("zoom_out", True) and not game.zoom_to_patrol_level():
        log.warning("Couldn't get to the right zoom (simplified map with resource points); "
                    "carrying on as it is.")
    names = ", ".join(s.name for s in patrol.sites)
    log.info("Patrolling: %s. Auto-recall is %s.", names, "ON" if patrol.auto_recall else "off")
    notifier.send("start", "RoK patrol started", f"Watching {names}.")
    rounds, failed_rounds = 0, 0
    while max_rounds is None or rounds < max_rounds:
        started = time.time()
        alerts = patrol.round()
        moved = [a for a in alerts if a.key == "goto"]
        failed_rounds = failed_rounds + 1 if len(moved) == len(patrol.sites) else 0
        for a in alerts:
            if a.key == "goto" and failed_rounds < 2:
                continue  # one bad round can be a hiccup; tell the user if it repeats
            notifier.send(a.key, a.title, a.message, a.urgent, a.image, a.cooldown)
        if failed_rounds >= 5:
            raise RuntimeError("can't move the map for 5 rounds")
        rounds += 1
        pause = float(p.get("pause_between_rounds", 5))
        log.info("Round %d done in %.0fs.", rounds, time.time() - started)
        time.sleep(pause)


def one_off(cfg, cfg_path, what, args, prompt=True):
    """Try a single action, to check it works on your screen."""
    from .browser import GameBrowser
    with GameBrowser.from_config(cfg) as gb:
        wait_for_game(cfg, prompt)
        game = make_game(gb, cfg)
        if what == "goto":
            ok = game.goto(*args)
            print("Jumped there." if ok else f"Couldn't jump. Camera is at {game.look().camera}.")
        elif what == "recall":
            for line in game.recall_all():
                print(line)
        elif what == "axes":
            m = learn_axes(game)
            if m is None:
                print("Couldn't work out the map directions (is the coordinate readout visible?)")
                return 1
            config.save_fields({"patrol.axes": m}, cfg_path)
            print(f"Saved map directions to setup.yaml: {m}")
        elif what == "look":
            v = game.look()
            print(f"camera={v.camera} zoom={game.zoom(v)}")
            print("troops:", game.troops(v))
            from .screen import find_march_lines
            for ln in find_march_lines(v.frame):
                print("march line:", ln)
            print("text:", [t.text for t in v.texts])
        input("Done. Press Enter to close... ") if prompt else None
    return 0


def run_patrol(cfg, prompt=True, max_rounds=None):
    from .browser import GameBrowser
    notifier = Notifier.from_config(cfg.get("notify", {}))
    restarts = 0
    while True:
        try:
            with GameBrowser.from_config(cfg) as gb:
                wait_for_game(cfg, prompt)
                patrol_loop(gb, cfg, notifier, max_rounds)
                return
        except (KeyboardInterrupt, SystemExit):
            raise
        except Exception as ex:
            restarts += 1
            log.error("Patrol crashed (%s). Restarting in 20s...", ex)
            notifier.send("crash", "RoK patrol restarting", f"Problem: {ex}", urgent=True)
            if restarts > 20:
                raise
            time.sleep(20)
            prompt = False
