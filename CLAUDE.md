# RoK Watcher: full context for a new session

Claude Code reads this file automatically. **Also read `docs/HANDOFF.md`**: the long version, with PC
setup, a real-game test plan, the debug recording, internals, the full problem history and open
issues. This file is everything the previous (cloud) session
knew: the goal, how the game looks, how the bot works, what went wrong before and how it
was fixed, how to test, and how to build and publish. Read it all before changing anything.

## 1. Goal and the user

- **What it does:** a watcher/bot for Rise of Kingdoms, the **official PC game** in window
  mode (Windows laptop). It runs in the background and is controlled from a web panel at
  `http://127.0.0.1:8765`, also usable from the user's phone over Wi-Fi or Tailscale.
- **The user:** `[JONS]IronAR`, city about **X:210 Y:422**, kingdom **4183**.
- **The target:** `[P83L]JONH WICK`, city **X:162 Y:374**. He bullies the user's farmers.
- **The job:** find all his armies around his city (farming and marching), confirm each is
  his, then follow them as fast as possible and keep checking his city. Alert the user's
  phone (ntfy) when one heads for (or moves close to) the user's city or troops, when he
  pulls farmers back, and when the user is scouted or attacked. Optionally auto-recall the
  user's troops. Log his activity to work out when he's online.
- **The user's preferences:**
  - Fast and rock-solid, no corner-cutting.
  - Test against **real screenshots**; never confuse scouts with armies.
  - No emojis in the UI; line icons only.
  - Short, clear answers.
  - **Always publish a new version after changes, unless they say not to.**
- **Game language:** English (Spanish is supported too; they ran it in Spanish before).
- **Game settings:** a game setting that shows the troop symbol on army diamonds is now on.

## 2. How the game looks (what the bot must recognise)

**Zoom levels**
- **3D close view:** buildings, names over armies. The top-left shows power ("2,032,880")
  and VIP. Resource points can be tapped here.
- **Simplified map:** the "searching zoom". Armies are diamonds, resource points are icons,
  and the power/VIP block is hidden.
- **Too far:** a globe button appears at the bottom right and the icons are gone.

**The coordinate readout**
- `#4183 X:… Y:…` at the top left, with a **magnifier** to its right and then an **orange
  star** (favourites).
- Tapping the magnifier opens the search bar (`#: 4183 X:[ ] Y:[ ]` and a blue search button).
- **Never tap the star.**

**Armies on the simplified map**
- Grey-lavender diamonds by day, mauve at night.
- Diamond **with a white troop symbol** = army. **Plain** diamond = scout.
- Scouts are 33–39 px wide and armies 52–58 px, on a 1320 px tall screen. A plain diamond of
  army size is counted as an army.
- A **red** diamond is a hostile army marching at the user.

**March lines** (dotted): white = others, green = the user or their alliance, red = hostile.

**Farms (gathering)**
- Show as a resource point with a round **red pickaxe badge** (not the user's alliance).
  Green = the user or their alliance.
- His name is **only** shown in the resource point's **info box**: "Gatherer
  [P83L]JONH WICK", or "Recolector" in Spanish. The box also shows the exact X:Y, the
  resource and level, and reserves.
- The box can only be opened at the close zoom.
- **Esc doesn't close this box on PC, and Esc on the plain map opens the profile.** Close it
  with a short map drag on the side away from the box.

**Resource points (full amounts, from the user)**

| Level | Stone | Wood | Food | Gold |
|---|---|---|---|---|
| 2 | 236,250 | 315,000 | 315,000 | 105,000 |
| 3 | 354,375 | 472,500 | 472,500 | 157,500 |
| 4 | 472,500 | 630,000 | 630,000 | 210,000 |
| 5 | 708,750 | 945,000 | 945,000 | 315,000 |
| 6 | 945,000 | 1,260,000 | 1,260,000 | 420,000 |

- Gems: 10/20/30/40 at levels 1–4. All of this is in `rokwatch/farms.py` (`FULL`).
- Full minus reserves = what he's taken. Reading it again later gives his rate, so: how long
  he's been there, when the point runs out, and (with his learned load) when his army heads home.
- Gathering, and armies walking home by themselves, do **not** count as him being online.

**The two zooms (the user's definition, self-calibrated: "Set up the zoom automatically")**
- **Close zoom** = the last 3D step, just before the view switches to the simplified map.
- **Searching zoom** = the furthest-out step where resource points / barbarians still show
  their **level number tags**. One step further out the numbers vanish and only bare icons
  stay (`tests/real_screens/zoom/`: 10 tags vs 1 tag).
- `Game.auto_calibrate()`: all the way in, step out, note the 3D end and the tags' end
  (`screen.count_level_tags`, ≥3 = shown, ≤1 = gone). It drags to find resource points if
  none are in view, checks by stepping back in, and only then saves `{gap, t_out: 1, t_in: gap}`.

**Danger**
- **Red eye** (bottom right): being scouted. **Red crossed swords** (also bottom right):
  being attacked.
- The screen edge also pulses red.
- **Only these alert for scouting.** No march-line scout alerts.

**The troop list** (right side, under "N/M") shows the user's commanders. Badge symbols tell
the state: gathering, marching, returning, fighting and so on. Scouts appear at the bottom.

**Real screenshots** are in `tests/real_screens/`, `tests/real_screens/crops/`,
`tests/real_screens/icons/` (red swords) and `tests/real_screens/gather/` (farms, info
boxes, the user's snapshots including the Spanish photos).

## 3. How the bot works (code map)

| File | What |
|---|---|
| `rokwatch_app.py` | exe entry point. Relaunches itself without a console window; single instance (reopening just opens the panel); `--updated`; `--test-update URL VER`. |
| `updater_app.py` | **RoKWatcherUpdater.exe**, a separate program with a Tk progress window. Started from a temp copy; waits for the bot's PID to exit, downloads the zip, replaces the files (keeps config/logs/snapshots), restarts the bot. Log: `%TEMP%\rokwatch-update.log`. |
| `rokwatch/desktop.py` | Game window control on Windows: find the window by title, PrintWindow capture (works when covered), real mouse via pyautogui (fail-safe: move the mouse into a corner), drag with a short hold before release (no fling). |
| `rokwatch/game.py` | `look()` reads all the text (slow). `read_camera(frame)` / `camera_fast()` read only the padded top strip. `goto(x, y)`: magnifier found by template, bar checked by its blue button, remembered box positions, fast typing. `pan_to()`: one drag from the known camera. Two-zoom system: `to_sweep()` / `to_close()` with `zoom_cfg = {gap, t_out, t_in}`, checked around the switch point, so a lost notch is corrected. `calibrate_close(search_scale)` measures the map scale by a small drag after each notch. `close_popups()`. `Stopped` exception (Stop works mid-action). |
| `rokwatch/screen.py` | All detection: `camera_coords`/`top_texts`, `view_level`, `find_march_lines` (ignores the left side panel and the chat), `find_map_icons` (army/scout/hostile), `find_gather_badges` (round red pickaxe, not diamonds), `find_info_box`, `read_gatherer`, `danger_icons` (eye vs swords by the white symbol), `search_button`/`find_magnifier`, `search_go_button`, troop panel, `name_score`, `find_player`. |
| `rokwatch/hunter.py` | "Watch & track him". The first round **sweeps** the area around his city. Then each round **tracks**: cycles his known armies plus his city, scouts excluded. **base** mode when none are out. A new sweep only if a marching army of his is lost away from home, or every 30 min. Each spot runs `look_spot()`: one picture, reading the coordinates and detecting in parallel. New armies are checked where they're found: a march line to or from his city = his, no zoom. Otherwise zoom in: marching = read the name label; farm = tap the camp and read the box. `Tracker`: matching (farms only at the same spot, ≤3 tiles), "gone" only if the spot was really on screen (`_on_screen`, exact projection), twice for things that stay put. Events, threats, per-army focus/alerts, trail/ETA, farm details. A per-round timing log line: moving / looking / checking owners. |
| `rokwatch/activity.py` | `his_activity.csv` (his actions) and `his_watch.csv` (minutes watched) → the panel's heatmap and insights. |
| `rokwatch/worker.py` | One worker thread runs the commands from the panel (open, look, goto, remember_zoom sweep/close, hunt, start (simple patrol), stop, snap…). |
| `rokwatch/panel.py`, `panel.html` | Web panel: 1 Connect, 2 Who to watch (saved targets), 3 Set up the zoom (two buttons), 4 Watch; His armies (list + map, per-army chips); Activity; Live view; More (phone, updates, snapshots, places, tools, log, Quit). Closes the program when the page is closed (option). |
| `rokwatch/updater.py` | Update check via the `github.com/elvira-alec/ROK-bot/releases/latest` redirect (no API limit); API and PowerShell as fallbacks. |
| `rokwatch/snapshots.py` | "Save snapshot" plus automatic samples and alert pictures, each with a `.json` of what was read. **This is how the user sends evidence.** |

## 4. Problems already hit (don't reintroduce them)

- Clicking the star instead of the magnifier. Fix: find the magnifier by shape and check the bar opened.
- Esc opening the profile, and the info box not closing. Fix: drag-close, and Esc only when the map is covered.
- Spanish UI ("Recolector", "2 032 680"). Fix: language-free checks.
- Zooming out over the whole map. Fixes:
  - never count notches from fully out
  - wait for each notch to finish
  - calibrate by measured scale
- His farms hidden as "other". Fix: farms only match the same spot; don't copy a neighbour's owner.
- False "He pulled a farmer back". Fixes:
  - exact on-screen check
  - two misses
  - keep the sweep's estimate for matching (the exact X:Y is for display only)
- Phantom marches from the side panel. Fix: those lines are ignored.
- Red hostile diamonds read as pickaxe badges. Fix: a round-vs-diamond check.
- The PowerShell updater was silently blocked. Fix: the separate updater exe.
- The GitHub API rate limit. Fix: use the web redirect.
- A 5 s idle pause between rounds. Removed.
- Blind tap in the middle of the screen when no farm badge was found clicked **his city**.
  Now it only taps a red badge near the middle.
- Zoom steps waited up to 2 s each for a perfectly still picture (the map never is). Now
  "nearly still", capped at 0.7 s; lost steps are caught by the switch-point check.
- The same farm checked over and over: its zoomed-out estimate drifts a few tiles. Farms now
  match within 5 tiles, or by their exact X:Y.
- The first sweep restarted from the start after any problem (looping on the farms next to
  his base). Now it carries on where it stopped and skips a bad spot.
- After checking something it went straight on and ignored new things on screen. Now it
  looks again and checks those too.
- The mouse left resting on the map after a tap or drag opened name cards and menus under
  it, and a short closing drag counted as a long-press on his city. Now the mouse moves to
  the top bar after every action, and the closing drag is long (12% of the height).
- Reading the user's troop spots read the whole screen per troop (1-3 s each). Now it reads
  only the troop's info box.
- Unconfirmed armies were followed but hidden from the list. Now everything it follows is
  listed, and after 3 failed checks it stops following them.
- Every zoom check and camera read took ~1 s: the text reader enlarged the small top strip
  5x. Now a strip reader at its own size (~0.13 s). goto, the search bar and the name check
  read only the strip / bar row / middle of the screen, not the whole screen.
- A farm box left open blocked the zoom wheel and the bot zoomed out 10 notches. Now the box
  is always closed, boxes are closed before zooming, and zooming out stops where the level
  numbers vanish (`_out_keeping_tags`).
- Update check SSL failure on Windows: certifi added. Phone section order was reversed.

## 5. Open work / to verify in the real game

1. **Speed:** a full track round should take about **5–10 s**. Check the panel log's
   "Round (…): N spots … in X s - moving / looking / checking owners" lines and cut whatever
   dominates. Text reading (rapidocr) of the top strip is the main per-spot cost.
2. **Farms:** check his farms are found at the user's searching zoom. The red badge size
   range was widened; it was tuned on one sweep screenshot plus one snapshot.
3. Per-army features in the real game: follow, alerts, ETA, farm details.
4. Not built yet (needs screenshots): sending a scout, sharing a location to alliance chat.

## 6. Testing

- Fast and pure (no browser): `python -m pytest tests/test_real_screens.py tests/test_gatherers.py tests/test_zoom_measure.py tests/test_updater.py -q`
- Browser-based, using the fake game `tests/fake_game/rok.html` with Playwright Chromium:
  `tests/test_hunter.py`, `tests/test_patrol.py` (~8–10 min each), `tests/test_panel.py`.
- New evidence from the user: snapshots (jpg + json) go into `tests/real_screens/gather/`
  with a test asserting the right reading.
- **On the PC with the real game you can do what the cloud couldn't:**
  - run `python -m rokwatch.panel` from source
  - press the buttons
  - watch the timing log
  - capture screenshots
  - tune the detectors live

## 7. Build and publish

- Repo: **https://github.com/elvira-alec/ROK-bot** (public). Branch `main`.
- `.github/workflows/windows-exe.yml` builds `dist/RoKWatcher/` with PyInstaller
  (`build_exe.py`, which also builds `RoKWatcherUpdater.exe`).
- **On every push it builds and tests only:**
  - the exe self-test
  - the panel driving the fake game in Edge
  - window mode
  - the update check
  - a real update via `--test-update`
- **Publishing:** Actions → windows-exe → Run workflow with `publish=true`, and optionally
  `version=1.x` to set the number or replace that release. Releases are `v1.0`, `v1.1`, …
  (see the Releases page for the latest), plus a `latest` prerelease. The app's updater installs them by itself.
- To push from the PC, Git must be signed in to GitHub (Git Credential Manager or `gh auth login`).
- **Publish every change (next version number) unless the user says not to.**
