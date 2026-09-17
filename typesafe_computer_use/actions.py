"""Execute one decided action. Every function returns a one-line description for the history."""

from __future__ import annotations

import time
from dataclasses import dataclass

import anthropic
from typesafe_sdk import TypeSafeClient

from .platform import load as _load_plat
macos = _load_plat()  # noqa: N816 — keep name macos for minimal diff
from .config import SITES
from .decide import Decision, verify_typed
from .models import Item, Screen
from .writer import compose_text, compose_url

VERIFY_THRESHOLD = 0.5
NOOP_MARKERS = ("refused", "failed", "waited")


@dataclass(frozen=True)
class Context:
    goal: str
    browser: str
    email: str | None
    typesafe: TypeSafeClient
    writer: anthropic.Anthropic | None
    history: list[str]


def is_noop(description: str) -> bool:
    return any(marker in description for marker in NOOP_MARKERS)


def perform(decision: Decision, screen: Screen, items: list[Item], ctx: Context) -> str:
    key = decision.chosen
    by_index = {str(it.index): it for it in items}
    if key in by_index:
        item = by_index[key]
        macos.click_at(screen.to_points(item))
        return f"clicked {item.text!r}"
    handler = _HANDLERS.get(key)
    if handler is None:
        raise ValueError(f"unknown action {key!r}")
    return handler(decision, screen, items, ctx)


def _switch_to_browser(decision, screen, items, ctx: Context) -> str:
    if macos.activate(ctx.browser):
        return f"activated {ctx.browser}"
    return f"switch_to_browser failed: {ctx.browser} did not come to the front"


def _open_site(decision: Decision, screen, items, ctx: Context) -> str:
    url = SITES.get(decision.site.choice) or (compose_url(ctx.writer, ctx.goal, ctx.history) if ctx.writer else "")
    if not url:
        return "open_site refused: no known site matches and no writer available to propose a URL"
    if macos.open_url(ctx.browser, url):
        return f"opened {url}"
    return f"open_site failed: opened {url} but {ctx.browser} did not come to the front"


def _type_email(decision, screen: Screen, items, ctx: Context) -> str:
    if not (screen.field and screen.field.is_text):
        return "type_email refused: no text field is focused"
    macos.type_text(ctx.email or "")
    return "typed email"


def _type_text(decision, screen: Screen, items, ctx: Context) -> str:
    if not (screen.field and screen.field.is_text):
        return "type_text refused: no text field is focused"
    if ctx.writer is None:
        return "type_text refused: no writer available"
    text = compose_text(ctx.writer, ctx.goal, screen, items, ctx.history)
    if not text:
        return "type_text refused: writer declined to fill this field"
    macos.type_text(text)
    time.sleep(0.3)
    p = verify_typed(ctx.typesafe, ctx.goal, screen.field, text, macos.focused_field())
    if p < VERIFY_THRESHOLD:
        macos.clear_field()
        return f"typed {text!r} into {screen.field.label!r} but verification failed ({p:.2f}); cleared it"
    return f"typed {text!r} into {screen.field.label!r} (verified {p:.2f})"


def _key(name: str, description: str):
    def handler(decision, screen, items, ctx) -> str:
        macos.press(name)
        return description

    return handler


def _scroll(lines: int, description: str):
    def handler(decision, screen, items, ctx) -> str:
        macos.scroll(lines)
        return description

    return handler


_HANDLERS = {
    "switch_to_browser": _switch_to_browser,
    "open_site": _open_site,
    "type_email": _type_email,
    "type_text": _type_text,
    "press_enter": _key("return", "pressed Return"),
    "press_escape": _key("escape", "pressed Escape"),
    "scroll_down": _scroll(-10, "scrolled down"),
    "scroll_up": _scroll(10, "scrolled up"),
    "wait": lambda decision, screen, items, ctx: "waited",
}
