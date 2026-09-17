"""Browser catalog for Hyprland: your real Chrome / Firefox / Brave / etc.

Maps friendly names → window classes, binaries, and URL-read strategy.
Prefers focusing an already-open window so logins/cookies stay yours.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass


@dataclass(frozen=True)
class Browser:
    name: str                 # CLICKER_BROWSER value / activate() match
    classes: tuple[str, ...]  # Hyprland `class` / initialClass
    bins: tuple[str, ...]     # first existing wins
    family: str               # "chromium" | "firefox" | "other"


# Order = preference when auto-detecting (Chrome before stock Chromium)
BROWSERS: tuple[Browser, ...] = (
    Browser(
        "google-chrome",
        (
            "google-chrome",
            "google-chrome-stable",
            "Google-chrome",
            "Google-chrome-stable",
            "chrome",
            "google-chrome-beta",
            "google-chrome-unstable",
        ),
        (
            "google-chrome-stable",
            "google-chrome",
            "google-chrome-beta",
            "chrome",
        ),
        "chromium",
    ),
    Browser(
        "chromium",
        ("chromium", "Chromium", "chromium-browser"),
        ("chromium", "chromium-browser"),
        "chromium",
    ),
    Browser(
        "brave",
        ("brave-browser", "brave", "Brave-browser"),
        ("brave", "brave-browser"),
        "chromium",
    ),
    Browser(
        "vivaldi",
        ("vivaldi-stable", "vivaldi"),
        ("vivaldi", "vivaldi-stable"),
        "chromium",
    ),
    Browser(
        "microsoft-edge",
        ("microsoft-edge", "Microsoft-edge", "microsoft-edge-stable"),
        ("microsoft-edge", "microsoft-edge-stable"),
        "chromium",
    ),
    Browser(
        "firefox",
        ("firefox", "firefox-esr", "firefox-bin", "Navigator"),
        ("firefox", "firefox-esr"),
        "firefox",
    ),
    Browser(
        "zen",
        ("zen", "zen-browser", "zen-alpha", "zen-beta", "zen-twilight"),
        ("zen", "zen-browser", "zen-beta"),
        "firefox",
    ),
)


def _norm(s: str) -> str:
    return "".join(ch for ch in s.lower() if ch.isalnum())


def resolve(name: str | None) -> Browser | None:
    """Match CLICKER_BROWSER / activate() string to a known browser."""
    if not name:
        return None
    n = _norm(name)
    aliases = {
        "chrome": "googlechrome",
        "googlechrome": "googlechrome",
        "googlechromestable": "googlechrome",
        "googlechromebeta": "googlechrome",
        "googchrome": "googlechrome",
        "edge": "microsoftedge",
        "bravebrowser": "brave",
        "zenbrowser": "zen",
        "ff": "firefox",
    }
    n = aliases.get(n, n)
    for b in BROWSERS:
        if n == _norm(b.name) or n in {_norm(c) for c in b.classes} or n in {_norm(x) for x in b.bins}:
            return b
        if _norm(b.name) in n or n in _norm(b.name):
            return b
    return None


def binary(browser: Browser) -> str | None:
    for b in browser.bins:
        path = shutil.which(b)
        if path:
            return path
    return None


def matches_client(browser: Browser, klass: str, title: str = "") -> bool:
    k = klass.lower()
    t = title.lower()
    for c in browser.classes:
        cl = c.lower()
        if k == cl or cl in k or k in cl:
            return True
    token = browser.name.split("-")[0].lower()
    if token == "google":
        return "chrome" in k or "chrome" in t
    return token in k or token in t


def installed() -> list[Browser]:
    return [b for b in BROWSERS if binary(b) is not None]


def auto_default() -> str:
    """Prefer Chrome if installed, else first installed browser."""
    env = os.environ.get("CLICKER_BROWSER")
    if env:
        return env
    # Prefer Chrome explicitly when present
    chrome = resolve("chrome")
    if chrome and binary(chrome):
        return chrome.name
    for b in BROWSERS:
        if binary(b):
            return b.name
    return "google-chrome"
