# Handoff: RoK Watcher

Read this first in a new session (for example one running on the user's own PC, next to
the game). It says what the bot is, where things stand, and how to work on it.

## What it is

A watcher/bot for Rise of Kingdoms (PC game, window mode). It finds one enemy player's armies
around his city and follows them. It alerts the user's phone (ntfy) when an army heads for
their city or troops, when they're scouted (red eye) or attacked (red swords), and when he
pulls farmers back. It can recall the user's troops. It's controlled from a web panel
(`http://127.0.0.1:8765`, optionally from the phone over Wi-Fi or Tailscale).

- The user: account `[JONS]IronAR`, city about X:210 Y:422, kingdom 4183.
- The enemy: `[P83L]JONH WICK`, city X:162 Y:374.
- Game language: English now (Spanish is supported too).
- The user wants fast, rock-solid behaviour, real-screenshot testing, and no emojis in the UI
  (line icons only). They publish only when they say "publish". Pushes only build and test.

## Layout

| Path | What |
|---|---|
| `rokwatch_app.py` | exe entry. Runs in the background (no console), single instance, `--updated`, `--test-update`. |
| `updater_app.py` | RoKWatcherUpdater.exe: separate program, Tk progress window. Waits for the bot to close, downloads, replaces files, restarts it. Log in `%TEMP%\rokwatch-update.log`. |
| `rokwatch/game.py` | Game control: `look`/`fast_view`, `goto` (magnifier found by shape, search bar by its blue button), `pan_to` (drag), the two-zoom system (`to_sweep`/`to_close`, `calibrate_close` by measured map scale), `close_popups` (drag away from the info box; Esc only when the map is covered), `Stopped`. |
| `rokwatch/screen.py` | All detection: coordinates (top strip OCR, padded), march lines, army/scout diamonds (plain diamond under 46 px per 1320 px of height = scout), red pickaxe gather badges, info box, red eye / swords, view level. |
| `rokwatch/hunter.py` | "Watch & track him": sweep, then track, or watch his city when nothing's out. `Tracker`, owner checks (farm: tap the camp and read his name in the box; marching: name label, or a march line to his city = his), threats, events, per-army focus/alerts, trail/ETA. |
| `rokwatch/activity.py` | `his_activity.csv` / `his_watch.csv`, and the data for the panel's heatmap. |
| `rokwatch/worker.py` | The single worker thread behind the panel. |
| `rokwatch/panel.py`, `panel.html` | Web panel, phone access, updates, snapshots, `/api/*`. |
| `rokwatch/updater.py` | Update check (github.com/.../releases/latest redirect; API and PowerShell as fallbacks), launches the updater. |
| `.github/workflows/windows-exe.yml` | Windows build. On push: build and test only. Publishing: Run workflow with `publish=true` and optionally `version=1.x` (replaces that release). |
| `tests/` | `real_screens/` real screenshots; `gather/` more real ones and the user's snapshots; `fake_game/rok.html` simulated game. Slow suites: `test_hunter.py`, `test_patrol.py` (~8 min each). |

## Recently fixed (worth checking in the real game)

- False "He pulled a farmer back": coverage is now the exact on-screen area, a farm must be
  missing on two looks, and its matching position stays the sweep's estimate (the exact
  X/Y from its box is display only).
- His armies going home drawn as a big plain diamond: army-sized plain diamonds count as armies.
- Faster: an army on a line from or to his city is his without zooming in, and zoom changes
  check the view only around the switch point.

## Open / unverified (needs the real game)

- How fast a round is in practice, and where the time goes (the panel log has a line per round).
- Whether his farms are now found at the user's searching zoom (the red pickaxe badge size
  range was widened; tuned on only one sweep screenshot).
- Scout sending, recall in the real game, and the share-to-alliance idea (needs screenshots).

## How to work on it on the PC

1. `pip install -r requirements.txt`, then `python -m rokwatch.panel` runs the panel from source.
2. With the game open: the panel's Save snapshot gives exactly what the bot sees, plus a
   `.json` of what it read. Turn those into tests in `tests/real_screens/gather/`.
3. `python -m pytest tests/test_gatherers.py tests/test_real_screens.py -q` (fast).
4. Commit and push; publish only when the user says so.
