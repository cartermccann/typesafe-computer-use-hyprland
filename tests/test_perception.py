from typesafe_computer_use.models import Item
from typesafe_computer_use.perception import ax_is_grounded, goal_echoes, ground_ax_targets, is_echo, merge_blocks, to_items


def line(text, x1, y1, x2, y2, conf=1.0):
    return (text, conf, (float(x1), float(y1), float(x2), float(y2)))


def test_merges_stacked_lines_across_columns():
    lines = [
        line("Kash Patel defends", 1080, 1531, 1300, 1561),
        line("Two House Democrats", 1400, 1531, 1600, 1561),
        line("removing bestiality as", 1075, 1571, 1300, 1601),
        line("defect again on key vote", 1400, 1571, 1600, 1601),
        line("FBI applicants", 1080, 1606, 1300, 1636),
    ]
    texts = sorted(t for t, _, _ in merge_blocks(lines))
    assert texts == ["Kash Patel defends removing bestiality as FBI applicants", "Two House Democrats defect again on key vote"]


def test_does_not_merge_far_or_misaligned_lines():
    lines = [line("Home", 100, 100, 200, 130), line("World", 400, 100, 500, 130), line("Footer", 100, 900, 200, 930)]
    assert len(merge_blocks(lines)) == 3


def test_merged_block_keeps_min_confidence_and_union_box():
    lines = [line("a", 100, 100, 200, 130, conf=1.0), line("b", 102, 140, 260, 170, conf=0.5)]
    ((text, conf, box),) = merge_blocks(lines)
    assert text == "a b" and conf == 0.5 and box == (100, 100, 260, 170)


def test_reading_order_rows_then_columns():
    lines = [line("right", 800, 100, 900, 130), line("left", 100, 105, 200, 135), line("below", 100, 300, 200, 330)]
    assert [it.text for it in to_items(lines, 255)] == ["left", "right", "below"]


def test_budget_caps_items():
    lines = [line(str(i), 100, 100 + 40 * i, 200, 130 + 40 * i) for i in range(10)]
    assert len(to_items(lines, 3)) == 3


def _item(text: str) -> Item:
    return Item(0, text, 1.0, 0, 0, 10, 10, "tree item", "ax", True)


def test_ax_grounded_when_sidebar_is_on_screen():
    controls = [_item("Active Josh Nolan"), _item("New message"), _item("DMs")]
    assert ax_is_grounded(controls, ["Josh", "Nolan", "New", "message"], "Josh Nolan (DM)")


def test_ax_not_grounded_on_workspace_picker():
    controls = [_item("Active Josh Nolan"), _item("Chat with Slackbot AI"), _item("New message")]
    assert not ax_is_grounded(controls, ["Welcome", "back", "Comcreate", "workspace"], "Welcome back | Slack, Home")


def test_drops_hidden_dm_row_when_ocr_cannot_see_it():
    controls = [_item("Active Josh Nolan"), _item("New message")]
    visible = [
        Item(0, "New message", 0.9, 200, 80, 320, 100),
        Item(1, "Channels", 0.9, 200, 200, 300, 220),
        Item(2, "Comcreate", 0.9, 40, 40, 120, 60),
    ]
    grounded = ground_ax_targets(controls, [it.text for it in visible], "Jake (DM)", visible)
    names = [it.text for it in grounded]
    assert "New message" in names
    assert "Active Josh Nolan" not in names
    new = next(it for it in grounded if it.text == "New message")
    assert new.source == "ax+ocr" and new.x1 == 200


def test_uses_ocr_box_for_visible_josh_row():
    controls = [_item("Active Josh Nolan")]
    visible = [Item(0, "Josh Nolan", 0.9, 2100, 400, 2300, 430)]
    grounded = ground_ax_targets(controls, ["Josh Nolan"], "Slack", visible)
    assert len(grounded) == 1
    assert grounded[0].source == "ax+ocr"
    assert grounded[0].x1 == 2100


def test_goal_echo_matches_wrapped_command_lines():
    goal = "go to cnn and click onto something related to AI on the homepage"
    echoes = goal_echoes(goal)
    assert is_echo('clear && uv run clicker "go to cnn and click onto something', echoes)
    assert is_echo('related to AI on the homepage" --act', echoes)
    assert not is_echo("Trending: Trump and AI warnings", echoes)
