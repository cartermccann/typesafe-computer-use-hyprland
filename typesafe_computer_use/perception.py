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
    return Screen(
        image=image,
        scale=macos.display_scale(image),
        app=app or macos.frontmost_app(),
        field=None if replay else macos.focused_field(),
        url=url if url is not None else (None if replay else macos.browser_url(browser)),
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


def ocr(screen: Screen, budget: int, goal: str) -> list[Item]:
    raw = _ocr_raw(screen.image)
    echoes = goal_echoes(goal)
    lines: list[Line] = [(t.strip(), c, b) for t, c, b in raw if t.strip() and c >= MIN_OCR_CONFIDENCE and not is_echo(t, echoes)]
    return to_items(merge_blocks(lines), budget)


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
