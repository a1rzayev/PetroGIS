"""Screen-space label placement with collision culling (level of detail). No Qt dependency.

Labels are placed greedily in input order. Each label tries four positions around its anchor
(right, left, above, below) and takes the first one that doesn't overlap an already-placed
label or another anchor's symbol. If none fits, the label is dropped. Collision checks use a
uniform grid, so each label only tests nearby boxes. When too many anchors are visible, no
labels are drawn at all, because a wall of text is useless and costly to lay out.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray

MAX_VISIBLE_ANCHORS = 5000
"""Above this many visible anchors, labels are hidden entirely."""

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class LabelPlacement:
    indices: NDArray[np.intp]
    """Indices of the labels to draw."""
    x: NDArray[np.float64]
    """Left edge of each drawn label's text box, in pixels."""
    y: NDArray[np.float64]
    """Top edge of each drawn label's text box, in pixels."""

    @property
    def size(self) -> int:
        return int(self.indices.size)


def _empty() -> LabelPlacement:
    return LabelPlacement(np.empty(0, dtype=np.intp), np.empty(0), np.empty(0))


def place_labels(
    px: NDArray[np.float64],
    py: NDArray[np.float64],
    widths: NDArray[np.float64],
    height: float,
    viewport: tuple[float, float, float, float],
    gap: float = 6.0,
    padding: float = 2.0,
    symbol_size: float = 0.0,
) -> LabelPlacement:
    """Choose which labels to draw and where.

    ``px``/``py`` are anchor positions in device pixels (y down). ``widths`` are label widths in
    pixels. ``gap`` is the distance between the anchor and the label. ``viewport`` is
    (left, top, right, bottom). With ``symbol_size`` > 0, every visible anchor's symbol (a square
    of that size) blocks labels, except the label's own symbol.
    """
    left, top, right, bottom = viewport
    visible = np.flatnonzero((px >= left) & (px <= right) & (py >= top) & (py <= bottom))
    if visible.size == 0 or visible.size > MAX_VISIBLE_ANCHORS:
        return _empty()

    cell = max(float(height) * 2.0, 16.0)
    grid: dict[tuple[int, int], list[int]] = {}
    boxes: list[Box] = []

    def cells_of(box: Box) -> list[tuple[int, int]]:
        x0, y0, x1, y1 = box
        return [
            (cx, cy)
            for cx in range(int(x0 // cell), int(x1 // cell) + 1)
            for cy in range(int(y0 // cell), int(y1 // cell) + 1)
        ]

    def add(box: Box) -> int:
        boxes.append(box)
        box_id = len(boxes) - 1
        for c in cells_of(box):
            grid.setdefault(c, []).append(box_id)
        return box_id

    def collides(box: Box, ignore: int) -> bool:
        x0, y0, x1, y1 = box
        for c in cells_of(box):
            for j in grid.get(c, ()):
                if j == ignore:
                    continue
                bx0, by0, bx1, by1 = boxes[j]
                if x0 < bx1 and x1 > bx0 and y0 < by1 and y1 > by0:
                    return True
        return False

    n = visible.size
    ax, ay, aw = px[visible], py[visible], widths[visible]
    symbol_ids = [-1] * n
    if symbol_size > 0:
        half = symbol_size / 2
        symbol_ids = [
            add((ax[k] - half, ay[k] - half, ax[k] + half, ay[k] + half)) for k in range(n)
        ]

    chosen: list[int] = []
    out_x: list[float] = []
    out_y: list[float] = []
    for k in range(n):
        x, y, w = float(ax[k]), float(ay[k]), float(aw[k])
        candidates = (
            (x + gap, y - height / 2),  # right
            (x - gap - w, y - height / 2),  # left
            (x - w / 2, y - gap - height),  # above
            (x - w / 2, y + gap),  # below
        )
        for tx, ty in candidates:
            box = (tx - padding, ty - padding, tx + w + padding, ty + height + padding)
            if not collides(box, ignore=symbol_ids[k]):
                add(box)
                chosen.append(k)
                out_x.append(tx)
                out_y.append(ty)
                break
    return LabelPlacement(
        indices=visible[np.asarray(chosen, dtype=np.intp)],
        x=np.asarray(out_x, dtype=np.float64),
        y=np.asarray(out_y, dtype=np.float64),
    )
