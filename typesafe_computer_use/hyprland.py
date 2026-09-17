"""Hyprland / Wayland adapter.

Same public surface as macos.py so decide/actions/runner stay untouched.

Deps (NixOS/Hyprland):
  hyprland (hyprctl), grim, ydotool (+ ydotoold), wtype, tesseract,
  at-spi2-core, and either gobject-introspection Atspi or pyatspi.

This module does the full Mac loop on Linux:
  screenshot, click, type, scroll, focus app, open URL,
  read focused text field (AT-SPI), read browser URL (CDP).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image

from .config import ABORT_CORNER_PX
from .models import Abort, Field
from . import browsers as browser_catalog

# ydotool key codes (linux input-event-codes.h)
KEY = {
    "return": 28,
    "tab": 15,
    "escape": 1,
    "a": 30,
    "delete": 14,  # KEY_BACKSPACE — Mac clear uses delete after select-all
    "backspace": 14,
    "leftctrl": 29,
}

# AT-SPI role name → AX-style role so Field.is_text keeps working
_ATSPI_TO_AX = {
    "text": "AXTextField",
    "entry": "AXTextField",
    "password text": "AXTextField",
    "text box": "AXTextArea",
    "textframe": "AXTextArea",
    "paragraph": "AXTextArea",
    "search box": "AXSearchField",
    "combo box": "AXComboBox",
    "spin button": "AXTextField",
    "editable text": "AXTextField",
    "entry": "AXTextField",
}

_CDP_PORTS = (9222, 9223, 9229, 9333, 9888)


def _run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _hypr(cmd: str) -> dict | list | str:
    out = _run(["hyprctl", "-j", *cmd.split()], check=False).stdout
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return (out or "").strip()


def _have(bin_name: str) -> bool:
    return shutil.which(bin_name) is not None


# ------------------------------------------------------------------ escape hatch


def mouse_location() -> tuple[float, float]:
    try:
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
    """Ready to drive: hyprctl + grim present. Input tools checked at click/type time."""
    return _have("hyprctl") and _have("grim")


# ------------------------------------------------------------------ input


def click_at(point: tuple[float, float]) -> None:
    x, y = int(point[0]), int(point[1])
    if not _have("ydotool"):
        raise RuntimeError("ydotool not installed (ydotoold must be running with uinput)")
    _run(["ydotool", "mousemove", "--absolute", "-x", str(x), "-y", str(y)])
    time.sleep(0.03)
    _run(["ydotool", "click", "0xC0"])  # left down+up


def press(key: str, command: bool = False) -> None:
    """command=True → Ctrl (Linux stand-in for macOS Cmd: select-all, etc.)."""
    if not _have("ydotool"):
        raise RuntimeError("ydotool not installed")
    code = KEY.get(key)
    if code is None:
        if _have("wtype"):
            mods = ["-M", "ctrl"] if command else []
            _run(["wtype", *mods, "-k", key])
            return
        raise KeyError(key)
    if command:
        _run(
            [
                "ydotool",
                "key",
                f"{KEY['leftctrl']}:1",
                f"{code}:1",
                f"{code}:0",
                f"{KEY['leftctrl']}:0",
            ]
        )
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
        _run(
            [
                "ydotool",
                "mousemove",
                "--absolute",
                "-x",
                str(int(center[0])),
                "-y",
                str(int(center[1])),
            ]
        )
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
    """Focus an already-open app/browser window; launch only if none exists.

    Browser names (`firefox`, `chrome`, `brave`, …) match Hyprland classes via
    the browser catalog so your real session (cookies/logins) is reused.
    """
    spec = browser_catalog.resolve(app)
    clients = _hypr("clients")
    if not isinstance(clients, list):
        clients = []

    target = None
    if spec is not None:
        for c in clients:
            klass = str(c.get("class") or c.get("initialClass") or "")
            title = str(c.get("title") or "")
            if browser_catalog.matches_client(spec, klass, title):
                target = c
                break
    if target is None:
        needle = app.lower()
        for c in clients:
            klass = str(c.get("class") or "")
            title = str(c.get("title") or "")
            if needle in klass.lower() or needle in title.lower() or klass.lower() == needle:
                target = c
                break

    if target is not None:
        addr = target.get("address")
        if addr:
            _run(["hyprctl", "dispatch", "focuswindow", f"address:{addr}"], check=False)
    else:
        launch = browser_catalog.binary(spec) if spec else None
        launch = launch or (shutil.which(app) if shutil.which(app) else None)
        if not launch:
            return False
        _run(["hyprctl", "dispatch", "exec", "--", launch], check=False)

    end = time.monotonic() + timeout
    while time.monotonic() < end:
        front = frontmost_app()
        if spec is not None and browser_catalog.matches_client(spec, front):
            return True
        if app.lower() in front.lower():
            return True
        time.sleep(0.1)
    front = frontmost_app()
    if spec is not None:
        return browser_catalog.matches_client(spec, front)
    return app.lower() in front.lower()


def open_url(browser: str, url: str) -> bool:
    """Open URL in your real browser profile.

    If that browser is already running, `exec browser url` reuses the same
    process/profile (new tab). Otherwise launches it.
    """
    spec = browser_catalog.resolve(browser)
    bin_path = browser_catalog.binary(spec) if spec else None
    if bin_path is None:
        # last-resort: xdg-open (uses your default browser / portal)
        _run(["hyprctl", "dispatch", "exec", "--", "xdg-open", url], check=False)
        time.sleep(0.6)
        return activate(browser) or True

    # Prefer focusing existing window first so the new tab lands in THAT profile.
    activate(browser, timeout=1.5)
    _run(["hyprctl", "dispatch", "exec", "--", bin_path, url], check=False)
    time.sleep(0.5)
    return activate(browser)


def browser_url(browser: str) -> str | None:
    """Active tab URL from your running browser.

    Order:
      1. Chromium-family CDP (--remote-debugging-port, keeps your profile)
      2. AT-SPI address bar / document URL (Firefox + Chromium)
      3. Window title heuristic
    """
    spec = browser_catalog.resolve(browser)
    family = spec.family if spec else ""

    if family != "firefox":
        url = _cdp_active_url(browser)
        if url:
            return url

    url = _atspi_browser_url(browser)
    if url:
        return url

    return _title_url_guess()


def _cdp_active_url(browser: str) -> str | None:
    """Read chrome://json/list from common debugging ports.

    Start Chromium with --remote-debugging-port=9222 (or set CLICKER_CDP_PORT).
    """
    import os

    ports: list[int] = []
    env_port = os.environ.get("CLICKER_CDP_PORT")
    if env_port and env_port.isdigit():
        ports.append(int(env_port))
    ports.extend(_CDP_PORTS)

    needle = browser.lower().replace(" ", "")
    for port in ports:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=0.4) as resp:
                tabs = json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
            continue
        if not isinstance(tabs, list):
            continue
        # Prefer a page tab that looks focused / matches browser
        pages = [t for t in tabs if t.get("type") == "page" and t.get("url")]
        if not pages:
            continue
        # Match browser name loosely against tab metadata; else first http(s) page
        for t in pages:
            blob = f"{t.get('url','')} {t.get('title','')}".lower()
            if needle and needle in {"firefox"}:
                continue  # CDP is chromium-family
            if t.get("url", "").startswith(("http://", "https://", "file://")):
                return str(t["url"])
        for t in pages:
            if t.get("url"):
                return str(t["url"])
    return None



def _atspi_browser_url(browser: str) -> str | None:
    """Best-effort URL from the accessibility tree (address bar or document URI)."""
    field = _atspi_focused_field()
    if field and field.value:
        v = field.value.strip()
        if v.startswith(("http://", "https://", "file://", "about:")):
            return v
        if "." in v and " " not in v and "/" in v:
            return v if "://" in v else "https://" + v
    try:
        return _atspi_find_document_uri(browser)
    except Exception:
        return None


def _atspi_find_document_uri(browser: str) -> str | None:
    """Search the frontmost app tree for a document URI or address entry."""
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi

        if hasattr(Atspi, "init"):
            Atspi.init()
        desktop = Atspi.get_desktop(0)
    except Exception:
        return None

    spec = browser_catalog.resolve(browser)
    pid = frontmost_pid()

    def walk(node, depth: int) -> str | None:
        if node is None or depth > 18:
            return None
        try:
            role = (node.get_role_name() or "").lower()
            name = (node.get_name() or "").strip()
            try:
                attrs = dict(node.get_attributes() or {})
            except Exception:
                attrs = {}
            for key in ("uri", "URL", "url", "doc-uri"):
                val = attrs.get(key)
                if val and str(val).startswith(("http://", "https://", "file://")):
                    return str(val)
            if role in {"document web", "document frame", "html container"} and name.startswith(
                ("http://", "https://")
            ):
                return name
            if role in {"text", "entry", "edit bar", "autocomplete"} and (
                "address" in name.lower()
                or "location" in name.lower()
                or "search or enter" in name.lower()
                or name.lower() == "address and search bar"
            ):
                try:
                    if hasattr(node, "get_text") and hasattr(node, "get_character_count"):
                        n = node.get_character_count()
                        val = node.get_text(0, n) if n else ""
                    else:
                        val = name
                    val = (val or "").strip()
                    if val.startswith(("http://", "https://")):
                        return val
                except Exception:
                    pass
            count = node.get_child_count()
            for i in range(min(count, 80)):
                found = walk(node.get_child_at_index(i), depth + 1)
                if found:
                    return found
        except Exception:
            return None
        return None

    try:
        n_apps = desktop.get_child_count()
        for i in range(n_apps):
            app = desktop.get_child_at_index(i)
            try:
                app_pid = app.get_process_id() if hasattr(app, "get_process_id") else None
            except Exception:
                app_pid = None
            app_name = ""
            try:
                app_name = app.get_name() or ""
            except Exception:
                pass
            use = False
            if pid and app_pid == pid:
                use = True
            elif spec and browser_catalog.matches_client(spec, app_name, app_name):
                use = True
            if not use:
                continue
            found = walk(app, 0)
            if found:
                return found
    except Exception:
        return None
    return None


def _title_url_guess() -> str | None:
    w = _hypr("activewindow")
    if not isinstance(w, dict):
        return None
    title = str(w.get("title") or "")
    # Rare: some setups put the raw URL in the title
    for token in title.split():
        if token.startswith("http://") or token.startswith("https://"):
            return token.rstrip(".,)")
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


# ------------------------------------------------------------------ capture


def screenshot() -> Image.Image:
    if not _have("grim"):
        raise RuntimeError("grim not installed (Wayland screenshot tool)")
    path = Path(tempfile.mkdtemp()) / "screen.png"
    _run(["grim", str(path)])
    return Image.open(path).convert("RGB")


def display_scale(image: Image.Image) -> float:
    mons = _hypr("monitors")
    if isinstance(mons, list):
        for m in mons:
            if m.get("focused"):
                scale = float(m.get("scale") or 1.0)
                return scale if scale > 0 else 1.0
    return 1.0


# ------------------------------------------------------------------ focused field (AT-SPI)


def focused_field() -> Field | None:
    """Focused UI element via AT-SPI (Linux accessibility), else None.

    Maps AT-SPI roles onto AX* names so Field.is_text / type_text work unchanged.
    """
    field = _atspi_focused_field()
    if field is not None:
        return field
    return None


def _atspi_focused_field() -> Field | None:
    # Prefer GI Atspi (Nix: wrap with gobject-introspection / at-spi2-core)
    try:
        return _atspi_gi_focused()
    except Exception:
        pass
    try:
        return _atspi_pyatspi_focused()
    except Exception:
        pass
    return None


def _map_role(raw: str) -> str:
    key = (raw or "").strip().lower()
    if key in _ATSPI_TO_AX:
        return _ATSPI_TO_AX[key]
    # already AX-style
    if raw.startswith("AX"):
        return raw
    # heuristic
    if "text" in key or "entry" in key or "edit" in key:
        return "AXTextField"
    if "combo" in key:
        return "AXComboBox"
    if "search" in key:
        return "AXSearchField"
    return raw or "AXUnknown"


def _atspi_gi_focused() -> Field | None:
    import gi

    gi.require_version("Atspi", "2.0")
    from gi.repository import Atspi

    if hasattr(Atspi, "init"):
        Atspi.init()

    acc = None
    if hasattr(Atspi, "get_focus"):
        acc = Atspi.get_focus()
    if acc is None and hasattr(Atspi, "get_desktop"):
        # walk desktops for STATE_FOCUSED
        desktop = Atspi.get_desktop(0)
        acc = _walk_focused_gi(desktop, depth=0)
    if acc is None:
        return None

    role_name = ""
    try:
        role_name = acc.get_role_name() or ""
    except Exception:
        pass

    name = ""
    try:
        name = acc.get_name() or ""
    except Exception:
        pass

    value = ""
    try:
        # text interface
        if hasattr(acc, "get_text"):
            # Atspi.Text get_text(start, end)
            n = acc.get_character_count() if hasattr(acc, "get_character_count") else 0
            value = acc.get_text(0, n) if n else ""
        elif hasattr(acc, "get_text_iface"):
            iface = acc.get_text_iface()
            if iface:
                n = iface.get_character_count()
                value = iface.get_text(0, n)
    except Exception:
        try:
            value = str(acc.get_attributes().get("value", ""))  # type: ignore[attr-defined]
        except Exception:
            value = ""

    placeholder = ""
    try:
        attrs = acc.get_attributes() or {}
        placeholder = str(attrs.get("placeholder-text") or attrs.get("placeholder") or "")
    except Exception:
        pass

    x = y = w = h = 0.0
    try:
        comp = acc.get_component_iface() if hasattr(acc, "get_component_iface") else None
        if comp is None and hasattr(acc, "get_extents"):
            ext = acc.get_extents(Atspi.CoordType.SCREEN)
            x, y, w, h = float(ext.x), float(ext.y), float(ext.width), float(ext.height)
        elif comp is not None:
            ext = comp.get_extents(Atspi.CoordType.SCREEN)
            x, y, w, h = float(ext.x), float(ext.y), float(ext.width), float(ext.height)
    except Exception:
        pass

    return Field(
        role=_map_role(role_name),
        label=str(name),
        placeholder=str(placeholder),
        value=value if isinstance(value, str) else "",
        x=x,
        y=y,
        w=w,
        h=h,
    )


def _walk_focused_gi(node, depth: int):
    if depth > 25 or node is None:
        return None
    try:
        from gi.repository import Atspi

        state = node.get_state_set()
        if state and state.contains(Atspi.StateType.FOCUSED):
            return node
        count = node.get_child_count()
        for i in range(count):
            child = node.get_child_at_index(i)
            found = _walk_focused_gi(child, depth + 1)
            if found is not None:
                return found
    except Exception:
        return None
    return None


def _atspi_pyatspi_focused() -> Field | None:
    import pyatspi

    acc = None
    if hasattr(pyatspi, "getFocus"):
        acc = pyatspi.getFocus()
    if acc is None:
        desktop = pyatspi.Registry.getDesktop(0)
        acc = _walk_focused_pyatspi(desktop, 0)
    if acc is None:
        return None

    role_name = ""
    try:
        role_name = acc.getRoleName() or ""
    except Exception:
        pass
    name = ""
    try:
        name = acc.name or ""
    except Exception:
        pass
    value = ""
    try:
        text = acc.queryText()
        value = text.getText(0, text.characterCount)
    except Exception:
        try:
            value = acc.queryEditableText().getText(0, -1)  # type: ignore
        except Exception:
            value = ""
    x = y = w = h = 0.0
    try:
        comp = acc.queryComponent()
        ext = comp.getExtents(pyatspi.COMPONENT_LAYER_WINDOW)
        # getExtents often returns (x,y,w,h) in screen coords with SCREEN coord type
        ext = comp.getExtents(0)  # COORD_TYPE_SCREEN = 0
        x, y, w, h = float(ext.x), float(ext.y), float(ext.width), float(ext.height)
    except Exception:
        pass
    return Field(
        role=_map_role(role_name),
        label=str(name),
        placeholder="",
        value=value if isinstance(value, str) else "",
        x=x,
        y=y,
        w=w,
        h=h,
    )


def _walk_focused_pyatspi(node, depth: int):
    if depth > 25 or node is None:
        return None
    try:
        import pyatspi

        if node.getState().contains(pyatspi.STATE_FOCUSED):
            return node
        for i in range(node.childCount):
            found = _walk_focused_pyatspi(node.getChildAtIndex(i), depth + 1)
            if found is not None:
                return found
    except Exception:
        return None
    return None


def platform_info() -> dict:
    atspi = False
    try:
        import gi

        gi.require_version("Atspi", "2.0")
        from gi.repository import Atspi  # noqa: F401

        atspi = True
    except Exception:
        try:
            import pyatspi  # noqa: F401

            atspi = True
        except Exception:
            atspi = False
    return {
        "platform": "hyprland",
        "hyprctl": _have("hyprctl"),
        "grim": _have("grim"),
        "ydotool": _have("ydotool"),
        "wtype": _have("wtype"),
        "tesseract": _have("tesseract"),
        "atspi": atspi,
        "cdp": _cdp_active_url("") is not None,
        "active": frontmost_app(),
    }
