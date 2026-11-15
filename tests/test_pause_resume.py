"""'Make sure the bot doesn't try to override when I connect to the game; it must pause itself
and restart once I hit go again' (user, 2026-11-15). Opening the game on the phone makes the PC
game say 'You are already logged in on another device' and close."""

import types

import cv2
import pytest

from rokwatch import restart
from rokwatch.game import GamePaused
from pathlib import Path

D = Path(__file__).parent / "real_screens"


def test_launcher_start_button_found():
    f = cv2.imread(str(D / "launcher" / "launcher_es.jpg"))
    x, y = restart.find_start_button(f)
    assert abs(x - 1480) < 25 and abs(y - 803) < 20


@pytest.mark.parametrize("p", ["city_view/my_city_home.png", "zoom/close_3d.png",
                               "moved/his_old_spot_empty.png", "zoom/search_with_tags.png"])
def test_no_start_button_on_game_screens(p):
    assert restart.find_start_button(cv2.imread(str(D / p))) is None


def test_round_pauses_when_kicked():
    from rokwatch.hunter import Hunter

    class G:
        kicked = False
        zoom_cfg = {}
        _at = "sweep"

        def recover_connection(self):
            self.kicked = True              # the 'another device' message was seen
            return False

        def check_stop(self):
            pass
    h = Hunter(G(), {"hunt": {"guard_only": True},
                     "patrol": {"axes": [[0.0085, 0], [0, -0.012]]}}, [("my city", (210, 422))], "nul")
    with pytest.raises(GamePaused):
        h.round()


def test_never_starts_the_game_by_itself(monkeypatch):
    # only Watch / Guard / Resume start it - and only for the official PC game window
    from rokwatch.worker import Worker
    w = Worker.__new__(Worker)
    w.status = types.SimpleNamespace(doing="", phase="ready", message="", progress=None)
    w.stop_patrol = types.SimpleNamespace(is_set=lambda: False)
    started = []
    monkeypatch.setattr(restart, "game_window", lambda: None)
    monkeypatch.setattr(restart, "start_game", lambda *a, **k: started.append(1) or True)
    w.gb = None
    assert w._game_running({"game_window": "BlueStacks"}) and not started
    assert w._game_running({"game_url": "http://x"}) and not started
    assert w._game_running({"game_window": "Rise of Kingdoms"}) and started == [1]
