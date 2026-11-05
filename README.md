# ROK-bot: attack watcher for Rise of Kingdoms

Keeps the browser version of Rise of Kingdoms open on a PC, watches it, and sends your phone a notification. **Simple mode** watches one map view; **patrol mode** (below) moves around the map for you. Simple mode alerts when:

| Alert | When |
|---|---|
| 🚨 **YOU ARE UNDER ATTACK** | The screen edges flash red (or a warning icon you saved shows up) |
| 🚨 **ENEMY MARCH HEADING YOUR WAY** | A march line leaves **his** city pointing at your city or one of your gathering spots. Comes before the game warns you, and says which spot |
| 👀 **Enemy sent a march** | He sent troops out, but not toward you |
| ⚠️ **Lost the map / can't see the game** | A menu is covering the map, the map moved away, the stream froze, or you got logged out |

Every alert comes with a screenshot, with arrows drawn on the marches it saw.

> **Alerts only.** It never clicks in the game or moves troops, so it's much less likely to get you banned than a bot. Any third-party tool is still against Lilith's rules, so use it at your own risk.

## Quick start (Windows, no installing)

1. On GitHub, open **Releases → "RoKWatcher for Windows (latest)"** and download **`RoKWatcher-windows.zip`**.
2. Unzip it anywhere, open the folder, and double-click **`RoKWatcher.exe`**. A black window opens (leave it open), then the **control panel** opens in your browser.
3. In the panel:
   1. **Alerts and game page:** paste the page where you play RoK in the browser and your ntfy topic. Press **Save**, then **Send test alert**. Your phone should buzz.
   2. **Places to watch:** add his city as **Enemy** (e.g. 162, 374), and your city and gathering spots as **Mine**. Press **Save**.
   3. **Run it:** press **Open game window**. Log in, go to the map, and close any menus. Then press **Start patrol**.
4. Leave the game window and the black window open. Alerts go to your phone, and the panel shows them too.

Before the first real patrol, use **"Check it works on your screen"** in the panel:
- **What do you see?** It should show your camera coordinates, your troops (gathering / marching) and any march lines.
- **Jump there** with his coordinates. The map should move to his city.
- **Learn map directions** (once). This lets it tell whether a march from him is aimed at your spots.

It needs Windows 10/11 with **Google Chrome or Microsoft Edge** (Edge comes with Windows). Nothing else to install. It's a large download (about 300 MB zipped) because it includes the text-reading engine.

Windows may warn about an unknown app the first time. Click **More info → Run anyway**: the exe isn't code-signed. It's built automatically from this repo's code on GitHub, and the build log is under **Actions → windows-exe**.

## Patrol mode (the full bot)

`run.bat --patrol` makes the bot move around the map for you, like a player checking in:

1. It jumps to each site in `patrol.sites` using the game's coordinate search: his city, your city, your gathering spots.
2. At each site it reads the screen and looks for **dotted march lines** that start or end there.
3. **At his city:** it logs every march to `activity.csv`. If it knows the map directions (see `--learn-axes` below), it works out whether the march points at one of your spots. If so, you get an urgent alert and, with `auto_recall: true`, the bot recalls the troops there.
4. **At your spots:** a march line ending there that isn't green (green is yours or your alliance's) triggers an urgent alert, plus a recall if that's turned on.
5. It also reads your troop list each time and alerts you if a troop stops gathering. Attack coordinates posted in alliance chat get logged.

`run.bat --report` shows **when he's active**: his march sightings by hour of day.

Try each piece on your own screen first:

| Command | What it does |
|---|---|
| `run.bat --look` | Prints what the bot sees: camera coordinates, your troops, march lines, all text |
| `run.bat --goto 162 374` | Jumps the map to those coordinates |
| `run.bat --learn-axes` | Drags the map a little to learn which way X/Y run on screen, so it can tell where a march is heading |
| `run.bat --recall-now` | Recalls all gathering troops |

### What's checked on real game screens and what isn't yet

Checked against real screenshots (`tests/real_screens/`, day and night):
- Reads the camera coordinates and your troop list. Tells **gathering** (pickaxe), **marching** (footprints) and scout badges apart.
- Finds the RECALL button, the coordinates in a troop's info box, player names, and coordinates posted in chat.
- Finds the **coordinate search bar** and where to tap: the X box, the Y box and the search button. The boxes start empty.
- Tells the 3 **zoom levels** apart: close 3D view, the patrol level (simplified map with resource points), and too far out (the globe button appears). The bot zooms to the patrol level.
- Finds white (other player) and green (yours or alliance) dotted march lines, at any screen size and at night. Finds other players' army diamonds. Trees, flowers, icons and red territory borders aren't mistaken for marches or attacks.
- Ignores green lines at his city, since those are you attacking him.

Not seen on a real screen yet. Send a screenshot if it doesn't work:
- Whether **tapping a troop portrait** opens its info box with RECALL. It looks that way from your screenshots.
- **Mouse-wheel zoom** in the browser version.
- How a **hostile march aimed at you** looks (probably red dots, which are supported), and how the **"under attack" warning** looks.

## What you need

- A PC that can stay on while you're away (Windows, Mac or Linux)
- **Python 3.10+** from [python.org](https://www.python.org/downloads/). On Windows, tick **"Add Python to PATH"** during install.
- **Google Chrome** (recommended: it plays the game's video stream best)
- The free **ntfy** app on your phone ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iPhone](https://apps.apple.com/app/ntfy/id1625396347)). Discord and Telegram also work.

## Setup without the exe (Python)

1. Download this repo (green **Code** button → **Download ZIP**) and unzip it.
2. Double-click **`install.bat`**. When it opens `config.yaml`, change:
   - `game_url`: the page where you play RoK in the browser.
   - `ntfy_topic`: a long, random name like `rok-alerts-jx83kd92`. In the ntfy app, tap **+** and subscribe to the same name.
3. Double-click **`test_alert.bat`**. Your phone should buzz.
4. Double-click **`run.bat`**. A Chrome window opens. The first time:
   1. Log in and open the game.
   2. Go to the **map** and move it so you can see **his city** and **your city / gathering spots**. Zoom out enough that march lines are visible.
   3. Press Enter in the black window.
   4. A screenshot pops up. Drag a box around the game picture and press Enter.
   5. Click the middle of **his city**.
   6. Click **your city**, then any gathering spots you want protected. Press Enter when done (Backspace undoes a click).

It's now watching. Leave the window open on the map. It's fine if it's behind other windows. Avoid minimizing it.

Next time, run `run.bat`, put the map roughly where it was (`calibration.png` shows you), and press Enter. Small map nudges are corrected automatically. If he moves his city, or you want different spots, run `run.bat --calibrate`.

**Mac / Linux:** `pip3 install -r requirements.txt`, `python3 -m playwright install chromium`, `cp config.example.yaml config.yaml`, then `./run.sh`.

### If you can't log in from the watcher's browser

Some sites (and Google sign-in) block browsers controlled by a program. If that happens:

1. Close the watcher.
2. Double-click **`start_chrome.bat`** and log in to the game in that Chrome window.
3. In `config.yaml`, set `connect_to_chrome: "http://localhost:9222"`.
4. Run `run.bat` again. The watcher now uses that Chrome window instead of opening its own.

## Tuning

Every alert saves a marked-up screenshot in `debug/`. His city is circled red, your spots green, and marches are drawn as yellow arrows. Settings are in `config.yaml`, and each one has a comment explaining it.

| Problem | Fix |
|---|---|
| False "under attack" alerts | Raise `attack_alert.red_fraction` (e.g. `0.45`) |
| Missed a real attack | Screenshot an attack, crop just the warning icon, and save it as a `.png` in `templates/attack/` |
| Missed his march | Lower `enemy_watch.min_line_length` or `change_threshold`, or raise `start_radius` |
| Too many "Enemy sent a march" alerts | Set `report_other_marches: false` |
| Alerted for marches not aimed at you | Lower `angle_tolerance_degrees` |
| "Lost the map" while the map is fine | Lower `map_tracking.lost_ratio` (e.g. `0.25`) |

**Record and replay:** `run.bat --record` saves every screenshot to `recordings/`. Afterwards, `python -m rokwatch --check recordings` replays them and prints what it would have alerted on. This makes it easy to tune settings against a real attack.

## Limits

- It only knows what's on screen, so his city has to stay in view on the map.
- It catches marches that **start at his city**. Troops he already has out in the field aren't tracked. A march returning to his city can look like one leaving it.
- Zooming the map changes the picture too much to follow. You'll get a "lost the map" alert. Zoom back, or redo setup.
- Cloud browser sessions can time out. The "can't see the game" alert tells you when that happens.

## How it works

Every few seconds it screenshots the game, then:

- **Attack:** measures how red the screen border is compared with normal. Steady red, like red territory at the edge, is learned as normal. The pulsing warning isn't.
- **Marches:** learns what the map normally looks like, then looks for new straight lines of any color that start at his city. It skips lines that pass straight over his city, which are someone else's march going by. Each line's direction is compared with the direction to each of your spots, and the same march is only reported once.
- **Map nudges:** lines up each screenshot with the setup screenshot (`calibration.png`) and shifts all positions to match.

## Tests

```
pip install pytest
python -m pytest
```

The tests include a fake RoK page (`tests/fake_game/index.html`) with a scrolling map, drifting clouds, other players' marches, a HUD, video-stream noise and the red attack flash. They run the real watcher against it in a headless browser: attacks, marches toward you, marches elsewhere, marches passing by, map nudges, menus, frozen streams, the full command line with alerts sent to a mock phone server, crash recovery, and setup.
