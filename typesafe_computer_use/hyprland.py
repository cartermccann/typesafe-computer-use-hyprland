"""Hyprland / Wayland adapter.

Same public surface as macos.py so decide/actions/runner stay untouched.

Deps (Arch/Hyprland):
  hyprland (hyprctl), grim, ydotool (+ ydotoold user service), tesseract, wtype (optional)

Notes:
  - Screenshots via grim (Wayland-native).
  - Clicks/keys via ydotool (needs ydotoold with uinput access).
  - Window focus / geometry via hyprctl JSON.
  - Focused field: best-effort from activewindow (AT-SPI full field introspect is TODO).
  - Browser URL: best-effort from window title; optional CDP later.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from PIL import Image

from .config import ABORT_CORNER_PX
from .models import Abort, Field

# ydotool key codes (linux input-event-codes)
KEY = {
    "return": 28,
    "tab": 15,
    "escape": 1,
    "a": 30,
    "delete": 14,  # KEY_BACKSPACE — used like mac clear
    "backspace": 14,
    "leftctrl": 29,
}


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _hypr(cmd: str) -> dict | list | str:
    out = _run(["hyprctl", "-j", *cmd.split()]).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return out.strip()


def _have(bin_name: str) -> bool:
    return shutil.which(bin_name) is not None


# ------------------------------------------------------------------ escape hatch


def mouse_location() -> tuple[float, float]:
    # ydotool has no get-location; fall back to hyprctl cursor pos if available
    try:
        # hyprctl cursorpos → "1234, 567"
        raw = _run(["hyprctl", "cursorpos"], check=False).stdout.strip()
        if "," in raw:
            x, y = raw.split(",", 1)
            return float(x.strip()), float(y.strip())
    except Exception:
        pass
    return -1.0, -1.0


def check_abort() -> None:
    x, y = mouse_location()
    if x < 0:
        return
    if x <= ABORT_CORNER_PX and y <= ABORT_CORNER_PX:
        raise Abort("mouse in top-left corner")


def sleep_watching(seconds: float) -> None:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        check_abort()
        time.sleep(0.1)


def accessibility_trusted() -> bool:
    """On Hyprland we need hyprctl + grim + (ydotool or dry-run)."""
    return _have("hyprctl") and _have("grim")


# ------------------------------------------------------------------ input


def click_at(point: tuple[float, float]) -> None:
    x, y = int(point[0]), int(point[1])
    if not _have("ydotool"):
        raise RuntimeError("ydotool not installed (and ydotoold must be running)")
    _run(["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)])
    time.sleep(0.03)
    _run(["ydotool", "click", "0xC0"])  # left click down+up


def press(key: str, command: bool = False) -> None:
    """command=True → Ctrl (Linux equivalent of macOS Cmd for select-all etc.)."""
    if not _have("ydotool"):
        raise RuntimeError("ydotool not installed")
    code = KEY.get(key)
    if code is None:
        # try wtype for named keys
        if _have("wtype"):
            _run(["wtype", "-k", key])
            return
        raise KeyError(key)
    if command:
        # hold ctrl
        _run(["ydotool", "key", f"{KEY['leftctrl']}:1", f"{code}:1", f"{code}:0", f"{KEY['leftctrl']}:0"])
    else:
        _run(["ydotool", "key", f"{code}:1", f"{code}:0"])


def type_text(text: str) -> None:
    if _have("wtype"):
        _run(["wtype", "--", text])
        return
    if _have("ydotool"):
        _run(["ydotool", "type", "--", text])
        return
    raise RuntimeError("need wtype or ydotool to type")


def clear_field() -> None:
    press("a", command=True)
    press("delete")


def scroll(lines: int) -> None:
    if not _have("ydotool"):
        raise RuntimeError("ydotool not installed")
    center = frontmost_window_center()
    if center is not None:
        _run(["ydotool", "mousemove", "--absolute", "-x", str(int(center[0])), "-y", str(int(center[1]))])
    # ydotool wheel: positive = up on many setups; match mac "lines" sign as best-effort
    amount = abs(int(lines)) or 1
    btn = "4" if lines > 0 else "5"  # 4=up 5=down typical
    for _ in range(amount):
        _run(["ydotool", "click", btn])
        time.sleep(0.02)


# ------------------------------------------------------------------ apps and windows


def frontmost_app() -> str:
    w = _hypr("activewindow")
    if isinstance(w, dict):
        return str(w.get("class") or w.get("initialClass") or w.get("title") or "")
    return ""


def frontmost_pid() -> int:
    w = _hypr("activewindow")
    if isinstance(w, dict):
        return int(w.get("pid") or 0)
    return 0


def activate(app: str, timeout: float = 3.0) -> bool:
    """Focus a client whose class or title matches app (case-insensitive contains)."""
    needle = app.lower()
    clients = _hypr("clients")
    if not isinstance(clients, list):
        return False
    target = None
    for c in clients:
        klass = str(c.get("class") or "")
        title = str(c.get("title") or "")
        if needle in klass.lower() or needle in title.lower() or klass.lower() == needle:
            target = c
            break
    if target is None:
        # try launching via hyprctl dispatch exec — only if LOOKS like a binary
        if shutil.which(app):
            _run(["hyprctl", "dispatch", "exec", "--", app], check=False)
        else:
            return False
    else:
        addr = target.get("address")
        if addr:
            _run(["hyprctl", "dispatch", "focuswindow", f"address:{addr}"], check=False)
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if needle in frontmost_app().lower():
            return True
        time.sleep(0.1)
    return needle in frontmost_app().lower()


def open_url(browser: str, url: str) -> bool:
    # Prefer xdg-open / browser CLI so we don't fight Hyprland
    browser_bin = {
        "google chrome": "google-chrome-stable",
        "google-chrome": "google-chrome-stable",
        "chrome": "google-chrome-stable",
        "chromium": "chromium",
        "firefox": "firefox",
        "brave": "brave",
    }.get(browser.lower(), browser.lower().replace(" ", "-"))
    if shutil.which(browser_bin):
        _run(["hyprctl", "dispatch", "exec", "--", browser_bin, url], check=False)
    else:
        _run(["hyprctl", "dispatch", "exec", "--", "xdg-open", url], check=False)
    time.sleep(0.5)
    return activate(browser_bin) or activate(browser)


def browser_url(browser: str) -> str | None:
    """Best-effort: many browsers put the URL or site in the title — not reliable.

    For real URL reads, wire Chrome DevTools Protocol later.
    """
    w = _hypr("activewindow")
    if not isinstance(w, dict):
        return None
    title = str(w.get("title") or "")
    # crude: if title looks like a host
    if " - " in title:
        # Chrome: "Page Title - Google Chrome"
        return None
    return None


def frontmost_window_center() -> tuple[float, float] | None:
    w = _hypr("activewindow")
    if not isinstance(w, dict):
        return None
    at = w.get("at") or [0, 0]
    size = w.get("size") or [0, 0]
    try:
        x, y = float(at[0]), float(at[1])
        ww, hh = float(size[0]), float(size[1])
    except (TypeError, ValueError, IndexError):
        return None
    if ww < 50 or hh < 50:
        return None
    return x + ww / 2, y + hh / 2


# ------------------------------------------------------------------ capture and focus


def screenshot() -> Image.Image:
    if not _have("grim"):
        raise RuntimeError("grim not installed (Wayland screenshot tool)")
    path = Path(tempfile.mkdtemp()) / "screen.png"
    # Output name from hyprctl monitors — grim without args = full virtual size
    _run(["grim", str(path)])
    return Image.open(path).convert("RGB")


def display_scale(image: Image.Image) -> float:
    """Capture pixels per logical px. Hyprland exposes scale on the focused monitor."""
    mons = _hypr("monitors")
    if isinstance(mons, list):
        for m in mons:
            if m.get("focused"):
                scale = float(m.get("scale") or 1.0)
                # grim captures in physical pixels; logical width ≈ width/scale
                return scale if scale > 0 else 1.0
    return 1.0


def focused_field() -> Field | None:
    """Placeholder until AT-SPI is wired. Uses active window geometry as a weak stand-in."""
    w = _hypr("activewindow")
    if not isinstance(w, dict):
        return None
    at = w.get("at") or [0, 0]
    size = w.get("size") or [0, 0]
    try:
        x, y = float(at[0]), float(at[1])
        ww, hh = float(size[0]), float(size[1])
    except (TypeError, ValueError, IndexError):
        x = y = ww = hh = 0.0
    # Without AT-SPI we can't know if a text field is focused — mark as unknown non-text
    return Field(
        role="hypr:window",
        label=str(w.get("title") or ""),
        placeholder="",
        value="",
        x=x,
        y=y,
        w=ww,
        h=hh,
    )


def platform_info() -> dict:
    return {
        "platform": "hyprland",
        "hyprctl": _have("hyprctl"),
        "grim": _have("grim"),
        "ydotool": _have("ydotool"),
        "wtype": _have("wtype"),
        "tesseract": _have("tesseract"),
        "active": frontmost_app(),
    }
