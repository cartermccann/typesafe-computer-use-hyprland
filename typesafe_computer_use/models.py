"""Data carried between perception, decision, and action."""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

TEXT_ROLES = {"AXTextField", "AXTextArea", "AXSearchField", "AXComboBox"}
Box = tuple[float, float, float, float]  # x1, y1, x2, y2 in capture pixels


class Abort(Exception):
    """Raised when the user triggers an escape hatch."""


@dataclass(frozen=True)
class Item:
    """One on-screen target: OCR text and/or an accessibility control."""

    index: int
    text: str
    ocr_confidence: float
    x1: float
    y1: float
    x2: float
    y2: float
    role: str = ""
    source: str = "ocr"  # ocr | ax | ax+ocr
    clickable: bool = False

    @property
    def center(self) -> tuple[float, float]:
        return (self.x1 + self.x2) / 2, (self.y1 + self.y2) / 2

    @property
    def from_ax(self) -> bool:
        return self.source.startswith("ax")


@dataclass(frozen=True)
class Field:
    """The focused accessibility element, in screen points."""

    role: str
    label: str
    placeholder: str
    value: str
    x: float
    y: float
    w: float
    h: float

    @property
    def is_text(self) -> bool:
        return self.role in TEXT_ROLES

    def summary(self) -> dict:
        return {
            "role": self.role,
            "label": self.label,
            "placeholder": self.placeholder,
            "current_value": self.value[:200],
        }


@dataclass(frozen=True)
class Screen:
    """Everything captured about the display at one instant."""

    image: Image.Image
    scale: float  # capture pixels per screen point
    app: str
    field: Field | None
    url: str | None
    title: str = ""
    window: tuple[float, float, float, float] | None = None  # x, y, w, h in screen points
    address: str = ""

    def region(self, item: Item) -> str:
        cx, cy = item.center
        col = ["left", "center", "right"][min(2, int(3 * cx / self.image.width))]
        row = ["top", "middle", "bottom"][min(2, int(3 * cy / self.image.height))]
        return f"{row}-{col}"

    def to_points(self, item: Item) -> tuple[float, float]:
        cx, cy = item.center
        return cx / self.scale, cy / self.scale
