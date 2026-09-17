"""Pick macos vs hyprland adapter."""

from __future__ import annotations

import os
import sys


def load():
    forced = os.environ.get("CLICKER_PLATFORM", "").lower()
    if forced in {"hyprland", "linux", "wayland"}:
        from . import hyprland as plat
        return plat
    if forced in {"macos", "darwin"}:
        from . import macos as plat
        return plat
    if sys.platform == "darwin":
        from . import macos as plat
        return plat
    # default Linux → hyprland adapter (x11 users can still use ydotool)
    from . import hyprland as plat
    return plat
