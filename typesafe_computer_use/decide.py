"""The TypeSafe side: state, criteria, and the three-Choice request."""

from __future__ import annotations

from dataclasses import dataclass

from typesafe_sdk import Choice, ChoiceAnswer, Noul, TypeSafeClient

from .config import SITES
from .dates import date_hints, now_context
from .models import Field, Item, Screen

STOP_KINDS = ("done", "none")


def fixed_actions(browser: str, email: str | None) -> dict[str, str]:
    """Deterministic actions offered alongside click_item. Keep them mutually exclusive."""
    actions = {
        "switch_to_browser": (
            f"Bring {browser} to the front to continue with whatever page is already open there. "
            "Not for reaching a specific website: open_site does that on its own, even from another app."
        ),
        "open_site": (
            "Navigate the browser to a website. This is the only way to go to a site: never click the "
            "address bar, a URL, or a search box to get there."
        ),
        "type_text": (
            "Type the message or form value from the goal. Prefer this once the destination "
            "conversation or form is open and the next step is writing. A writer composes the "
            "text, or the explicit words in the goal are used (for example 'hi from jev'). "
            "A focused-field report is often missing on Linux; still type if the composer is on screen."
        ),
        "press_enter": "Press Return to submit the focused form or field.",
        "press_escape": "Press Escape to dismiss a dialog, menu, or popup.",
        "scroll_down": "Scroll down to reveal more of the page.",
        "scroll_up": "Scroll up.",
        "wait": "Nothing to do yet; the screen is still loading or changing.",
        "done": "The goal is already achieved.",
        "none": "Nothing on screen or in this list helps with the goal.",
    }
    if email:
        actions["type_email"] = (
            "Type the user's email address into the focused text field. Use this, not type_text, "
            "whenever the field wants an email or username."
        )
    return actions


def kind_criteria(browser: str, email: str | None) -> dict[str, str]:
    return {"click_item": "Click one of the on-screen text items (chosen in the item question).", **fixed_actions(browser, email)}


def item_criteria(screen: Screen, items: list[Item]) -> dict[str, str]:
    hints = date_hints(items, screen)
    out = {}
    for it in items:
        bits = [screen.region(it)]
        if it.index in hints:
            bits.append(hints[it.index])
        if it.role:
            bits.append(it.role)
        bits.append("clickable control" if it.clickable or it.from_ax else "visible text, not a known control")
        out[str(it.index)] = f"{it.text!r} ({'; '.join(bits)})"
    return out


def site_criteria() -> dict[str, str]:
    return {**SITES, "none": "No website is needed."}


def base_state(
    goal: str,
    screen: Screen,
    items: list[Item],
    history: list[str],
    visible_text: list[str] | None = None,
) -> dict:
    hints = date_hints(items, screen)
    return {
        "goal": goal,
        "now": now_context(),
        "frontmost_app": screen.app,
        "window_title": screen.title,
        "browser_active_tab_url": screen.url,
        "focused_field": screen.field.summary() if screen.field else None,
        "previous_actions": history[-8:],
        "click_targets": [
            {
                "i": it.index,
                "text": it.text,
                "where": screen.region(it),
                "role": it.role or None,
                "source": it.source,
                "clickable": it.clickable or it.from_ax,
                **({"when": hints[it.index]} if it.index in hints else {}),
            }
            for it in items
        ],
        "visible_text": (visible_text or [it.text for it in items])[:80],
    }


@dataclass(frozen=True)
class Decision:
    kind: ChoiceAnswer
    item: ChoiceAnswer | None
    site: ChoiceAnswer

    @property
    def clicking(self) -> bool:
        return self.kind.choice == "click_item" and self.item is not None

    @property
    def chosen(self) -> str:
        return self.item.choice if self.clicking else self.kind.choice

    @property
    def confidence(self) -> float:
        return min(self.kind.confidence, self.item.confidence) if self.clicking else self.kind.confidence

    @property
    def stops(self) -> bool:
        return self.kind.choice in STOP_KINDS


def decide(
    client: TypeSafeClient,
    goal: str,
    screen: Screen,
    items: list[Item],
    history: list[str],
    browser: str,
    email: str | None,
    visible_text: list[str] | None = None,
) -> Decision:
    questions = {
        "kind": Choice(
            instructions=(
                "You are driving this computer one action at a time. Which kind of action "
                "makes the most progress toward the goal right now? Do not repeat an action "
                "that was just taken unless the screen changed. Prefer click_targets that "
                "are clickable controls. Visible text is context, not a click list. "
                "If window_title already shows the destination, do not keep clicking its name. "
                "If window_title is a different person or channel than the goal, do not "
                "type_text yet; open the right conversation first (New message, or their row). "
                "After type_text of the intended message, press_enter to send. Do not type twice. "
                "Never click Save, Deploy, Delete, Remove, or Revoke unless the goal explicitly "
                "asks to write that change; prefer done and hand back what you found."
            ),
            criteria=kind_criteria(browser, email),
        ),
        "site": Choice(instructions="If a website must be opened to progress the goal, which one?", criteria=site_criteria()),
    }
    if items:
        questions["item"] = Choice(
            instructions=(
                "If clicking an on-screen control is the right move, which click_target? "
                "Prefer named buttons, tabs, and entries. Do not pick decorative chrome. "
                "If the goal names a person, prefer their exact row (for example 'Active Josh Nolan') "
                "over a group that merely contains that name."
            ),
            criteria=item_criteria(screen, items),
        )
    answers = client.system_one(
        state=base_state(goal, screen, items, history, visible_text=visible_text), questions=questions
    ).answers
    return Decision(kind=answers["kind"], item=answers.get("item"), site=answers["site"])


def verify_typed(client: TypeSafeClient, goal: str, field_before: Field, typed: str, field_after: Field | None) -> float:
    """Probability that the field now holds a sensible value for its purpose."""
    state = {
        "goal": goal,
        "field": field_before.summary(),
        "text_typed": typed,
        "field_value_now": field_after.value[:300] if field_after else None,
        "field_still_focused": bool(
            field_after and field_after.role == field_before.role and field_after.label == field_before.label
        ),
    }
    question = Noul(
        instructions=(
            "Did the typing succeed: does the field now contain the typed text, and is that "
            "text a sensible value for what this field asks for, given the goal?"
        )
    )
    return client.system_one(state=state, questions={"ok": question}).answers["ok"].noul
