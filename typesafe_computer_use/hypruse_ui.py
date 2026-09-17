"""Read-only Hypruse `ui` dump for the focused window.

Hypruse is the Linux accessibility chassis. One MCP call per capture. Input tools
are never requested; HYPRUSE_READONLY stays on.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass

CHROME_NOISE = {
    "add to dictionary",
    "undo",
    "redo",
    "cut",
    "copy",
    "paste",
    "paste without formatting",
    "delete",
    "select all",
    "check spelling",
    "languages",
    "inspect accessibility properties",
    "inspect",
    "close",
    "back in history",
    "forward in history",
    "show history",
    "history navigation",
}


@dataclass(frozen=True)
class AxTarget:
    name: str
    role: str
    x: float
    y: float
    clickable: bool
    w: float = 0.0
    h: float = 0.0


def hypruse_bin() -> str | None:
    env = os.environ.get("HYPRUSE_BIN")
    if env and Path_exists(env):
        return env
    found = shutil.which("hypruse")
    if found:
        return found
    fallback = "/home/cjm/.local/state/hypruse-poc/package/bin/hypruse"
    return fallback if Path_exists(fallback) else None


def Path_exists(path: str) -> bool:
    return os.path.isfile(path) and os.access(path, os.X_OK)


def is_chrome_noise(name: str) -> bool:
    return (name or "").strip().lower() in CHROME_NOISE


INTERACTIVE_ROLES = {
    "button",
    "page tab",
    "entry",
    "link",
    "tree item",
    "check box",
    "combo box",
    "radio button",
    "toggle button",
    "tab",
    "push button",
}

SKIP_ROLES = {
    "frame",
    "document web",
    "embedded",
    "panel",
    "scroll pane",
    "tool bar",
    "page tab list",
    "menu",
    "image",
    "tree",
}


def parse_nodes(texts: list[str]) -> list[dict]:
    nodes: list[dict] = []
    for block in texts:
        block = (block or "").strip()
        if not block:
            continue
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, list):
            nodes.extend(x for x in parsed if isinstance(x, dict))
            continue
        if isinstance(parsed, dict):
            nodes.append(parsed)
            continue
        for line in block.splitlines():
            line = line.strip()
            if line.startswith("{") and line.endswith("}"):
                try:
                    node = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(node, dict):
                    nodes.append(node)
    return nodes


def nodes_to_targets(nodes: list[dict], clickable_only: bool = False) -> list[AxTarget]:
    """Slack/Vercel rows often have clickable=false. Keep interactive roles anyway."""
    out: list[AxTarget] = []
    seen: set[tuple[str, str, int, int]] = set()
    for node in nodes:
        name = str(node.get("name") or "").strip()
        role = str(node.get("role") or "").strip()
        if not name or is_chrome_noise(name):
            continue
        if role in SKIP_ROLES:
            continue
        clickable = bool(node.get("clickable"))
        useful = clickable or role in INTERACTIVE_ROLES
        if clickable_only and not useful:
            continue
        if not useful:
            continue
        x = float(node.get("x") or 0)
        y = float(node.get("y") or 0)
        key = (name.lower(), role, int(x), int(y))
        if key in seen:
            continue
        seen.add(key)
        out.append(AxTarget(name=name, role=role, x=x, y=y, clickable=clickable or role in INTERACTIVE_ROLES))
    return out


async def _ui_nodes(address: str) -> list[dict]:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    binary = hypruse_bin()
    if not binary or not address:
        return []
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        in {
            "PATH",
            "HOME",
            "USER",
            "LANG",
            "LC_ALL",
            "XDG_RUNTIME_DIR",
            "XDG_CURRENT_DESKTOP",
            "XDG_SESSION_TYPE",
            "WAYLAND_DISPLAY",
            "DISPLAY",
            "HYPRLAND_INSTANCE_SIGNATURE",
            "DBUS_SESSION_BUS_ADDRESS",
        }
    }
    env.update(
        {
            "HYPRUSE_READONLY": "1",
            "HYPRUSE_MARK": "0",
            "HYPRUSE_CLIPBOARD": "0",
            "HYPRUSE_JOURNAL": "0",
            "HYPRUSE_AUTH_GUARD": "off",
            "HYPRUSE_STRICT": "0",
        }
    )
    async with stdio_client(StdioServerParameters(command=binary, env=env)) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            result = await session.call_tool("ui", {"window": address, "actionable": False})
            texts = [c.text for c in result.content if getattr(c, "type", None) == "text"]
            return parse_nodes(texts)


def window_targets(address: str) -> list[AxTarget]:
    """Blocking read of clickable Hypruse nodes. Empty on any failure."""
    if not address or not hypruse_bin():
        return []
    try:
        import asyncio

        nodes = asyncio.run(_ui_nodes(address))
        return nodes_to_targets(nodes, clickable_only=False)
    except Exception:
        return []
