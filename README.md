# ROK-bot: attack watcher for Rise of Kingdoms

Keeps the browser version of Rise of Kingdoms open on a PC and sends your phone a notification when:

1. **You're under attack.** It spots the red flashing screen edges or the warning icon.
2. **The bully sends troops out.** You park the map on his city. If a march line leaves his city pointing at you, you get an urgent alert. If it points somewhere else, you get a normal alert. This works even when the game itself hasn't warned you yet.
3. **The watcher loses the game.** If the picture stops changing (you got disconnected or logged out), it tells you.

Alerts come with a screenshot so you can see what's happening.

> **Alerts only.** It doesn't touch the game. It never clicks or recalls troops, so it's far less likely to get you banned than a bot. Any third-party tool is still against Lilith's rules, so use it at your own risk.

## What you need

- A Windows, Mac or Linux PC that can stay on while you're away
- Python 3.10 or newer ([python.org](https://www.python.org/downloads/); on Windows, tick **"Add Python to PATH"**)
- The free **ntfy** app on your phone ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) / [iPhone](https://apps.apple.com/app/ntfy/id1625396347))

## Setup (one time)

Open a terminal (Windows: search for **cmd**) in this folder and run:

```
pip install -r requirements.txt
python -m playwright install chromium
copy config.example.yaml config.yaml      (Mac/Linux: cp config.example.yaml config.yaml)
```

Open `config.yaml` in Notepad and change:

- `game_url`: the page where you play RoK in the browser.
- `ntfy_topic`: a long, random name, e.g. `rok-alerts-jx83kd92`. In the ntfy app, tap **+** and subscribe to the same name.

Check that alerts reach your phone:

```
python -m rokwatch --test-notify
```

## Running it

```
python -m rokwatch
```

The first time, a browser window opens:

1. Log in and open the game.
2. Go to the **map** and move it so you can see **his city** and **your city** (or where you gather). Zoom out enough that march lines show up.
3. Press Enter in the terminal.
4. A screenshot window pops up. Drag a box around the game picture and press Enter.
5. Click the center of **his city**, then click **your city**.

Then it starts watching. **Don't move the map or resize the window after this**, or the positions will be off.

Next time, run `python -m rokwatch`, put the map back where it was (it saved `calibration.png` to remind you), and press Enter. If he moves his city, run `python -m rokwatch --calibrate`.

## Tuning

Every alert saves a marked-up screenshot in the `debug/` folder. His city is circled red, yours green, and detected marches are drawn as yellow arrows.

- **Too many false "under attack" alerts:** raise `attack_alert.red_fraction` (e.g. `0.45`).
- **Missed a real attack:** take a screenshot during an attack and crop just the warning icon. Save it as a `.png` in `templates/attack/`. The watcher will then look for that exact icon.
- **Missed his march:** lower `enemy_watch.min_line_length`, or raise `start_radius`.
- **Alerts on marches that weren't aimed at you:** lower `angle_tolerance_degrees`.
- **March colors look different in your game:** adjust the `line_colors` ranges.

## Limits

- It only sees what's on screen, so his city has to stay in view on the map.
- It only catches marches that start at his city. Troops he already has out on the map aren't tracked.
- Cloud browser sessions may kick you after a while. The "can't see the game" alert tells you when that happens.

## Running the tests

```
pip install pytest
python -m pytest
```
