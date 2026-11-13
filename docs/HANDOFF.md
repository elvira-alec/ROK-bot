# RoK Watcher: handoff for working on the user's PC

This is the complete handoff from the cloud session to a Claude Code session on the
user's Windows PC, right next to the real game. The cloud session could only test against
screenshots and simulated games. **On the PC you can run the bot against the real game,
watch it, record it, and fix things with real evidence.** That's the point of this handoff.

`CLAUDE.md` (in the repo root, loaded automatically) is the short version of the rules and the
code map. This file is the long version: setup, a full test plan, how to gather evidence,
how everything works inside, every problem met so far with its cause and fix, and what's still
open.

---

## 0. The user and what they want

- **The user:** `[JONS]IronAR`, kingdom 4183, city about X:210 Y:422.
- **The target:** `[P83L]JONH WICK`, city X:162 Y:374. He bullies the user's farmers.
- **What they want:** a watcher that finds all his armies around his city, both farming and
  marching. It confirms each one is his, follows them **fast**, keeps checking his city, and
  alerts the user's phone (ntfy):
  - when one heads for or comes close to the user's city or troops
  - when he pulls a farmer back, or a farmer is about to leave (10 / 5 min warnings)
  - when the user is scouted or attacked (only from the game's own red eye / red swords /
    red border)

  It can optionally auto-recall the user's troops, and it logs when he's online. Only
  intentional moves count as online, never gathering itself.
- **How they work:**
  - They want speed and reliability: "go to a spot, scan as soon as the icons load, move on".
  - Plain explanations, short answers, line icons only (no emojis).
  - **Publish a new version after every change unless they say not to.**
  - They're frustrated by slowness and by fixes that didn't hold. **Measure before claiming,
    and test against the real game.**

---

## 1. Setting up on the PC

1. Install Git, and **Python 3.12** (python.org, tick "Add to PATH").
2. Get the code:
   ```
   git clone https://github.com/elvira-alec/ROK-bot.git
   cd ROK-bot
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt pytest pyinstaller
   python -m playwright install chromium        (only needed for the fake-game tests)
   ```
3. Run the bot **from source** (instead of the exe), so changes take effect immediately:
   ```
   python -m rokwatch.panel
   ```
   This opens `http://127.0.0.1:8765`. Settings live in `config.yaml` / `setup.yaml` next to the
   code. Copy them from the user's RoKWatcher folder to keep their target and zoom set-up.
4. The game: the official Rise of Kingdoms PC client in **window mode**, English. The
   window title is "Rise of Kingdoms". The bot moves the real mouse. **Moving the mouse into a
   screen corner stops it** (pyautogui fail-safe).
5. Pushing from the PC: Git must be signed in to GitHub (Git Credential Manager prompts
   on the first push, or use `gh auth login`).

---

## 2. Gathering evidence (do this first, before changing code)

| Tool | How | What it gives |
|---|---|---|
| **Debug recording** | Panel → More → Snapshots → *Start debug recording*, reproduce the problem, *Stop*, *Download the last recording*. Files in `debug_trace/<time>/` | Every picture the bot looked at (`frames/*.jpg`, up to 5/s), and `trace.jsonl`: every click/drag/scroll/key/typed text with positions, every look (camera + what was detected with pixel positions), zoom steps (`notch`, `zoom`), jumps (`goto` / `goto_done` with seconds), owner checks (`check_owner`, `owner` + the text read), round summaries (`round`), and all log messages. Each line has the time and the frame on screen. |
| **Round timing** | Panel log lines `Round (track): 3 spots, … in 4.1 s - moving 0.9 s, looking 2.3 s, checking owners 0.0 s` | Where each round's time goes. |
| **Snapshots** | Panel → Save snapshot (plus automatic ones) | A picture plus a `.json` of what the bot read. |
| **Screen video** | Any recorder | The cloud session can read video frames (OpenCV); traces are more precise. |

**Workflow:** reproduce the problem with a debug recording, read `trace.jsonl` next to the
frames, and find the first step that went wrong. Turn that frame into a regression test in
`tests/real_screens/...`, then fix, test and publish.

---

## 3. Real-game test plan (run these in order; note pass/fail and timings)

| # | Test | How | Expect | If it fails, capture |
|---|---|---|---|---|
| T1 | Connect | Panel step 1, window title "Rise of Kingdoms", Connect | "Connected to the game window" | log |
| T2 | Automatic zoom setup | Step 3 → *Set up the zoom automatically* (menus closed) | Ends at the searching zoom (furthest step with level numbers on resource points). Message gives the gap. Repeat 3 times: **the same gap every time**. | debug recording |
| T3 | Bounce between zooms | More → tools, or start watching | Searching ↔ close zoom, never further out than the searching zoom | debug recording (`notch` / `zoom` events) |
| T4 | Jump speed | Check it works → Jump to his city X:162 Y:374 | Under ~2 s from the magnifier tap to arrival; **digits typed correctly** (game drops keys typed too fast: typing is 50 ms/key) | recording: time between `goto` and `goto_done` |
| T5 | First sweep | Step 4 → Watch & track him | Goes **straight to his city**, then searches the grid around it (radius in step 2), no wandering elsewhere | recording + round lines |
| T6 | His farms found | His farms near his city | Red pickaxe badges seen at the searching zoom; each checked once: zoom in, tap the camp, read "Gatherer [P83L]JONH WICK", close, back out. All farms visible in that close view are tapped one after another. Listed in *His armies*. **Never taps his city.** | recording (`check_owner` / `owner` events with the text read) |
| T7 | Following | After the sweep | Rounds go between his armies and his city only; a round should take about **5–10 s** | round lines |
| T8 | Marching army | When he sends troops | Followed along its line; "moving close" / "heading for" alert when aimed at the user | recording |
| T9 | Farm timing | After a farm is read twice a few minutes apart | Row shows taken/full, minutes gathering, leaves in ~N min; alerts at 10 and 5 min | panel screenshot |
| T10 | Stop | Press Stop mid-jump or mid-zoom | Stops within about a second | — |
| T11 | Scouted / attacked | When it happens | Alert from the red eye / swords only | snapshot |
| T12 | Update | More → Updates → Check now | Finds the newest version; Update now → progress window → restart | `%TEMP%\rokwatch-update.log` |
| T13 | Close with page | Close the panel tab | Program quits after a few seconds (reload doesn't) | — |

---

## 4. How it works inside (deeper than CLAUDE.md)

### 4.1 A round of "Watch & track him" (`hunter.py: Hunter.round`)

1. **Zoom:** if not known to be at the searching zoom, `to_sweep()`. On the first round,
   `goto(his city)` first. Every 5 min `keep_scale()` checks the zoom: two scale readings must
   agree and drag-back must return home, else nothing changes; at most one notch per check.
2. **Plan:**
   - `sweep`: the first time, if a sweep was cut short (resumes; `_sweep_left`), if an army of
     his got lost away from home, or every 30 min.
   - `track`: his known armies (predicted positions) plus his city.
   - `base`: his city only.
3. **Per spot:**
   - `pan_to()`: one drag from the known camera, or typed coordinates if more than 3 screens away.
   - `look_spot()`: `fresh_frame()` waits until the picture is nearly still, max 0.4 s. Then
     `detect(frame)` and `read_camera(frame)` run **in parallel**, and danger icons are checked.
4. **New things at a spot** (`_check_new_here` → `_unknown`):
   - skip his city itself (within 3.5 tiles)
   - an army on a march line touching his city counts as his without zooming in
   - otherwise `identify()`: zoom in once, nearest first; `_check_owner` (drag when nearby,
     else typed)
   - farm: `_identify_farm` taps only a red badge near the middle, then `_read_farm_box`
     (OCR of the box only), then `_farms_in_frame` taps the other visible farms
   - marching army: reads the name label (`find_player`)
   - then back out, and **look again** for new things on screen (up to 2 more times)
5. **`Tracker.match`:**
   - farms match within 5 tiles or by their exact X:Y
   - marching armies match against `predict()` (along the line, measured speed or 4
     tiles/min) with a growing tolerance
   - "gone" only if the spot was on screen (`_on_screen`), twice; other players' armies are
     dropped at once
6. **Events and alerts:** `army_out`, `army_close`, `hostile`, `pulling_back`, `all_home`,
   `farm_left` (finished vs pulled back early), farm leave warnings at 10 / 5 min,
   danger-icon alerts, optional auto-recall.
7. **Logging and timing:** the activity log (`activity.py`, intentional moves only), the farm
   reserves re-read (`_refresh_one_farm`, at most one per round, every 2 min or more), and
   the timing line.

### 4.2 Detection (`screen.py`), tuned on real screenshots (all relative to a 1320 px tall game)

| Function | What it does |
|---|---|
| `camera_coords` / `top_texts` | OCR of the padded top-left strip (0.4W × 0.08H, cut at the magnifier), enlarged on small windows. Uses `ocr_strip_engine()`: the reader at the strip's own size (~0.13 s; the default engine blows small images up to 736 px and took ~1 s). Cached per frame. `parse_coords` copes with the reader's slips (`X:18QY`, `Y-382`, a trailing 9 from the magnifier). |
| `view_level` | `close` if the power or VIP block is in the strip. `far` if the globe button is present. Otherwise `patrol`. |
| `count_level_tags` | Light tags with a dark number under resource points. ≥3 = level numbers shown, ≤1 = gone. |
| `find_map_icons` | Army diamonds: grey-lavender by day, mauve at night. Plain diamond = scout, unless it's army-sized (≥46 px). Red diamond = hostile. |
| `find_march_lines` | Dotted lines. Ignores the chat (bottom 14%) and the left side panel (x < 13%). |
| `find_gather_badges` | Round red pickaxe badge, 0.016–0.06 H. Must be round (empty bbox corners, red at the diagonal points), not a diamond. |
| `find_info_box` | The light info box (resource or army), left or right of the tap. |
| `danger_icons` | Red eye (scouted) vs red crossed swords (attacked), told apart by the white symbol. |
| `find_magnifier` / `search_go_button` | The coordinate search: the magnifier template, and the blue button of the opened bar. |

### 4.3 Zoom (`game.py`)

- `zoom_cfg = {gap, t_out, t_in, close_ratio}` in `hunt.zoom`.
- `to_close` / `to_sweep` step without checks up to just before the 3D ↔ map switch, then step
  one at a time while checking `view_level`, then the remainder. Each step `_settled()`: nearly
  still, max 0.7 s.
- **Automatic setup** (`auto_calibrate`, preferred): all the way in; step out; the last 3D step
  = close; the last step with level numbers = searching. Drags to find resource points if
  none are in view; checks before saving.
- Manual two-button setup (`calibrate_close`) uses `measure_scale` drags; the user found it
  unreliable.
- **Guards (v1.11):** an open info box is closed before any zoom (`_no_popup`), and a notch
  that changes nothing while a box is open closes it and repeats. Stepping out past the
  switch point (`_out_keeping_tags`) counts the level-number tags after each notch: if they
  vanish, it steps back in and lowers `gap` for the session.

### 4.4 Panel API (`panel.py`)

- **GET:** `/api/state`, `/api/settings`, `/api/phone`, `/api/activity`, `/live.jpg?w=`,
  `/view.png`, `/snap/<name>`, `/api/snapshots.zip`, `/api/trace.zip`
- **POST:**
  - commands: `/api/open`, `/api/start`, `/api/hunt`, `/api/stop`, `/api/look`,
    `/api/goto {x,y}`, `/api/axes`, `/api/recall`, `/api/test_alert`
  - zoom: `/api/auto_zoom`, `/api/remember_zoom {kind}`
  - other: `/api/snap {note}`, `/api/army {id, follow, alert_moves, alert_home}`,
    `/api/settings`, `/api/update {action}`, `/api/trace {on}`, `/api/quit`, `/api/bye`

### 4.5 Settings (`config.yaml` + `setup.yaml`)

- `game_window`
- `notify.ntfy_topic`
- `hunt`:
  - target: `player_name`, `city`, `radius`
  - zoom: `zoom`
  - tuning: `full_sweep_minutes` (30), `checks_per_sweep`, `match_distance` (12),
    `near_city` (6), `near_site`, `moving_close`, `track_my_gathering_minutes` (10),
    `pause_between_sweeps` (0)
- `patrol`: `axes` (tiles per pixel at the searching zoom), `auto_recall`, `recall_scope`,
  `sites`
- `snapshots.*`, `updates.check_minutes`, `close_with_page`, `panel_on_phone`, `targets`

### 4.6 Other files

| Path | What |
|---|---|
| `his_activity.csv` / `his_watch.csv` | The activity log |
| `farm_capacity.json` | Learned full amounts and his army loads |
| `his_armies.csv` | Per-round positions |
| `rokwatch.log` | The log |
| `snapshots/` | Saved snapshots |
| `debug_trace/` | Debug recordings |

---

## 5. Everything that went wrong so far (cause → fix)

**Coordinate search**
- Clicked the star instead of the magnifier: the click was at a fixed offset from the OCR
  text. Fix: magnifier template matching, and check the bar opened (blue button).
- Jumps were slow:
  - a full-screen OCR while waiting for the camera (2–3 s each)
  - pyautogui's 50 ms pause after every action
  - long fixed sleeps

  Fixed: strip-only reads, 10 ms pause, short polls.
- The game dropped digits typed at 15 ms/key, causing wrong jumps that looked like "searching
  random places". Now 50 ms/key everywhere.

**Popups and the mouse**
- Esc doesn't close the resource box on PC, and Esc on the plain map opens the profile. Fix:
  close with a map drag away from the box; Esc only when the coordinates are covered.
- A short close-drag with a hold was taken as a **long-press on his city**, opening its menu.
  The mouse resting on the map kept name cards open. Fix: long drag (12% H). The mouse now
  parks **outside the game window** (parking it on the game's top bar looked like it was stuck
  and clicking up there; reverted).
- Tapped the screen centre when no badge was found, which near his base was his city. Fix:
  only tap a red badge near the middle.

**Zoom**
- Counted notches from fully zoomed out: notches got lost and it ended far too far out. Fix:
  two zooms relative to each other, with switch-point checks.
- Waited for a perfectly still picture after each notch (the real map never is): 2 s per
  notch. Fix: "nearly still", max 0.7 s.
- Manual calibration gave 1 or 3 notches at random, and the 5-minute zoom check zoomed way
  out. Cause: one misread coordinate (e.g. "4368") made a scale measurement garbage. Fix:
  measurements must come back to the start point and look sane; two must agree; one notch
  max. Prefer the automatic setup.

**Detection**
- Farms gathering aren't diamonds: they're resource points with a **red pickaxe badge**.
  Their owner is only in the resource box ("Gatherer …"). Added badge detection plus
  tap-and-read.
- The Spanish game ("Recolector", "2 032 680"): owner found by name anywhere in the box; power
  in any number format.
- A red hostile diamond was read as a pickaxe badge. Fix: the round-vs-diamond test.
- Fake march lines from the left side panel. Fix: ignored.
- A big plain diamond is an army whose symbol isn't shown (a game setting; now on). Fix: size
  rule.
- March lines aren't drawn near the screen edge. Fix: edge armies are "unsure" and keep a
  known march.

**Tracking logic**
- His farms were labelled "other": they copied a neighbour's owner. Fix: farms only match
  their own spot.
- The same farm was re-checked endlessly: the zoomed-out estimate drifts a few tiles with the
  camera. Fix: 5-tile or exact X:Y match.
- The first sweep restarted from scratch after any error, looping on the farms by his
  base. Fix: it resumes and skips a bad spot.
- After checking, new farms on screen were ignored. Fix: look again and check those.
- False "pulled a farmer back" alerts for farms not even on screen. Fix: exact on-screen
  projection, and two misses.
- Unconfirmed armies were followed but hidden. Fix: listed; dropped after 3 failed checks.
- The same moving army was counted twice in one round. Fix: `same_spot` with an 8-tile
  allowance for moving ones.
- One bad round stopped the watch. Fix: recover and carry on.
- Started by wandering around wherever the camera was. Fix: go to his city first.
- **The search order hopped across his city:** spots were sorted by distance only, so it
  went east, west, north, south. Fix: a spiral, ring by ring, each spot next to the last:
  39 screens of travel instead of 124 for the same area.
- **Things under the troop list / side panel / chat / top bar were never seen:** spots
  were spaced as if the whole screen showed map. Fix: spacing uses only the visible map
  area (x 14–88%, y 12–84%), the same area `_on_screen` uses.
- **"Moving close" depended on where the camera stood:** it used the visible line end,
  which is where the screen cut the line. Fix: the closest point of the army's path,
  followed 30 tiles past the cut. The alert names every one of the user's spots that's
  close, nearest first.

**Speed**
- A 5 s idle pause between rounds. Removed.
- (v1.11, from the user's debug recording) every zoom-level check and camera read was ~1 s of
  OCR (the reader enlarged the small top strip 5x); goto read the whole screen before even
  tapping the magnifier, and again to find the X/Y boxes (2.6 s); the name check at close zoom
  read the whole screen (1.5–2 s); pans used two drags. Now: strip reader ~0.13 s, the bar row
  only (~0.2 s), the middle of the screen only (~0.35 s), one drag covers 60% of the view.
- (v1.13) The user reported armies not being recognised, maybe when zooming in makes the
  diamond turn into 3D troops. Their recording had no marching army in it (only his 4
  farms, tracked fine, rounds 4-5 s). Added the army recorder (`armyrec.py`) to get that
  evidence: full-size frames through every zoom step plus detections per frame.
- (v1.12) Fixed waits trimmed: closing a box waits only until it's gone (was a flat 0.35 s),
  click-to-type 0.06 s, name re-read 0.2 s. The debug recording now logs how long every
  control took (`action.ms`) and every screen capture (`frame.capture_ms`): use these to find
  any gap that's left on the user's PC.
- (v1.12) Live view: its own loop, ~3 pictures a second, 960 px (640 on phones), quality
  70; only sent when the picture changed (`/live.jpg?have=<id>` returns 204).
- (v1.11) A farm box left open (an error while reading it) made the zoom wheel do nothing,
  so the bot zoomed out 10 notches. Fixes: the box is always closed (`finally`), boxes are
  closed before zooming, zoom-out stops where the level numbers vanish, and the crash itself
  (`ArmySeen` had no `info`) is fixed.
- Reading the user's own troop spots read the whole screen per troop. Now only the troop's box.

**Program and updates**
- The PowerShell updater was silently blocked. Fix: a separate RoKWatcherUpdater.exe.
- The update check hit the GitHub API rate limit. Fix: the releases/latest redirect.
- No console window; single instance; Quit in the panel; closes with the page.
- (v1.11) Update check failed with `CERTIFICATE_VERIFY_FAILED` on the user's PC (Windows
  hadn't fetched GitHub's root certificate yet). Fix: Windows' store plus certifi's list.
- (v1.11) On the phone the sections came out 4, 3, 1, 2 (a prepend loop reversed them). Now:
  Live view, His armies, 1, 2, 3, 4, Recent alerts, Alerts, Activity.

---

## 6. Still open: what to verify or fix on the PC, most important first

1. **Round speed in the real game.** Target 5–10 s for a tracking round. Use the round lines and
   the trace (look `seconds`, `goto_done` seconds, gaps between events) to find the biggest
   cost. Suspects:
   - OCR time per read on the user's CPU
   - `_settled` / `fresh_frame` caps
   - owner checks (zoom in/out each time)
2. **The sweep pattern** (now a spiral over the visible area; verify in the real game). Check `sweep_points` covers the radius around his city sensibly at
   the user's searching zoom, and that `pan_to` lands where intended. Compare the trace
   `look.camera` with the planned spots. If `patrol.axes` is off, the drags miss and it looks
   random. Re-learn with the automatic setup.
3. **Farm checks in the real game.**
   - Does tapping just under the badge always open the box?
   - Are all farms in the close view tapped?
   - Does closing always work?
   - Is the right farm matched to the right badge (`_farms_in_frame` uses `close_ratio`)?
4. **Never interacting with his city or anything else unintended.** Check every `action` in
   the trace.
5. **Typing reliability at 50 ms/key** (check typed digits in the trace match `goto x/y`).
   Raise it if the game still drops keys; lower it if it's solid.
6. **Farm timing** (load learning, 10/5 min alerts) over a real session.
7. **Not built:** sending a scout (needs screenshots of the flow), sharing a location in
   alliance chat.

---

## 7. Tests

- **Fast:** `python -m pytest tests/test_real_screens.py tests/test_gatherers.py tests/test_zoom_measure.py tests/test_updater.py tests/test_trace.py -q`
- **Fake game** (Playwright Chromium, slow): `tests/test_hunter.py`, `tests/test_patrol.py`
  (~8–10 min each), `tests/test_panel.py`, `tests/test_phone.py`, `tests/test_snapshots.py`.
- **Real-screen fixtures:** `tests/real_screens/` (+ `crops/`, `icons/`, `gather/`, `zoom/`).
  Add new real frames there, with a test asserting the right reading. That's how fixes stay
  fixed.
- **Simulators:** `tests/test_zoom_measure.py` has `ZoomSim` / `TagSim` (zoom levels built
  from real screenshots). `tests/fake_game/rok.html` is the fake game for end-to-end runs.

---

## 8. Build and publish

- On every push, GitHub Actions `windows-exe.yml` builds and runs the Windows checks:
  - the exe self-test
  - the panel driving the fake game in Edge
  - window mode
  - the update check
  - a real update via `--test-update`
- **Publish:** Actions → windows-exe → Run workflow with `publish=true` and `version=1.N`
  (the next number). Or ask the cloud session to do it. Releases are `v1.x`; the panel's
  updater installs them by itself.
- **The user wants every change published unless they say otherwise.**
