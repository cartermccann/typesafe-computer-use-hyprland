from typesafe_computer_use.hypruse_ui import is_chrome_noise, nodes_to_targets, parse_nodes
from typesafe_computer_use.runner import is_click_loop, needs_write_confirm, redact_action


def test_parse_hypruse_lines_and_drop_chrome():
    raw = """
{"role": "menu item", "name": "Undo", "x": 1, "y": 1, "clickable": false}
{"role": "page tab", "name": "DMs", "x": 10, "y": 20, "clickable": true}
{"role": "button", "name": "New message", "x": 30, "y": 40, "clickable": true}
{"role": "tree", "name": "Channels and direct messages", "x": 50, "y": 60, "clickable": true}
"""
    nodes = parse_nodes([raw])
    assert is_chrome_noise("Undo")
    extra = parse_nodes(
        [
            '{\n  "role": "tree item",\n  "name": "Active Josh Nolan",\n  "x": 10,\n  "y": 80,\n  "clickable": false\n}'
        ]
    )
    targets = nodes_to_targets(nodes + extra)
    names = [t.name for t in targets]
    assert "DMs" in names
    assert "New message" in names
    assert "Active Josh Nolan" in names
    assert "Undo" not in names
    assert "Channels and direct messages" not in names


def test_click_loop_detects_josh_dms_pong():
    hist = ["clicked 'Josh'", "clicked 'DMs'", "clicked 'Josh'", "clicked 'DMs'"]
    assert is_click_loop(hist)
    assert not is_click_loop(["clicked 'Josh'", "typed hi"])


def test_redact_and_confirm():
    assert redact_action("typed redis://default:secret@host:6379") == "typed [redacted]"
    assert (
        redact_action("typed 'hi from jev' (no focused-field check on this platform)")
        == "typed 'hi from jev' (no focused-field check on this platform)"
    )
    assert redact_action("clicked 'New message'") == "clicked 'New message'"
    assert needs_write_confirm("clicked 'Save'")
    assert needs_write_confirm("clicked 'Delete project'")
    assert not needs_write_confirm("clicked 'New message'")
    assert not needs_write_confirm("clicked 'DMs'")
