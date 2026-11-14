# RoK Watcher: full handoff (state at v1.15)

Read this whole file before changing anything. `CLAUDE.md` (repo root, loaded automatically) is
the short version. This is the long version:

- who the user is and how to work with them
- how to set up on their PC
- **the rules that stop you undoing earlier fixes**
- how every part works, with the numbers that were tuned and why
- every problem met so far (cause → fix → the test that guards it)
- what's open, what the user asked for last, how to test, how to publish

The previous sessions ran in the cloud. They had only screenshots, recordings and a fake game.
**A session on the user's PC can drive the real game.** Use that, but keep every rule below.

---

## 0. The user

- **The user:** `[JONS]IronAR`, kingdom **4183**, city about **X:210 Y:422**. Windows laptop,
  official Rise of Kingdoms **PC client in window mode**, game in **English** (Spanish was used
  before and must keep working). The game window is about **1852×1042** px (16:9). The
  screenshots in `tests/real_screens/*.png` are 2868×1320 (an older setup). Code must work at
  both sizes: **always scale by the window height `H`, never use fixed pixels.**
- **The target:** `[P83L]JONH WICK`, city **X:162 Y:374**. He bullies the user's farmers.
- **What they want:** find all his armies around his city, farming and marching. Confirm each
  is his, follow them fast, and keep checking his city. Alert the user's phone (ntfy):
  - when one heads for, or comes close to, the user's city or troops
  - when he pulls a farmer back; 10- and 5-minute warnings before a farmer leaves
  - when the user is scouted or attacked: **only** from the game's red eye, red swords or red
    screen border. Never from march lines.

  It can optionally auto-recall the user's troops. It logs when he is online: only
  intentional moves count, never gathering or an army walking home by itself. It also checks
  the user's own troops (v1.15).
- **How they work, and what makes them angry:**
  - Speed. "Go to a spot, scan as soon as the icons load, move on." Every step under a second.
    They notice gaps of 1–2 s.
  - **Fixes that don't hold, and claims without evidence.** Measure, test against real
    pictures, and say plainly what was and wasn't tested in the real game.
  - Short, clear answers. No emojis anywhere in the UI (line icons only, the SVG sprite in
    `panel.html`).
  - **Publish a new version after every change unless they say not to** (section 9).
  - They send evidence as debug recordings, army recordings, snapshots and screen videos.
    Turn each into a regression test.
  - Their messages can be terse, typo-heavy and sometimes angry. Read for the intent.
  - When they say "don't do anything yet", only answer and wait.

---

## 1. Setting up on the PC

1. Install Git and **Python 3.12** (python.org, tick "Add to PATH").
2. Get the code:
   ```
   git clone https://github.com/elvira-alec/ROK-bot.git
   cd ROK-bot
   python -m venv .venv
   .venv\Scripts\activate
   pip install -r requirements.txt pytest pyinstaller
   python -m playwright install chromium        (only for the fake-game tests)
   ```
   `rapidocr_onnxruntime==1.2.3` and `onnxruntime==1.29.0` are **pinned on purpose**. The text
   reading is tuned on those versions (section 3.3). Don't upgrade them casually.
3. Run **from source**, so changes apply at once:
   ```
   python -m rokwatch.panel
   ```
   - This opens `http://127.0.0.1:8765`.
   - Settings are in `config.yaml` / `setup.yaml` next to the code. Copy them from the user's
     installed RoKWatcher folder (next to `RoKWatcher.exe`) to keep their target, zoom setup
     and ntfy topic.
   - **Close the installed RoKWatcher first:** both use port 8765, and the second instance
     only opens the panel of the first.
4. **The bot moves the real mouse.** Moving the mouse into a screen corner stops it
   (pyautogui fail-safe). Don't use the PC while it runs.
5. To push, Git must be signed in (Git Credential Manager prompts on the first push, or
   `gh auth login`).
6. Commit messages: plain English, what changed and why. **No model names or IDs** in commits,
   code or docs.

---

## 2. RULES: don't undo earlier work

Each rule below was learned from a real failure in the user's game. The test named after
"guard:" protects it. If you need to change one, read its history in section 6 first, and keep
or replace its test.

### Clicking and the mouse
1. **Never tap the orange star** next to the magnifier. Find the magnifier by its shape
   (`find_magnifier`, template plus several sizes) and confirm the bar opened by its **blue
   search button** (`search_go_button`). Guard: `test_patrol.py::test_jump_taps_magnifier_not_star`
   (no `starClicks`).
2. **Never press Esc on the plain map:** it opens the user's profile. Esc doesn't close the
   resource box on PC anyway. Close boxes with a **map drag away from the box, 12% of the
   height long** (`close_popups`). A short drag with a hold was taken as a long-press on his
   city and opened its menu. Esc only when something covers the coordinate readout. Guard:
   real-screen and fake-game tests of `close_popups`.
3. **Never tap blindly.** When no red pickaxe badge is near the middle, don't tap the middle:
   near his base that's his city. `_identify_farm` only taps a red badge within 0.3·H of the
   middle.
4. **Park the mouse outside the game window after every action** (`desktop._park`: right of
   the window, else below it, never a screen corner). Resting on the map opened name cards.
   Parking on the game's top bar looked stuck and clicked up there, so that was reverted.
5. **Typing at 50 ms per key** (`desktop.type`, `typewrite(interval=0.05)`). At 15 ms the
   game dropped digits and jumped to wrong places, which looked like "searching random
   places". `pyautogui.PAUSE = 0.01`.
6. **Drags: 0.18 s move plus 0.1 s hold before release** (no fling or drift).

### Zoom
7. **Two zooms, relative to each other, never counted from fully out.** Close zoom = the last
   3D step. Searching zoom = the furthest-out step where resource points still show level
   numbers. `zoom_cfg = {gap, t_out, t_in, close_ratio}`. Counting from fully out lost notches
   and ended far too far out.
8. **Never zoom past the searching zoom.** `_out_keeping_tags` counts level-number tags
   after each notch out. When they vanish (≥3 → ≤1) it steps back in and lowers `gap`.
   Guard: `test_zoom_measure.py::test_zoom_out_stops_where_the_level_numbers_vanish`.
9. **An open info box blocks the zoom wheel.** Close boxes before any zoom (`_no_popup`). A
   notch that changes nothing while a box is open closes it and repeats. The farm box is
   closed in a `finally`. Missing this once made the bot zoom out 10 notches. Guard:
   `test_open_box_is_closed_before_zooming_out`, `test_farm_box_closed_even_if_reading_fails`.
10. **Wait for each notch, but only until "nearly still"** (`_settled`: <1.2% of pixels
    changing, max 0.7 s). The real map always moves (water, flags, marches), so waiting for
    "perfectly still" cost 2 s a notch. Lost notches are caught by the switch-point check.
11. **Scale checks (`keep_scale`) need two agreeing readings, and move at most one notch.** A
    single misread coordinate once made a garbage scale and zoomed far out.
12. **Prefer the automatic zoom setup** (`auto_calibrate`). The user found the manual
    two-button setup unreliable, but wants both kept.

### Reading the screen
13. **The coordinates strip and zoom checks use the strip reader** (`ocr_strip_engine()`:
    detection at the image's own size, `limit_type="max"`, 1280, `width_height_ratio=-1`). The
    default reader blows small images up to 736 px on the short side: ~1 s per read. Strip:
    ~0.13 s. Never put back a full-screen read in a per-step path (goto, zoom checks,
    look_spot).
14. **The top strip is cut at the magnifier**, which reads as "Q" or a trailing "9".
    `parse_coords` copes with the reader's slips: `X:18QY` = 180, `Y-382`, `180.Y`, a 4th
    digit > 1199 dropped. Guard: `test_gatherers.py::test_coordinates_read_with_glitches`,
    `test_quick_top_strip_reads_like_the_slow_one`.
15. **The power number can come with junk** ("X 2,086,708", "2090,028", "VIPT").
    `is_simple_view` allows it. Spanish uses "2 032 680" / "2.032.680". Never rely on
    language-specific words: the farm's owner is found by **his name anywhere in the box**
    ("Gatherer" / "Recolector").
16. **The farm box is read with the normal reader** (small text needs the enlargement; ~0.3 s).
17. **March lines ignore the left side panel (x < 13% of W) and the chat (bottom 14%).** Those
    made phantom marches.
18. **A red hostile diamond is not a pickaxe badge.** The badge must be round (empty bounding
    box corners, red at the diagonals).
19. **A plain diamond of army size is an army** (≥46 px at H=1320). Scouts are 33–39 px,
    armies 52–58 px. Never confuse scouts with armies.

### Tracking
20. **Farms match only their own spot** (within 5 tiles, or by their exact X:Y). They never
    copy a neighbour's owner: that once labelled his farms "other".
21. **"Gone" only if the spot was really on screen** (`_on_screen`: exact projection inside
    the visible map area x 14–88%, y 12–84%), **and missed twice** for things that stay put.
    Matching uses the sweep's estimate; the exact X:Y is for display. These fixed false "He
    pulled a farmer back" alerts.
22. **A farm's estimate wobbles a tile or two:** no trail for farms that stay put, and they
    never count as heading home. Guard: `test_gatherers.py::test_farm_estimate_wobble_is_not_a_move`.
23. **Activity counts only intentional moves** (`activity.ACTIVE`): farm started, scout sent,
    recalled farm, sent out, recalled mid-march, scouted/attacked you. Not gathering, not an
    army walking home after gathering.
24. **Scouting alerts come only from the game's red eye / red swords / red border.** No
    march-line scout alerts (the user said so explicitly).
25. **Go to his city first** when watching starts. It used to wander wherever the camera was.
26. **The sweep is a spiral over the visible map area**, ring by ring, each spot next to the
    last. It resumes where it stopped (`_sweep_left`) and skips a bad spot instead of
    restarting.
27. **After checking owners, look again** for new things on screen (up to 2 more times).
28. **Unconfirmed armies are listed**, and dropped after 3 failed checks.
29. **"Moving close" uses the closest point of the army's path**, followed 30 tiles
    (`PAST_EDGE`) past where the screen cut the line. Not the visible line end.

### Close view (armies)
30. **At the close view a marching army is a cluster of small red troops** with the owner's
    name in **tiny white letters just above it.** A normal read misses it.
    - `find_troop_clusters` finds the cluster: a red mask, closed, ≥10 separate bits, fill
      0.06–0.45. The HUD is excluded.
    - `read_army_label` reads the strip above it enlarged 3x.
    - `army_owner_at_close` returns **'him' on a clear read (name_score ≥ 0.75)**. It returns
      **'other' only when the name part has nothing of his** (`_name_part_similarity` ≤ 0.3,
      text ≥ 7 characters).
    - Other alliances' tags look like his: `[IP83]` vs `[P83L]`. So never decide "other" on
      the whole-label score.
    - The hunter needs **two looks agreeing** before it says "other".

    Guard: `test_real_screens.py::test_troop_cluster_found_and_never_mistaken_for_him`,
    `test_his_army_close_up_is_his`.

### Program
31. **The updater is a separate exe** (`RoKWatcherUpdater.exe`, run from a temp copy). The
    PowerShell updater was silently blocked.
32. **The update check uses the `releases/latest` web redirect** (the API rate-limits). The API
    and PowerShell are fallbacks.
33. **The SSL context is Windows' certificates plus certifi** (`updater.ssl_context`). Windows
    can lack a root certificate it hasn't fetched yet.
34. **No console window, single instance, quits with the panel page** (option), Quit in the
    panel.
35. **Phone section order:** Live view, His armies, Your troops, 1 Connect, 2 Who to watch,
    3 Zoom, 4 Watch, Recent alerts, Alerts, Activity. The old prepend loop reversed it.

---

## 3. How it works inside

### 3.1 Files

| File | What |
|---|---|
| `rokwatch_app.py` | Exe entry. Relaunches itself without a console; single instance; `--updated`; `--test-update URL VER` (build check). |
| `updater_app.py` | The updater exe (Tk progress window). Waits for the bot's PID to exit, downloads the zip, replaces the files (keeps config, logs, snapshots), restarts the bot. Log: `%TEMP%\rokwatch-update.log`. |
| `rokwatch/desktop.py` | Windows game control. Finds the window by title. Captures with `PrintWindow` (works when covered; `mss` as fallback). Real mouse via pyautogui. `_front()` brings the game forward (Alt tap + SetForegroundWindow, 0.25 s, **only when it isn't already in front**). |
| `rokwatch/browser.py` | The same interface for a browser page (the fake game in tests). |
| `rokwatch/game.py` | Everything that acts on the game (section 3.2). |
| `rokwatch/screen.py` | All detection (section 3.3). Pure functions on a frame, no clicks. |
| `rokwatch/hunter.py` | "Watch & track him" plus `Tracker` (section 3.4). Also "Your troops". |
| `rokwatch/patrol.py` | The older simple patrol (places list, leaving/returning). `learn_axes`, `AttackAlertDetector`. |
| `rokwatch/farms.py` | Resource point full amounts (`FULL`), reserves, rates, loads (`farm_capacity.json`). |
| `rokwatch/activity.py` | `his_activity.csv` and `his_watch.csv`, giving the panel heatmap. |
| `rokwatch/worker.py` | One worker thread runs the panel's commands (`_do_<cmd>`). `Status` is what the panel shows. |
| `rokwatch/panel.py`, `panel.html` | Web panel and API (section 3.6). |
| `rokwatch/trace.py` | Debug recording (section 4). |
| `rokwatch/armyrec.py` | Army recorder (section 4). |
| `rokwatch/snapshots.py` | Snapshots (picture plus `.json`). |
| `rokwatch/updater.py` | Update check and updater launch. |
| `rokwatch/notify.py` | ntfy, Discord, Telegram. |

### 3.2 `game.py`: acting on the game

**Jump: `goto(x, y)`.** Takes about 1.4–2.0 s on the user's PC (measured in their v1.12
recording).
1. Read the camera from the strip. If it's already there, done.
2. `_quick_jump`:
   - tap the magnifier, found by template
   - wait for the blue button (`_bar_opened`, polls every 60 ms, up to 1.5 s)
   - the X/Y box positions come from `_bar_cache` (fractions of the window). The first time,
     they're read from **the bar's row only**, with the strip reader.
   - `_type_into`: click the box, 0.06 s, Ctrl+A, Backspace, type at 50 ms per key
   - click the blue button
3. `_wait_camera`: strip reads until the camera is within 2 tiles (up to 4 s).
4. `_slow_jump` (full-screen read) is only a fallback when the magnifier can't be found.

**Moving: `pan_to(x, y, axes)`.**
- One drag computed from the known camera (`_cam`, dead-reckoned) and `axes` (tiles per pixel
  at the current zoom). One drag covers up to 60% of the view; at most 2 drags.
- Further away it uses `goto`.
- Below 4 tiles it doesn't move at all.
- `_cam` is set to the expected position. The next look reads the real one.

**Zoom:**
- `_notch(out)`: the wheel at the window centre, then `_settled()`. A notch that did nothing
  while a box is open closes the box and repeats.
- `to_close()` / `to_sweep()`:
  1. `_no_popup()` first.
  2. Blind steps up to just before the 3D ↔ map switch.
  3. Single steps checking `level()` until the switch.
  4. The remaining steps. Going out, these use `_out_keeping_tags` (rule 8).
  5. `to_sweep` also steps back in from "far" (globe showing).
  - `_at` (`sweep` / `close` / None) is stored on the window object. **Set `game._at = None`
    whenever something else may have changed the zoom** (a jump to a troop, recovery). The
    next `to_sweep` then checks.
- `level()`: `screen.view_level` (strip read plus globe template), ~0.13–0.4 s.
- `auto_calibrate()`:
  1. Zoom all the way in.
  2. Step out until the 3D view ends (close = the last 3D step).
  3. Make sure resource points are in view (drag around, up to 6 moves).
  4. Step out until the level tags vanish.
  5. Searching zoom = the last step with ≥3 tags.
  6. Step back in to check the tags return.
  7. Save `{gap, t_out: 1, t_in: gap}`.
- `measure_scale`, `calibrate_close`, `keep_scale`: the manual setup, and the 5-minute check.

**Waiting for a picture:**
- `fresh_frame(max_wait=0.4)`: grab every ~40 ms; the first frame <1% different from the
  previous one wins.
- `_settled(timeout=0.7, min_wait=0.15)`: threshold 1.2%.

**Popups:** `close_popups()`. A box found means a drag of 0.12·H on the far side, then wait
until it's gone (≤0.5 s). No box and the coordinates visible means fine. Otherwise Esc
(something covers the map).

**Your troops (v1.15):**
- `troops()` reads the troop list (right 28% × top 50%, strip reader; the whole screen as
  fallback). It returns `(used, total, [TroopSlot(index, badge, portrait, state)])`. State
  comes from the badge colour and symbol (`screen.badge_state`): gathering, marching,
  returning, fighting, stopped, idle, scout.
- `troop_spot(slot)`:
  1. Tap the portrait: **the game jumps to that army**.
  2. Wait for the camera to land and settle (≤1.2 s).
  3. When a box opened (gathering), read the exact X:Y from it; otherwise use the camera.
  4. `close_popups`, then `_cam = None`.
- `troop_location` / `recall` / `recall_all`: older; used by auto-recall.

**Other:** `look()` reads the whole screen (slow, ~1.5–2 s; **not for per-step use**).
`look_middle()` reads only around the middle (0.42·H). `read_camera(frame)`,
`camera_fast(full)`. `Stopped` is raised by `check_stop()` so Stop works mid-action.

### 3.3 `screen.py`: detection (all sizes relative to the window height H)

| Function | How | Tuned on |
|---|---|---|
| `read_text(frame, region, strip)` | rapidocr; `strip=True` = the strip engine (rule 13) | — |
| `top_texts` / `camera_coords` | Strip 0.4W × 0.08H cut at the magnifier, padded 30 px, ×2 when H<1000; cached per frame | all real screens + the user's trace frames (102 pictures, 0 wrong) |
| `parse_coords` | Regex with the reader's slips (rule 14) | — |
| `view_level` | `close` if power/VIP is read in the strip; `far` if the globe template matches (>0.8); else `patrol` | same 102 pictures |
| `count_level_tags` | Light tags 0.016–0.05H wide with dark digits, HUD excluded. ≥3 = shown, ≤1 = gone | `zoom/` (10 vs 1) |
| `find_map_icons` | Army / scout / hostile diamonds (lavender by day, mauve at night, red = hostile); size rule (rule 19) | real screens |
| `find_march_lines` | Dotted lines: white / green / red; ignores the side panel and chat | real screens |
| `find_gather_badges` | Round red pickaxe badge 0.016–0.06H, fill ≥0.55, round test | `gather/sweep_red_pickaxe.png` + the user's snapshot |
| `find_info_box` | Light box (S<60, V>200), 12–65% W, 18–65% H | `gather/` |
| `danger_icons` | Red eye vs red swords (by the white symbol), bottom right | `icons/`, `scouted_*` |
| `find_magnifier` / `search_go_button` / `find_search_bar` | Template (sizes 0.6–1.6) / blue button / bar layout from the kingdom number to the button | `search_bar_*` |
| `troop_panel` / `badge_state` | "N/M" counter anchor, badge column, colour plus glyph | `recall_panel.png`, `own_city_gathering.png`, … |
| `find_troop_clusters` / `read_army_label` / `army_owner_at_close` | Rule 30 | `army_close/` (the user's army recording) + his older close-up screenshots |
| `name_score` / `_norm` / `find_player` / `label_kind` | Fuzzy name match (tag optional; OCR mix-ups folded: 0→O, 1/L/\|→I, 5→S, 8→B); a city label has a level tag under it, an army label doesn't | real screens |

### 3.4 `hunter.py`: Watch & track him

**`Hunter.round()`:**
1. Zoom to the searching zoom. On the first round, go to his city first. Every 5 min,
   `keep_scale`.
2. **Plan** (`plan`):
   - `sweep`: the first time; when a sweep was cut short; when an army of his got lost away
     from home; or every `full_sweep_minutes` (30).
   - `track`: his known armies (predicted positions) plus his city.
   - `base`: his city only.
3. **Each spot:**
   - `pan_to`, then `look_spot()`: `fresh_frame`, then `detect(frame)` and
     `read_camera(frame)` **in parallel**, plus danger icons.
   - `to_map` turns pixels into tiles with `axes` (`to_tile`).
   - New things: `_check_new_here`, then `_unknown`. Skip his city itself (within 3.5 tiles).
     A march line touching his city counts as his without zooming in.
4. **`identify()`:**
   - Zoom in once and check unknowns nearest first (limit `checks_per_sweep`).
   - `_check_owner(a)`: `pan_to` with the close-zoom axes (`close_ratio` × search axes), or
     `goto`.
     - farm: `_identify_farm` (tap a red badge near the middle, then `_read_farm_box`, then
       `_read_box_contents`), then `_farms_in_frame` for the other visible farms
     - marching army: `army_owner_at_close` twice (rule 30), then the older `look_middle` +
       `find_player` as fallback
   - Then back out.
5. **"Your troops"** (`update_my_troops`):
   - When due (`_troops_due`): every `track_my_gathering_minutes` (10), or every 45 s while
     one of the user's armies is moving.
   - It taps each commander (not scouts or idle), records state and position, sets
     `_at = None`, and zooms back out.
   - Their spots are added to `tracker.my_sites` (alerts use those).
6. `Tracker.match`, events, alerts, activity, the farm reserves re-read (`_refresh_one_farm`,
   ≤1 a round), and the timing line `Round (mode): N spots … in X s - moving / looking /
   checking owners`.

**`Tracker`:**
- **Matching:** each sighting takes the nearest known army of the same kind. Farms within 5
  tiles or by exact xy. Moving armies against `predict()` (along the line at the measured
  speed, or 4 tiles/min), with tolerance `match_distance` (12) plus a growing allowance.
  Others within `match_distance`.
  > **Weak spot:** this is greedy, one sighting at a time. Two or three armies close together
  > could swap identities. See section 7, item 1.
- **Gone** (rule 21). `unsure` = near the screen edge with no line (lines aren't drawn far
  from the view centre: edge zone outside 22–78%).
- **Events:** `army_out`, `army_close`, `hostile`, `pulling_back` (≥2 heading home; farms
  never count), `all_home`, `farm_left` (finished vs pulled back early), farm leave warnings
  at 10 / 5 min.
- `summary()` feeds the panel's "His armies": state, xy (exact for farms), threat, trail (none
  for farms), farm timing, speed and ETA.

### 3.5 `farms.py`: his farms
- `FULL` table (from the user: levels 2–6 for stone, wood, food, gold; gems 10/20/30/40 at
  levels 1–4). Level 3 stone is 354,375. The user's message said 354,475, then they confirmed
  354,375.
- Gathered = full − reserves. Rate from two readings ≥1 min apart. Then minutes left, minutes
  gathering, and (with the learned typical load) minutes until his army is full and leaves.

### 3.6 Panel (`panel.py`, `panel.html`)
- **GET:** `/api/state` (everything the page shows, incl. `armies`, `my_troops`, `trace`),
  `/api/settings`, `/api/phone`, `/api/activity`, `/live.jpg?w=&have=` (204 when unchanged;
  `X-Live` header), `/view.png`, `/snap/<name>`, `/api/snapshots.zip`, `/api/trace.zip`,
  `/api/army_rec.zip`.
- **POST:**
  - commands: `/api/open`, `/api/start`, `/api/hunt`, `/api/stop`, `/api/look`,
    `/api/goto {x,y}`, `/api/axes`, `/api/recall`, `/api/test_alert`, `/api/my_troops`,
    `/api/record_army`
  - zoom: `/api/auto_zoom`, `/api/remember_zoom {kind}`
  - other: `/api/snap {note}`, `/api/army {id, follow, alert_moves, alert_home}`,
    `/api/settings`, `/api/update {action}`, `/api/trace {on}`, `/api/quit`, `/api/bye`
- **Live view:** its own JS loop every 330 ms; 960 px (640 on phones), JPEG quality 70.
- **Phone access:** LAN or Tailscale with a secret key (`?key=`); a QR code in "Use this panel
  on your phone".
- The page polls `/api/state` every 1.5 s. Closing the page quits the program after a grace
  period (`should_quit`).

### 3.7 Settings (`config.yaml` + `setup.yaml`)
- `game_window`, `game_window_zoom_key`
- `notify` (`ntfy_topic`, `ntfy_server`, Discord, Telegram, `cooldown_seconds`)
- `hunt`:
  - `player_name`, `city`, `radius`
  - `zoom` (zoom_cfg)
  - `full_sweep_minutes`, `checks_per_sweep`, `match_distance`, `near_city`, `near_site`,
    `moving_close`
  - `track_my_gathering_minutes` (10; also drives "Your troops"), `pause_between_sweeps` (0)
- `patrol`: `axes` (tiles per pixel at the searching zoom, learned by the zoom setup),
  `auto_recall`, `recall_scope`, `sites`
- `snapshots.*`, `updates.check_minutes`, `close_with_page`, `panel_on_phone`, `targets`
  (saved players)

**Data files next to the program:** `his_activity.csv`, `his_watch.csv`, `his_armies.csv`,
`farm_capacity.json`, `rokwatch.log`, `snapshots/`, `debug_trace/`, `army_rec/`.

---

## 4. Evidence: how to see what the bot sees

| Tool | Where | Gives |
|---|---|---|
| **Debug recording** | More > Snapshots > Start/Stop debug recording; *Download the last recording* | `debug_trace/<time>/frames/*.jpg` (960 px wide, ≤5/s) and `trace.jsonl`. Every action with its duration (`action.ms`), every capture time (`frame.capture_ms`), every look (camera, seconds, detections in px), `notch`, `zoom`, `goto`/`goto_done` seconds, `check_owner`/`owner` with the text read, `round`, all log lines. |
| **Army recorder** | More > Snapshots > Record a marching army | `army_rec/<time>/`: **full-size** frames through every zoom step in and out (twice), `events.jsonl` with the detections per frame and the close-up text, `video.mp4`. |
| **Snapshots** | Save snapshot (plus automatic samples and alert pictures) | Picture plus `.json` of what was read. |
| **Round timing** | Panel log | Where each round's time goes. |

**Notes:**
- The debug recording's frames are **downscaled to 960 px**. The detectors are tuned for full
  size, so don't re-run detectors on those frames and trust the result. Use the army recorder
  or snapshots for full-size pictures.
- Large zips may arrive split in two (the second part holds the remaining frames).
- On the PC you can also grab frames directly:
  `rokwatch.desktop.DesktopWindow("Rise of Kingdoms")` (used as a context manager), then
  `rokwatch.commands.make_game(window, cfg)` gives a `Game`. Then `game.frame()` and
  `cv2.imwrite`.

**Workflow:**
1. Reproduce the problem with a recording.
2. Find the first wrong step in `trace.jsonl` next to its frame.
3. Copy that full-size frame into `tests/real_screens/<topic>/` and write a test asserting the
   right reading.
4. Fix, run the tests, publish.

---

## 5. Measured speed (user's PC, v1.12 recording)

- Track round: **3.7–5.5 s** for 4 spots (moving ~1.5–2.5 s, looking ~2 s).
- A look: 0.4–0.9 s. A drag: 0.39 s. A jump (`goto`): 1.4–2.1 s.
- A round with an owner check: ~19 s (zoom in, check, zoom out). That's the next thing to cut.
- The user still felt gaps were long before v1.11. Since v1.11/1.12 there is no new complaint
  about gaps.

---

## 6. History: every problem so far (cause → fix)

**Coordinate search**
- Tapped the star: the click was at a fixed offset from the text. → Magnifier template, then
  check the bar opened.
- Slow jumps: full-screen reads while waiting (2–3 s), pyautogui's 50 ms pause, long sleeps.
  → Strip reads, 10 ms pause, short polls. (v1.11) The read before the magnifier tap and the
  read of the bar were full-screen (1.5 s + 2.6 s). → Strip and bar row only.
- Dropped digits at 15 ms/key. → 50 ms.

**Popups and the mouse:** rules 2–4. Esc opened the profile. A short closing drag opened his
city's menu. Resting the mouse opened name cards. A blind tap hit his city.

**Zoom**
- Counting from fully out; waiting for a still picture; one misread making a garbage scale
  (rules 7, 10, 11).
- (v1.11) A box left open blocked the wheel: 10 notches out. Root cause: a crash on
  `ArmySeen.info` while reading the box. → Rule 9, and the field was added.
- (v1.11) The searching zoom ended too far out (numbers gone). → Rule 8.
- (v1.11) Every zoom check was a ~1 s read. → Rule 13.

**Detection**
- Farms are badges, not diamonds; the owner is only in the box.
- Spanish UI.
- Hostile diamonds read as badges; phantom side-panel marches; plain army-size diamonds;
  lines not drawn near the edge.
- (v1.14) Close-view armies weren't recognised (rule 30). Found in the user's army
  recording: one notch in from their searching zoom is already the 3D view (their gap is 1),
  where the army is red troops with a tiny name.

**Tracking logic:** rules 20–29. Farms copying a neighbour's owner. Endless re-checks of the
same farm. The sweep restarting. Ignoring new things after a check. False pullback alerts
(both the off-screen kind and, v1.14, the farm wobble kind). Hidden unconfirmed armies.
Double counting one moving army (`same_spot`, 8 tiles). One bad round stopping the watch
(now it recovers). Wandering at the start. The search order hopping across his city.
Coverage gaps under the HUD. "Moving close" depending on the camera.

**Speed:** the 5 s idle pause; full-screen reads per troop; the v1.11 read speedups; v1.12
waits trimmed (box close waits until gone, 0.06 s before typing, 0.2 s name re-read).

**Panel:**
- Phone order reversed.
- The live view was 1 picture per 1.5 s at 720 px / quality 55. Now ~3/s at 960 px / q70.

**Program and updates:**
- The PowerShell updater was blocked. The GitHub API rate limit. SSL CERTIFICATE_VERIFY_FAILED
  on Windows (certifi).
- No console window; single instance.

**Tests:** `test_patrol.py::test_tells_leaving_from_returning` was flaky. The army icon sits at
the end of the drawn line (the line is cut where the icon covers it). → `_army_along`
measures the icon's distance from the site and accepts icons near the line end.

---

## 7. Open work, most important first

1. **Several armies at once (asked by the user; awaiting their go-ahead).** The user's worry:
   when two or three armies march close together, the bot must keep them apart, check each
   one's owner quickly, and never transfer one army's owner to another. Plan, agreed in
   principle:
   - **Global matching** in `Tracker.match`: pair all sightings with all known armies at once
     (Hungarian / min-cost on distance to the predicted position, plus the march line's
     heading). Not greedy nearest-first.
   - **One zoom-in, every owner:** at the close view, read the label above **every** troop
     cluster (`find_troop_clusters` returns all of them). Assign each owner to the cluster it
     sits over, and map clusters back to tiles with the close-zoom axes.
   - Check several new armies nearest-first with short drags, without zooming out between
     them.
   - An owner sticks to the army it was read on. A new army leaving his base starts
     unconfirmed.
   - Tests: the user's army recording has 2–4 armies marching together. Fixtures in
     `tests/real_screens/army_close/`: `rec_0049.jpg` / `rec_0050.jpg` (searching zoom, four
     armies), `rec_0005.jpg` (two), close-view frames `rec_0015`–`rec_0081`, and
     `events.jsonl` (what the recorder detected on every frame of that recording, including
     the ones not kept).
2. **Owner checks are slow** (~19 s for a round with one). Ideas: stay zoomed in to check
   several; skip the zoom-out when the next unknown is close.
3. **Verify "Your troops" in the real game.** Does tapping a commander always jump within
   1.2 s? Does it change the zoom? Marching troops (the fake game has only gathering ones).
4. **Verify close-view owner reading on his armies live** (only tested on recordings and old
   screenshots).
5. **Farm checks live:** does tapping just under the badge always open the box? Are all farms
   in the close view tapped? Is the right farm matched to the right badge?
6. **Typing at 50 ms/key:** check that typed digits match in traces. Lower it only if it's
   solid.
7. **Not built:** sending a scout; sharing a location in alliance chat (needs screenshots of
   the flows).

---

## 8. Tests

**Fast (no browser, ~8 min):**
```
python -m pytest tests/test_real_screens.py tests/test_gatherers.py tests/test_zoom_measure.py tests/test_updater.py tests/test_trace.py tests/test_desktop.py -q
```

**Fake game** (Playwright Chromium; ~5–8 min each):
- `tests/test_hunter.py`
- `tests/test_patrol.py`
- `tests/test_panel.py`
- `tests/test_live_view.py`
- `tests/test_armyrec.py`
- `tests/test_phone.py`
- `tests/test_snapshots.py`

**Notes:**
- Run the slow suites **one at a time**. Running two at once loaded the machine and made a
  timing test fail (it passed alone).
- `tests/fake_game/rok.html` is the fake game: zoom 0 close / 1 patrol / 2 far, marches
  (`sim.march(from, to, color, progress, speed, owner, kind)`), farmers, a troop list with
  boxes, the search bar, the star, and dragging outside a box closes it (like the real game).
- `tests/test_zoom_measure.py` has `ZoomSim` / `TagSim` / `BoxSim`: zoom levels built from real
  screenshots. That's where zoom logic is tested.
- **Real-screen fixtures:** `tests/real_screens/` plus `crops/`, `icons/`, `gather/`, `zoom/`,
  `army_close/`. Every fix to a detector needs a fixture plus a test here.
- **Before claiming a fix:** make the new test fail on the old code, then pass on the new.
  It was done this way for the farm wobble and the patrol flake.

---

## 9. Build and publish

- `.github/workflows/windows-exe.yml` builds `dist/RoKWatcher/` with PyInstaller
  (`build_exe.py`, which also builds the updater; certifi data is collected).
- **On every push it only builds and tests:**
  - the exe self-test
  - the panel driving the fake game in Edge
  - window mode
  - the update check
  - a real update via `--test-update`
- **To publish:** Actions → windows-exe → Run workflow on `main` with `publish=true` and
  `version=1.N` (the next number; the latest is **v1.15**). `start_over=true` deletes all
  releases and starts at 1.0 (only if the user asks). It publishes `v1.N` plus a `latest`
  prerelease.
- **Steps that worked:**
  1. Push.
  2. Cancel the queued push run. The concurrency group runs one build at a time, so it would
     delay the publish.
  3. Dispatch the publish run.
  4. Wait until `https://github.com/elvira-alec/ROK-bot/releases/tag/v1.N` returns 200 and
     `releases/latest` redirects to it.
- From the PC you can use `gh workflow run windows-exe.yml -f publish=true -f version=1.16`
  (needs `gh auth login`).
- The installed app's updater picks the new release up by itself (check interval in the
  panel), or via More > Updates > Check now.
- **From the user's PC, fully automatic - NO popups (the user insists).** Git Credential
  Manager has two GitHub accounts stored (IronAR, elvira-alec) and asked which one every time.
  The repo's own git config pins `credential.https://github.com.username = elvira-alec`, and
  everything runs with `GCM_INTERACTIVE=never GIT_TERMINAL_PROMPT=0` (fails quietly rather than
  prompting). The stored token has `repo` + `workflow` scopes. Steps:
  1. `GCM_INTERACTIVE=never GIT_TERMINAL_PROMPT=0 git push origin main`
  2. `python tools/gh.py publish 1.N` (cancels the plain push builds, starts the publish)
  3. `python tools/gh.py wait-release 1.N` ; `python tools/gh.py runs` / `log <run id>` to check
  Don't cancel a run the user started (the permission check blocks it anyway).
- **Watch the `tests` workflow too** (Linux, Python 3.12, NumPy 2): this PC runs Python 3.10 /
  NumPy 1.26, so NumPy-2-only breakage doesn't show locally. v1.16 shipped one (`np.cross` on
  2-D vectors raises in NumPy 2) - fixed in v1.17. `python tools/gh.py log <run id>` shows it.
- **Publish every change unless the user says not to.** When they say "push, don't build",
  just push.

---

## 10. What the user asked for last (pick up here)

- They asked whether the handoff is detailed enough to not undo work. This file is the
  answer.
- **Pending decision:** build the several-armies handling (section 7, item 1)? The user
  described the need ("several armies marching, snap to one, snap to the other quickly,
  identify whose they are; what happens when two or three are very close together?") and
  asked whether Claude on their PC could control the game directly. Yes: run the bot from
  source and drive it, take frames and tune the detectors live.
- They've just been given "Your troops" (v1.15). Watch for their feedback on it.

### 2026-11-13: live run from the user's PC (v1.15) - confirmed faults + fix plan

Claude drove the installed app (`C:\Alec\ROK BOT\RoKWatcher`) over the local panel API
(`127.0.0.1:8765`, local requests need no key), turned on the debug trace, ran the hunt,
and read `debug_trace/<run>/trace.jsonl` + frames. The user watched live and corroborated.
Two good traces: `20261113-173258` (track rounds) and `20261113-194408` (sweep + track).
Mechanisms are confirmed with timestamps and frames; the user's three complaints are all real.

**Confirmed faults (most important first):**

1. **Goes past moving armies (A).** The sweep is a blind drag grid; an `army`+march-line
   seen mid-pan is not pursued. Trace 194408 t=41.1 `look found=('army','line')` -> t=41.4
   drag -> t=42.3 gone (frame 000103 shows the army + dotted line on the right). User: "it
   just went past an army."
2. **Can't identify a moving / hostile army (B).** By the time it zooms in (~1.5 s) the army
   has moved out from under the stale tile->tap projection; the close read is empty ->
   "unclear". Trace 194408 t=55.9 hostile -> t=59.1 `owner null, read=[]` (frame 000136 is an
   empty watchtower where it tapped). This is the biggest driver of persistent `owner=None`.
3. **Zoom in / out / back in on the SAME point (C).** Two sources, both confirmed:
   - `_unknown` matched farms within **2.5** tiles while `Tracker.match` uses **5.0** + the
     farm's exact `info["xy"]`. A farm whose zoomed-out estimate drifts 2.5-5 tiles looked
     "new" and was re-checked. **FIXED** 2026-11-13: `_unknown` now mirrors `match`
     (`tests/test_dedup.py`; fails on the old gate, passes on the new).
   - `_check_new_here` re-looks and re-identifies (`again=2`); a farm whose box read returns
     `None` stays unknown and is tapped again (up to `checks<3`). Trace 173258 t=51.8->66:
     two full close cycles (~15 s) on one neighbour farm; first read null (OCR grabbed his
     top-of-screen label + the box -> conflicting coords), second landed "other". STILL OPEN.
4. **Poor lock-on (D).** 2-3 armies stay `owner=None` every round; `missed` flaps round to
   round (194408 R2-R6). Mostly a consequence of B.

**New constraint from the user (2026-11-13):** at the close zoom the bot must NOT confuse
his army with the **NPC armies** around the area - some have names and look like troop
clusters, "but they're usually not red". `find_troop_clusters` already requires red (good),
but the `_check_owner` fallback (hunter.py ~797-814) reads ANY floating name near the middle
via `find_player` / `_other_army_label_near`, not gated on a red cluster - an NPC name can
leak in there. Gate that fallback on a red cluster near the middle before concluding an owner.

**Fix plan (remaining), with code locations:**

- **B/D - lock onto a moving army before reading it.** In `identify` / `_check_owner`
  (hunter.py 722-818): for a moving army, don't tap a stale projected tile. After
  `zoom_close`, re-detect the red troop cluster nearest the predicted spot in the close frame
  and read the label over *that* (use `screen.find_troop_clusters` + `read_army_label`), or
  read the owner at the sweep zoom from the march line's home end without zooming at all.
  Re-centre on `predict(a, now)` right before the close read.
- **A - don't pan past armies.** In `sweep` / `_check_new_here` (1048-1105, 1200-1220): when a
  look finds an `army`+line whose ends are NOT at his city, investigate it before moving to
  the next grid point (it's the thing that matters most). Record every army-on-a-line even
  when skipping the zoom.
- **C (second source) - stop re-tapping a farm whose box didn't read.** In `_check_new_here`:
  remember tiles already attempted this sweep-point and don't re-queue them in the `again`
  re-look; and fix the null-read cause (restrict the farm box read to the selected info box,
  not his name label bleeding in from elsewhere on screen - see `_read_box_contents` /
  `read_gatherer`).
- **NPC gate** - as above, gate the `_check_owner` label fallback on a red cluster.

**To validate the remaining fixes live** you must run from SOURCE (the installed exe can't be
hot-patched): stop `RoKWatcher.exe` (frees port 8765 and the mouse), copy the user's
`config.yaml` + `setup.yaml` + `calibration.png` from `C:\Alec\ROK BOT\RoKWatcher` into the
clone, then `python -m rokwatch --panel` and drive it the same way. Each live cycle: trace on,
hunt ~170 s, stop, read the new `debug_trace`. Needs fresh full-res fixtures for the
moving-army and NPC cases (use the army recorder / snapshots, not the downscaled trace frames).

### 2026-11-14: fixes found with the debug recordings (live on the user's PC)

Method: run the source panel (`python -m rokwatch --panel` in the clone, user's setup.yaml
copied in), drive it over `127.0.0.1:8765` (`/api/trace`, `/api/hunt`, `/api/auto_zoom`,
`/api/stop`), then `python tools/analyze_trace.py debug_trace/<run>` and look at the frames.
`tools/capture_sweep.py OUTDIR` saves full-size searching-zoom pictures (debug frames are
downscaled to 960 px wide, too small for the plate test). Also mine `his_armies.csv` /
`his_activity.csv` (split by session: army ids restart at 1 every hunt).

Fixed (each with a test that fails on the old code):

1. **"His troops moved" when they hadn't (false alerts)** - `tests/test_false_moves.py`. Three
   causes in `Tracker`, all seen in the user's logs:
   - greedy pairing swapped the ids of two farms side by side (175,366 / 180,365): `match()`
     now pairs all sightings and armies at once, closest pairs first;
   - a farm's position is the median of its recent sightings (or its exact X:Y), not the latest
     wobbly estimate; two tracked farms with the same exact X:Y are merged; his farm isn't
     "gone" (= "He stopped gathering") while an unchecked farm sits within 7 tiles of it;
   - an army on someone else's march line was "marching" for minutes (179,360): moving only
     counts if it actually moves - on a line 60 s+ without leaving 1.6 tiles = not marching
     (`_line_but_still`; one frame can't tell: armies sit anywhere along their own line, so
     "army at the line's end" is NOT a valid rule - checked on the fixtures);
   - direction flipped going home <-> marching out every round near his city (0.5-tile rule vs
     +-2 tile wobble), logging false "recalled"/"sent out" (= "he's online"): now decided by the
     trend over its sightings (>= 2.5 tiles) and kept until clear evidence the other way.
2. **Map scale (patrol.axes) was 20-150% wrong** - root cause of farm "wobble", close-zoom taps
   on empty ground and sweep gaps. `learn_axes` dragged 300 px (camera moved 1-4 whole tiles):
   now half the screen, going and coming back, twice. And `_do_auto_zoom` learned the axes at
   the calibration's zoom, then the tag check moved the searching zoom a notch in (gap 2 -> 1)
   - the saved scale belonged to another zoom. Now: settle the zoom first (`reanchor`), then
   measure there. Verified live: new axes are within ~8% of a least-squares fit over 82 real
   pans (`analyze_trace.py` prints that fit; ratio should be ~1.00).
3. **Zoomed too far out**: `keep_scale` (every 5 min) nudged a notch on a drag measurement,
   never checking level numbers; and a hunt accepted whatever simplified-map zoom the game was
   left at. Now `Game.reanchor()` (in to the 3D edge, out by `gap`) at hunt start and when the
   scale looks off. `_out_keeping_tags` counts tags on a settled frame (they hadn't drawn yet
   = "numbers vanished") and flags `gap_changed` so the hunter rescales its axes.
4. **Calibration**: retries once; close_ratio needs two agreeing readings and 0.25-0.95 (else
   the old close scale is kept, as an absolute value).
5. **Fake armies (NPCs) at the searching zoom**: at night white resource icons passed as army
   diamonds, red barbarian icons as hostile armies - 8 of 9 "army" checks were nothing.
   Resource points, cities and barbarians have a level plate right under the icon (dy
   0.020-0.026 H, dx +-0.010 H); armies/scouts never do. `screen.has_level_tag_below`, applied
   in `find_map_icons`. `tests/test_npc_filter.py` (live frame `real_screens/night/spot00`).
   Keep the window tight: an army passing just above a resource point has its plate ~0.04 H
   below and must still count (`test_scouts_and_armies_never_swapped` caught a wider window).
6. **NPCs / cities at the close zoom**: red barbarian troops and red-roofed cities came out as
   "troop clusters". `screen.cluster_is_npc` (level plate under the cluster) and
   `player_troop_clusters`, used by `army_owner_at_close`. On all 19 close fixtures his armies
   are always kept; everything dropped is a city, a city's red carpet or a barbarian.
7. **Moving army not in the middle when read**: `Hunter._centre_on_troops` drags the player
   cluster nearest the army's predicted spot to the middle before reading (logged as
   `close_read` in the recording).
8. **Same resource point checked 2-3x in one sweep** (4 s each): the "already known" check only
   looked at the tracker, which is empty until the round ends. Now also what was checked earlier
   this round (`_round_seen`), and a spot that failed twice this round isn't retried.
   Earlier: `_unknown`'s farm match aligned with `match()` (5 tiles + exact X:Y), `test_dedup.py`.
9. **Close-zoom farm taps missed** ("couldn't open the resource point"): `pan_to`'s 4-tile slack
   is ~600 px at the close zoom - now 1 tile there; the badge nearest the farm's projected spot
   is tapped; a box whose X:Y is >6 tiles from the farm meant is a neighbour's (not recorded).
   Live: farm reads went from 2/10 to 11/12.
10. **Camera misread** ('174' read as '17') sent it to check a "farm" at 19,358: a reading >10
    tiles from where the last drag/jump left it is re-read twice; the position two readings
    agree on wins, else that look is skipped.
11. `requirements.txt`: `onnxruntime==1.29.0` only installs on Python 3.11+ (the CI builds on
    3.12 - the shipped exe keeps 1.29.0); a marker adds 1.19.2 for Python 3.10 source installs.
12. **Looks taken before the icons were drawn = false "He stopped gathering"** (the user saw
    it bounce between his city and one farm, ignoring the other: that farm had been dropped).
    Right after a jump/drag the ground draws first, icons a moment later. `look_spot` waits up
    to 2 s for `screen.icons_drawn` (>= 3 level marks; works at night via `level_marks`, which
    finds a plate merged with the icon above it - NOT used for the zoom check or the NPC test,
    it over-counts bare icons). A look without icons drawn covers nothing (`Hunter._cover`).
    Two misses of something that stays put must be >= 5 s apart (`Army.missed_at`).
13. **Farms revisited every 30 s** in track mode (`hunt.farm_look_seconds`), not every round:
    a typed jump there and back every 2.7 s for something that can't move.
14. **Night false armies**: `find_map_icons`' second pass ("dark ring, light inside") took a
    white resource icon for an army at night; an all-white interior is now rejected (no real
    army in the fixtures comes through that pass). `tests/real_screens/night2/`.
15. **Lost connection**: 'NETWORK DISCONNECTED - click CONFIRM' (laptop on a phone hotspot,
    happened 3x). `screen.modal_dialog` (blue panel + dimmed map, no OCR) and
    `find_reconnect_button` (OCR must mention the network - never presses another CONFIRM);
    `Game.recover_connection` presses it and waits; checked on every look (the dimmed
    coordinates still read, so it used to "look" at nothing for minutes), in `close_popups`
    and each round; then zoom re-anchored + back to his city; urgent alert after 2 min.
    Verified live: reconnected in 4 s. `tests/test_reconnect.py`.
16. **Game window gone** (crash or closed): the hunt loop alerts "THE GAME HAS CLOSED" after a
    minute of failed rounds, retries every 5 s, and says "Watching again" when it's back.
17. **Map scale learned while hunting** (`Hunter._learn_scale`): the zoom doesn't land on the
    same scale every time (+-10-15% between runs). `pan_to` notes its drags since the last
    real camera reading, `look_spot` pairs them with the next reading; every 8 moves a robust
    fit, sanity-checked (0.75-1.33x of the set-up), moved half way. Close-zoom scale stays
    tied to the set-up (`_axes_cal`). Live: settled at 1.02-1.05x / 1.08-1.13x.
18. Someone else's farm needs two misses to drop too (it was re-checked 4x in one run).
19. **v1.18 - his farms hidden as someone else's again (user: "missing 2 of his gathering
    spots").** I had widened `_unknown`'s farm match to 5 tiles; his croplands at 179,386 /
    180,390 took the owner of [SRTR]TAAK's farm at 180,382 without a check. Back to 2.5 tiles
    from the exact X:Y (the old rule!). `Tracker.match` pairs a farm with a known exact X:Y only
    within 3 tiles (resource points here are 3.2+ apart); a drifted estimate gets checked and
    is merged back by its exact X:Y (merge now runs last and rescues a "gone" twin - that was a
    false "He stopped gathering"). Someone else's farm near his city is re-checked every 10 min
    (`hunt.recheck_other_minutes`). `tests/test_dedup.py`.
20. **The "rhombuses" on the panel map**: the white resource icon right under each of his
    gathering badges came out as an "army" at night (20 empty close-ups in one run). An
    army/scout directly under a gathering badge is dropped in `Hunter.detect`.
21. **Someone else's army walking past his city read as his** (false "HIS ARMY IS MOVING CLOSE
    TO YOU"): with no troop cluster found, the label fallback read his CITY's name partly
    ('NHWICK') as an army label. Names at his city's spot on screen are ignored
    (`_his_city_on_screen`, also passed to `army_owner_at_close`).
22. Calibration and the scale check read the camera only once the map stopped gliding
    (`Game.camera_settled`; read too soon, the scale came out ~25% low), and the hunt measures
    the scale before its first sweep (`_first_scale`) - the sweep decides whose farm is whose.
23. **v1.19 - "checks the same resource point twice and ignores the lower one"** (user, live):
    - the sweep's `same_spot` merged two farms 4 tiles apart into one (the lower one never
      checked): farms now merge only within 2 tiles;
    - at the close zoom it could tap the neighbouring farm's badge (nearest to an estimate a
      couple of tiles off): a box whose exact X:Y is a farm already read = wrong badge, tap the
      next one (`_known_farm_xys`); no other badge = it IS that farm (`_same_as_known`);
    - every farm's exact X:Y is stored now (only his were): `_farm_memory` (X:Y -> owner) means
      a farm that dropped out and came back isn't zoomed in on again (his 30 min, others' until
      their 10-min re-check), also applied to unknown farms just before the end-of-round checks.
24. **More NPC / fake armies**, each measured on live frames before the rule was added:
    - red barbarian axes / forts came out as "army": red share 0.49-0.65 vs 0.00 for every real
      army / scout -> `screen._red_share` < 0.25 for army/scout;
    - night: a stone-deposit icon's plate touches the icon; `level_marks` (night-capable)
      plate at dy 0.016-0.034 H / dx +-0.010 H under it = resource icon (real armies' only mark:
      their symbol's bottom at 0.010-0.013 H);
    - momentary false sightings while icons fade in: a stationary army/scout must be on a
      second picture 0.25 s later (`look_spot`); an empty close-up (no troops, no name) = not
      an army, no more checks.
25. The 5-min zoom check compared a drag measurement with the pan-fitted scale (they differ
    ~10-15%): it now compares with a drag measurement taken right after the zoom was set
    (`_measure_ref`).
26. Live testing: `tools/live_round.sh SECONDS` (traced hunt on the running panel + analysis,
    incl. FARM SPOTS: every badge seen vs checks). Rounds 4-6 on 2026-11-14 morning: every
    farm checked once, 0 fake armies, 0 false moves.

Live result after 12-18 (7 min, his 4 farms out): every farm tracked the whole run (~250
sightings each), 0 false alerts, 7-8 owner checks all answered first time, sweep ~40-80 s,
track rounds ~0.8 s. Dev tools: `tools/analyze_trace.py`, `tools/capture_sweep.py`.

Installed app note: its setup.yaml has `close_with_page: true`, so it quits a few minutes
after no panel page is open - it was NOT watching overnight. Worth telling the user.
