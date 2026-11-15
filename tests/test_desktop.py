"""Window mode's translation of clicks / keys / scrolls (real mouse replaced by a recorder)."""

from rokwatch.desktop import DesktopWindow


class Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        return lambda *a, **k: self.calls.append((name, a))


def window(zoom=""):
    w = DesktopWindow("Rise of Kingdoms", zoom)
    w._pg = Recorder()
    w._front = lambda: None
    w.rect = lambda: [100, 50, 1600, 900]      # window's inside at (100, 50) on screen
    return w


def test_click_lands_inside_the_window():
    # a click is: move there, a short pause, press, release (an instant click was ignored
    # by the game, 2026-11-15)
    w = window()
    w.click(10, 20)
    names = [c[0] for c in w._pg.calls]
    assert names[:3] == ["moveTo", "mouseDown", "mouseUp"]
    assert w._pg.calls[1] == ("mouseDown", (110, 70)) and w._pg.calls[2] == ("mouseUp", (110, 70))
    w.set_area([5, 5, 100, 100])
    w.click(10, 20)
    downs = [c for c in w._pg.calls if c[0] == "mouseDown"]
    assert downs[-1] == ("mouseDown", (115, 75))


def test_mouse_parks_outside_the_game_after_actions():
    """After a click or drag the mouse leaves the game window (resting on the map opened
    name cards / menus), never onto a screen corner (that's the stop switch)."""
    w = window()
    w._pg.size = lambda: (1920, 1080)
    w.click(10, 20)
    w.drag(100, 100, 300, 100)
    moves = [c[1] for c in w._pg.calls if c[0] == "moveTo" and len(c[1]) == 2]
    x, y = moves[-1]
    inside = 100 <= x <= 1700 and 50 <= y <= 950
    assert not inside and 10 < x < 1910 and 10 < y < 1070, moves[-1]


def test_keys():
    w = window()
    w.press("Control+A")
    w.press("Escape")
    w.press("Enter")
    w.type(162)
    assert w._pg.calls == [("hotkey", ("ctrl", "a")), ("press", ("esc",)), ("press", ("enter",)),
                           ("typewrite", ("162",))]


def test_wheel_out_and_in_with_bluestacks_ctrl():
    w = window("ctrl")
    w.wheel(800, 450, 300)      # scroll down = zoom out
    names = [c[0] for c in w._pg.calls]
    assert names[0] == "moveTo" and names[1] == "keyDown" and names[-1] == "keyUp"
    scrolls = [c[1][0] for c in w._pg.calls if c[0] == "scroll"]
    assert scrolls == [-120, -120, -120]
    w2 = window()
    w2.wheel(800, 450, -200)    # zoom in
    assert [c[1][0] for c in w2._pg.calls if c[0] == "scroll"] == [120, 120]
