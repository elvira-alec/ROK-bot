"""'It types in the wrong boxes' (user, 2026-11-15): a click on a box that didn't take put Y's
number in the X box. The boxes are read back before searching; only the real mistake makes it
type again - the text reader's own slips ('210' read as '3210', the cursor as a '1', a level
number under the bar) must not."""

import pytest

from rokwatch.game import Game


@pytest.mark.parametrize("got,wrong", [
    ((210, 422), False), ((3210, 422), False), ((23, 422), False), ((None, 4221), False),
    ((210, 16), False), (None, False),
    ((422, 374), True),            # Y typed into the X box
    ((4222, 374), True),
])
def test_boxes_wrong(got, wrong):
    assert Game._boxes_wrong(got, 210, 422) is wrong
