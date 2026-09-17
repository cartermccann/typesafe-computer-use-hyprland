"""The writer model: the only place free text is generated, and only when the classifier asks for it."""

from __future__ import annotations

import json
import re
from urllib.parse import urlparse

import anthropic

from .config import writer_model
from .dates import now_context
from .models import Item, Screen
from .perception import near_field


def make_writer() -> anthropic.Anthropic | None:
    """A client, or None when no Anthropic credentials resolve (the SDK only checks on first request)."""
    client = anthropic.Anthropic()
    if client.api_key or getattr(client, "auth_token", None):
        return client
    return None


def _structured(writer: anthropic.Anthropic, system: str, packet: dict, properties: dict, max_tokens: int) -> dict:
    response = writer.messages.create(
        model=writer_model(),
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": json.dumps(packet)}],
        output_config={
            "format": {
                "type": "json_schema",
                "schema": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            }
        },
    )
    return json.loads("".join(b.text for b in response.content if b.type == "text"))


def literal_text_from_goal(goal: str) -> str:
    """Pull an explicit string to type when no writer model is configured."""
    quoted = re.findall(r"[\"“](.+?)[\"”]", goal)
    if quoted:
        return quoted[-1].strip()
    match = re.search(r"\b(?:message|dm|tell|text|say)\s+\S+\s+(.+)$", goal, re.I)
    if match:
        return match.group(1).strip().strip("'\"")
    match = re.search(r"\btype\s+(.+)$", goal, re.I)
    if match:
        return match.group(1).strip().strip("'\"")
    return ""


def compose_text(writer: anthropic.Anthropic, goal: str, screen: Screen, items: list[Item], history: list[str]) -> str:
    """The exact string to type into the focused field. Empty means the writer declined."""
    packet = {
        "goal": goal,
        "now": now_context(),
        "frontmost_app": screen.app,
        "previous_actions": history[-8:],
        "focused_field": screen.field.summary() if screen.field else None,
        "text_near_field": near_field(screen, items),
        "all_screen_text": [it.text for it in items][:120],
    }
    data = _structured(
        writer,
        system=(
            "You fill in one text field on a user's screen. You receive the user's goal, recent "
            "actions, the focused field's label and placeholder, and nearby screen text. Decide the "
            "exact string to type. Never invent credentials, passwords, or personal data; for such "
            "fields, or when the field should not be filled, set fill to false."
        ),
        packet=packet,
        properties={"fill": {"type": "boolean"}, "text": {"type": "string"}, "reason": {"type": "string"}},
        max_tokens=256,
    )
    return data["text"].strip() if data["fill"] else ""


def valid_url(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and "." in parsed.netloc and not any(ch.isspace() for ch in url)


def compose_url(writer: anthropic.Anthropic, goal: str, history: list[str]) -> str:
    """The URL to open for this goal. Empty means no sensible site, or an invalid proposal."""
    data = _structured(
        writer,
        system=(
            "Given a user's goal for their web browser, give the single best https URL to open first. "
            "Prefer the site's homepage or the most direct public page. If no website is implied, set ok to false."
        ),
        packet={"goal": goal, "now": now_context(), "previous_actions": history[-8:]},
        properties={"ok": {"type": "boolean"}, "url": {"type": "string"}, "reason": {"type": "string"}},
        max_tokens=200,
    )
    url = data["url"].strip() if data["ok"] else ""
    return url if valid_url(url) else ""
