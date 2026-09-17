"""Turn the display into text blocks with pixel boxes."""

from __future__ import annotations

from pathlib import Path

from PIL import Image

try:
    from ocrmac import ocrmac as _ocrmac
except ImportError:
    _ocrmac = None

from .platform import load as _load_plat
macos = _load_plat()
from .config import MIN_OCR_CONFIDENCE
from .models import Box, Item, Screen

Line = tuple[str, float, Box]
ECHO_CHARS = 24


def capture(image_path: Path | None = None, app: str | None = None, url: str | None = None, browser: str = "") -> Screen:
    """Capture the main display, or load a saved capture for replay (then app/url are taken as given)."""
    replay = image_path is not None and app is not None
    image = Image.open(image_path).convert("RGB") if image_path else macos.screenshot()
    info = {} if replay else (macos.frontmost_window_info() if hasattr(macos, "frontmost_window_info") else {})
    title = "" if replay else str(info.get("title") or "")
    window = None if replay else info.get("window")
    address = "" if replay else str(info.get("address") or "")
    return Screen(
        image=image,
        scale=macos.display_scale(image),
        app=app or (info.get("class") if info.get("class") else macos.frontmost_app()),
        field=None if replay else macos.focused_field(),
        url=url if url is not None else (None if replay else macos.browser_url(browser)),
        title=title,
        window=window if isinstance(window, tuple) else None,
        address=address,
    )


def goal_echoes(goal: str) -> set[str]:
    """Substrings that identify a screen line as the command that launched this run."""
    norm = " ".join(goal.lower().split())
    return {norm[:ECHO_CHARS], norm[-ECHO_CHARS:]} if len(norm) >= ECHO_CHARS else {norm}


def is_echo(text: str, echoes: set[str]) -> bool:
    norm = " ".join(text.lower().split())
    return any(e in norm for e in echoes)


def _ocr_raw(image: Image.Image) -> list[tuple[str, float, Box]]:
    """macOS: ocrmac. Linux: tesseract image_to_data."""
    if _ocrmac is not None:
        return [(t, c, b) for t, c, b in _ocrmac.OCR(image, recognition_level="accurate").recognize(px=True)]
    try:
        import pytesseract
    except ImportError as e:
        raise RuntimeError("Install pytesseract + tesseract binary for Linux OCR") from e
    data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
    out: list[tuple[str, float, Box]] = []
    n = len(data["text"])
    for i in range(n):
        t = (data["text"][i] or "").strip()
        if not t:
            continue
        conf = float(data["conf"][i])
        if conf < 0:
            continue
        # pytesseract conf is 0-100
        c = conf / 100.0
        x, y, w, h = data["left"][i], data["top"][i], data["width"][i], data["height"][i]
        out.append((t, c, (float(x), float(y), float(x + w), float(y + h))))
    return out


def ocr_region(screen: Screen) -> Box:
    """Frontmost window in capture pixels, or the whole image."""
    width, height = float(screen.image.width), float(screen.image.height)
    if not screen.window:
        return (0.0, 0.0, width, height)
    x, y, w, h = screen.window
    scale = screen.scale or 1.0
    return (
        max(0.0, x * scale),
        max(0.0, y * scale),
        min(width, (x + w) * scale),
        min(height, (y + h) * scale),
    )


def _crop_lines(raw: list[tuple[str, float, Box]], region: Box) -> list[tuple[str, float, Box]]:
    x1, y1, x2, y2 = region
    out = []
    for text, conf, box in raw:
        cx = (box[0] + box[2]) / 2
        cy = (box[1] + box[3]) / 2
        if x1 <= cx <= x2 and y1 <= cy <= y2:
            out.append((text, conf, box))
    return out


def ocr(screen: Screen, budget: int, goal: str) -> list[Item]:
    raw = _ocr_raw(screen.image)
    raw = _crop_lines(raw, ocr_region(screen))
    echoes = goal_echoes(goal)
    lines: list[Line] = [(t.strip(), c, b) for t, c, b in raw if t.strip() and c >= MIN_OCR_CONFIDENCE and not is_echo(t, echoes)]
    return to_items(merge_blocks(lines), budget)


def ax_items(screen: Screen) -> list[Item]:
    """Clickable accessibility controls, in capture pixels."""
    if not hasattr(macos, "window_targets"):
        return []
    targets = macos.window_targets(screen.address)
    scale = screen.scale or 1.0
    items = []
    for i, target in enumerate(targets):
        # Hypruse currently reports a point, not a box. Give Jev a small hit target.
        x, y = float(target.x), float(target.y)
        items.append(
            Item(
                index=i,
                text=target.name,
                ocr_confidence=1.0,
                x1=x * scale,
                y1=y * scale,
                x2=(x + 24) * scale,
                y2=(y + 16) * scale,
                role=target.role,
                source="ax",
                clickable=target.clickable,
            )
        )
    return items


def perceive(screen: Screen, budget: int, goal: str) -> list[Item]:
    """Click targets for Jev: Hypruse/AX controls first. OCR is context, not a click menu.

    If accessibility returns nothing, fall back to window-cropped OCR so the loop is not blind.
    """
    items, _ = perceive_with_context(screen, budget, goal)
    return items


def _tokens(text: str) -> list[str]:
    return [t for t in "".join(ch.lower() if ch.isalnum() else " " for ch in text).split() if len(t) >= 3]


_SKIP_TOKENS = {"active", "with", "from", "page", "button", "open", "more", "the", "and"}


def ax_is_grounded(controls: list[Item], visible_text: list[str], title: str = "") -> bool:
    """False when Hypruse is describing a hidden Slack tab, not the pixels Jev sees."""
    return bool(ground_ax_targets(controls, visible_text, title))


def _jaccard(ax_name: str, ocr_text: str) -> float:
    a = {t for t in _tokens(ax_name) if t not in _SKIP_TOKENS}
    o = {t for t in _tokens(ocr_text) if t not in _SKIP_TOKENS}
    if not a or not o:
        return 0.0
    overlap = a & o
    return len(overlap) / len(a | o) if overlap else 0.0


def ground_ax_targets(
    controls: list[Item],
    visible_text: list[str] | list[Item],
    title: str = "",
    visible_items: list[Item] | None = None,
) -> list[Item]:
    """Keep an AX control only if OCR or the window title can see it.

    Slack/Zen split views publish the whole DM tree while Channels is on
    screen. Those rows have real names and dead coordinates. Prefer the OCR
    box when the label is visible; drop the rest.
    """
    if not controls:
        return []
    items = visible_items or [v for v in visible_text if isinstance(v, Item)]
    texts = [it.text for it in items] if items else [t for t in visible_text if isinstance(t, str)]
    blob = set(_tokens(" ".join([*texts, title])))
    out: list[Item] = []
    for it in controls:
        distinctive = [t for t in _tokens(it.text) if t not in _SKIP_TOKENS]
        best: Item | None = None
        best_score = 0.0
        for ocr_it in items:
            score = _jaccard(it.text, ocr_it.text)
            if score > best_score:
                best, best_score = ocr_it, score
        if best is not None and best_score >= 0.4:
            out.append(
                Item(
                    it.index,
                    it.text,
                    best.ocr_confidence,
                    best.x1,
                    best.y1,
                    best.x2,
                    best.y2,
                    it.role,
                    "ax+ocr",
                    True,
                )
            )
            continue
        if distinctive and all(t in blob for t in distinctive[:2]):
            out.append(it)
    return out


def perceive_with_context(screen: Screen, budget: int, goal: str) -> tuple[list[Item], list[str]]:
    visible = ocr(screen, min(budget, 80), goal)
    visible_text = [it.text for it in visible]
    controls = ax_items(screen)
    grounded = ground_ax_targets(controls, visible_text, screen.title, visible_items=visible)
    if grounded:
        items = [
            Item(i, it.text, it.ocr_confidence, it.x1, it.y1, it.x2, it.y2, it.role, it.source, it.clickable)
            for i, it in enumerate(grounded[:budget])
        ]
        return items, visible_text
    return visible, visible_text


def to_items(lines: list[Line], budget: int) -> list[Item]:
    """Number blocks in reading order: rows by the median line height, then left to right."""
    heights = sorted(b[3] - b[1] for _, _, b in lines) or [1.0]
    row_h = max(1.0, heights[len(heights) // 2])
    ordered = sorted(lines, key=lambda r: (round((r[2][1] + r[2][3]) / 2 / row_h), r[2][0]))
    return [Item(i, t, c, *b) for i, (t, c, b) in enumerate(ordered[:budget])]


def merge_blocks(lines: list[Line]) -> list[Line]:
    """Join lines that continue a block above them: aligned left edge, small gap, similar height."""
    blocks: list[list] = []  # [text, conf, box, last_line_height]
    for text, conf, (x1, y1, x2, y2) in sorted(lines, key=lambda r: (r[2][1], r[2][0])):
        h = y2 - y1
        best = None
        for block in blocks:
            bx1, _, _, by2 = block[2]
            bh = block[3]
            gap = y1 - by2
            continues = abs(x1 - bx1) < 0.6 * bh and -0.2 * bh < gap < 0.8 * bh and 0.7 < h / max(bh, 1) < 1.4
            if continues and (best is None or gap < best[0]):
                best = (gap, block)
        if best is None:
            blocks.append([text, conf, (x1, y1, x2, y2), h])
            continue
        block = best[1]
        bx1, by1, bx2, _ = block[2]
        block[0] = f"{block[0]} {text}"
        block[1] = min(block[1], conf)
        block[2] = (min(bx1, x1), by1, max(bx2, x2), y2)
        block[3] = h
    return [(t, c, b) for t, c, b, _ in blocks]


def near_field(screen: Screen, items: list[Item], radius_pt: float = 160) -> list[str]:
    """Text of items within a radius of the focused field, in screen points."""
    f = screen.field
    if f is None:
        return []
    out = []
    for it in items:
        cx, cy = screen.to_points(it)
        if abs(cx - (f.x + f.w / 2)) < radius_pt + f.w / 2 and abs(cy - (f.y + f.h / 2)) < radius_pt:
            out.append(it.text)
    return out
