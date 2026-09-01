"""Collision-aware point labeling for matplotlib choropleths.

Places a marker + text label for each candidate point, trying a sequence of
offset positions per label and skipping any placement whose text bounding box
would overlap an already-placed label (or a fixed set of pre-existing labels,
e.g. anchor cities placed before this runs). This lets a map surface however
many county/CBSA labels the data actually calls for — not a fixed hand-picked
list — while keeping the plot readable.
"""

from __future__ import annotations

from dataclasses import dataclass

import matplotlib.pyplot as plt

# Offsets tried in priority order: near, readable positions first, then
# farther/less-preferred ones. (dx, dy) in points, plus text alignment.
_OFFSET_CANDIDATES = [
    (6, 6, "left", "bottom"),
    (6, -10, "left", "top"),
    (-6, 6, "right", "bottom"),
    (-6, -10, "right", "top"),
    (10, 0, "left", "center"),
    (-10, 0, "right", "center"),
    (0, 12, "center", "bottom"),
    (0, -12, "center", "top"),
    (14, 14, "left", "bottom"),
    (-14, 14, "right", "bottom"),
    (14, -18, "left", "top"),
    (-14, -18, "right", "top"),
]


@dataclass
class LabelCandidate:
    text: str
    x: float
    y: float
    priority: float  # higher = placed first when space is contested


def _boxes_overlap(b1, b2, pad_px: float = 2.0) -> bool:
    return not (
        b1.x1 + pad_px < b2.x0 or b2.x1 + pad_px < b1.x0
        or b1.y1 + pad_px < b2.y0 or b2.y1 + pad_px < b1.y0
    )


def place_labels(
    fig,
    ax,
    candidates: list[LabelCandidate],
    max_labels: int = 20,
    fontsize: float = 7.5,
    marker_size: float = 3.5,
    reserved_boxes: list | None = None,
) -> int:
    """Place up to max_labels candidates, highest-priority first, skipping any
    whose text would collide with an already-placed label, a marker dot, or a
    caller-supplied reserved region (e.g. the legend box). Returns the number
    of labels actually placed.

    Must be called after the axes' data (choropleth polygons) are drawn, and
    the figure needs a renderer available — this triggers one internally via
    fig.canvas.draw() before measuring text, and again after each accepted
    placement, since matplotlib can only report accurate text extents once a
    draw has happened.
    """
    ordered = sorted(candidates, key=lambda c: -c.priority)
    placed_boxes = list(reserved_boxes or [])
    n_placed = 0

    renderer = fig.canvas.get_renderer()

    for cand in ordered:
        if n_placed >= max_labels:
            break

        accepted_text = None
        accepted_marker = None
        for dx, dy, ha, va in _OFFSET_CANDIDATES:
            marker = ax.plot(cand.x, cand.y, marker="o", markersize=marker_size,
                              color="black", zorder=5, linestyle="none")[0]
            text = ax.annotate(
                cand.text, (cand.x, cand.y), xytext=(dx, dy), textcoords="offset points",
                fontsize=fontsize, fontweight="bold", color="black", zorder=6,
                ha=ha, va=va,
            )
            fig.canvas.draw()
            bbox = text.get_window_extent(renderer=renderer)

            if any(_boxes_overlap(bbox, other) for other in placed_boxes):
                text.remove()
                marker.remove()
                continue

            accepted_text, accepted_marker = text, marker
            placed_boxes.append(bbox)
            # Also reserve the marker's own small footprint so later labels
            # don't get typeset directly on top of an unrelated dot.
            mx0, my0 = ax.transData.transform((cand.x, cand.y))
            marker_pad = marker_size * 1.5
            from matplotlib.transforms import Bbox
            placed_boxes.append(Bbox([[mx0 - marker_pad, my0 - marker_pad],
                                       [mx0 + marker_pad, my0 + marker_pad]]))
            break

        if accepted_text is not None:
            n_placed += 1
        # else: no non-colliding offset found for this candidate at this
        # priority rank — skip it silently rather than clutter the plot;
        # a lower-priority candidate elsewhere may still find room.

    return n_placed
