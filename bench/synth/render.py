"""Drawing primitives for synthetic documents. Every drawn field records its bounding box."""

from __future__ import annotations

import random
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT_DIR = Path(__file__).resolve().parents[1] / "fonts"
HANDWRITING = ("Caveat.ttf", "Kalam-Regular.ttf", "PatrickHand-Regular.ttf", "HomemadeApple-Regular.ttf")
SANS, MONO, MONO_BOLD, TYPE, TYPE_BOLD = (
    "IBMPlexSans.ttf", "IBMPlexMono-Regular.ttf", "IBMPlexMono-Bold.ttf", "CourierPrime-Regular.ttf",
    "CourierPrime-Bold.ttf",
)
INK = (25, 25, 30)
BLUE_INK = (20, 40, 120)


@cache
def font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a bundled font (cached)."""
    return ImageFont.truetype(str(FONT_DIR / name), size)


Box = tuple[int, int, int, int]


class Canvas:
    """A page plus a record of where each field was drawn (``boxes[path]``)."""

    def __init__(self, width: int, height: int, rng: random.Random, paper: tuple[int, int, int] = (252, 251, 247)):
        self.img = Image.new("RGB", (width, height), paper)
        self.draw = ImageDraw.Draw(self.img)
        self.rng = rng
        self.boxes: dict[str, Box] = {}

    @property
    def width(self) -> int:
        """Page width in pixels."""
        return self.img.width

    def _record(self, path: str | None, box: Box) -> None:
        if path is None:
            return
        old = self.boxes.get(path)
        self.boxes[path] = box if old is None else (min(old[0], box[0]), min(old[1], box[1]),
                                                   max(old[2], box[2]), max(old[3], box[3]))

    def text(self, xy: tuple[float, float], text: str, fnt: ImageFont.FreeTypeFont, *, fill=INK,
             path: str | None = None, anchor: str = "la") -> Box:
        """Draw printed text."""
        box = self.draw.textbbox(xy, text, font=fnt, anchor=anchor)
        self.draw.text(xy, text, font=fnt, fill=fill, anchor=anchor)
        self._record(path, box)
        return box

    def hand(self, xy: tuple[float, float], text: str, fnt_name: str, size: int, *, fill=BLUE_INK,
             path: str | None = None) -> Box:
        """Draw 'handwritten' text: per-word baseline wobble, size and spacing jitter."""
        x, y = xy
        x0, y0, x1, y1 = int(x), int(y), int(x), int(y)
        for word in text.split(" "):
            if not word:
                x += size * 0.3
                continue
            fnt = font(fnt_name, max(10, int(size * self.rng.uniform(0.92, 1.08))))
            wy = y + self.rng.uniform(-0.06, 0.06) * size
            box = self.draw.textbbox((x, wy), word, font=fnt)
            self.draw.text((x, wy), word, font=fnt, fill=fill)
            x0, y0, x1, y1 = min(x0, box[0]), min(y0, box[1]), max(x1, box[2]), max(y1, box[3])
            x = box[2] + size * self.rng.uniform(0.25, 0.4)
        self._record(path, (x0, y0, x1, y1))
        return (x0, y0, x1, y1)

    def hline(self, y: float, x0: float = 40, x1: float | None = None, width: int = 2, fill=INK) -> None:
        """Horizontal rule."""
        self.draw.line((x0, y, x1 if x1 is not None else self.width - 40, y), fill=fill, width=width)

    def rect(self, box: tuple[float, float, float, float], width: int = 2, fill=None, outline=INK) -> None:
        """Rectangle."""
        self.draw.rectangle(box, fill=fill, outline=outline, width=width)

    def signature(self, x: float, y: float) -> None:
        """A scribble."""
        pts = [(x + i * 9, y + self.rng.uniform(-14, 14)) for i in range(18)]
        self.draw.line(pts, fill=BLUE_INK, width=3, joint="curve")


def money(v: float) -> str:
    """Indian-style grouping: 1,23,456.50."""
    neg = v < 0
    whole, frac = f"{abs(v):.2f}".split(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups) + "," + tail
    return ("-" if neg else "") + whole + "." + frac


def qty_str(q: float) -> str:
    """'10' or '2.5'."""
    return f"{q:g}"
