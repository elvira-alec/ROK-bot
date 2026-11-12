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
    w = window()
    w.click(10, 20)
    assert w._pg.calls == [("click", (110, 70))]
    w.set_area([5, 5, 100, 100])
    w.click(10, 20)
    assert w._pg.calls[-1] == ("click", (115, 75))


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
