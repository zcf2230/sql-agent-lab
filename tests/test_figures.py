"""Generated figures must stay readable at the size GitHub renders them.

A screenshot cannot fail this way: text that lands under a whisker is baked in and
looks intentional. A generated SVG can, and the README tells the reader to trust
these bars, so "no label collides with another label or with a line" is a property
worth locking.

Widths are estimated, not measured - CJK glyphs count as one em, ASCII as ~0.56.
The tolerance (3 px) is deliberately loose: the point is to catch a label sitting
*on top of* a whisker, which is what happened to the variant percentages, whose
confidence interval crosses the right end of its own bar by construction.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

import pytest

FIGURES = Path(__file__).resolve().parent.parent / "docs" / "figures"
TEXT_RE = re.compile(
    r'<text x="([\d.]+)" y="([\d.]+)" font-size="([\d.]+)"[^>]*text-anchor="(\w+)"[^>]*>([^<]*)</text>'
)
LINE_RE = re.compile(r'<line x1="([\d.]+)" y1="([\d.]+)" x2="([\d.]+)" y2="([\d.]+)"')
VIEWBOX_RE = re.compile(r'viewBox="0 0 ([\d.]+) ([\d.]+)"')


def text_width(s: str, size: float) -> float:
    total = 0.0
    for ch in s:
        if unicodedata.east_asian_width(ch) in ("W", "F"):
            total += size
        elif ch in " .:,/+-()=→%·":
            total += size * 0.40
        else:
            total += size * 0.56
    return total


def boxes(src: str) -> list[tuple[str, float, float, float, float]]:
    out = []
    for m in TEXT_RE.finditer(src):
        x, y, size, anchor, txt = float(m[1]), float(m[2]), float(m[3]), m[4], m[5]
        w = text_width(txt, size)
        x0 = x - w / 2 if anchor == "middle" else x - w if anchor == "end" else x
        out.append((txt, x0, y - size, x0 + w, y + size * 0.30))
    return out


def segments(src: str) -> list[tuple[float, float, float, float]]:
    return [tuple(float(g) for g in m.groups()) for m in LINE_RE.finditer(src)]  # type: ignore[misc]


def available() -> list[Path]:
    return sorted(FIGURES.glob("*.svg"))


pytestmark = pytest.mark.skipif(not available(), reason="run `python -m sqlagent.figures` first")


@pytest.mark.parametrize("path", available(), ids=lambda p: p.name)
def test_nothing_is_drawn_outside_the_canvas(path):
    src = path.read_text(encoding="utf-8")
    w, h = (float(g) for g in VIEWBOX_RE.search(src).groups())
    for txt, x0, _y0, x1, y1 in boxes(src):
        assert x0 >= -1 and x1 <= w + 1, f"{path.name}: {txt!r} runs past the canvas horizontally"
        assert y1 <= h + 1, f"{path.name}: {txt!r} sits below the canvas and would be clipped"


@pytest.mark.parametrize("path", available(), ids=lambda p: p.name)
def test_labels_do_not_collide_with_each_other(path):
    src = path.read_text(encoding="utf-8")
    bs = boxes(src)
    for i in range(len(bs)):
        for j in range(i + 1, len(bs)):
            a, b = bs[i], bs[j]
            ox = min(a[3], b[3]) - max(a[1], b[1])
            oy = min(a[4], b[4]) - max(a[2], b[2])
            assert not (ox >= 3 and oy >= 3), f"{path.name}: {a[0]!r} overlaps {b[0]!r}"


@pytest.mark.parametrize("path", available(), ids=lambda p: p.name)
def test_labels_do_not_sit_on_a_line(path):
    src = path.read_text(encoding="utf-8")
    for txt, tx0, ty0, tx1, ty1 in boxes(src):
        for lx0, ly0, lx1, ly1 in segments(src):
            if lx0 == lx1:
                hits = tx0 - 2 <= lx0 <= tx1 + 2 and min(ly0, ly1) <= ty1 and max(ly0, ly1) >= ty0
            elif ly0 == ly1:
                hits = ty0 - 2 <= ly0 <= ty1 + 2 and min(lx0, lx1) <= tx1 and max(lx0, lx1) >= tx0
            else:
                hits = False
            assert not hits, f"{path.name}: {txt!r} is crossed by the line ({lx0:.0f},{ly0:.0f})-({lx1:.0f},{ly1:.0f})"
