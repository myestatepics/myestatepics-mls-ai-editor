"""Zero-cost, conservative window-pull assessment for V4 Smart quality."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


NO_WINDOW_PULL = "NO_WINDOW_PULL"
WINDOW_PULL_REQUIRED = "WINDOW_PULL_REQUIRED"
UNCERTAIN = "UNCERTAIN"


@dataclass(frozen=True)
class WindowPullAssessment:
    classification: str
    reason: str


def _components(mask: np.ndarray) -> list[tuple[int, int, int, int, int]]:
    """Return 8-connected bright regions as (area, min_x, min_y, max_x, max_y)."""
    height, width = mask.shape
    seen = np.zeros_like(mask, dtype=bool)
    regions = []
    for y, x in np.argwhere(mask):
        if seen[y, x]:
            continue
        stack = [(int(y), int(x))]
        seen[y, x] = True
        area = 0
        min_x = max_x = int(x)
        min_y = max_y = int(y)
        while stack:
            current_y, current_x = stack.pop()
            area += 1
            min_x, max_x = min(min_x, current_x), max(max_x, current_x)
            min_y, max_y = min(min_y, current_y), max(max_y, current_y)
            for next_y in range(max(0, current_y - 1), min(height, current_y + 2)):
                for next_x in range(max(0, current_x - 1), min(width, current_x + 2)):
                    if mask[next_y, next_x] and not seen[next_y, next_x]:
                        seen[next_y, next_x] = True
                        stack.append((next_y, next_x))
        regions.append((area, min_x, min_y, max_x, max_y))
    return regions


def assess_window_pull(image_path: Path) -> WindowPullAssessment:
    """Choose Low only for a clear no-window case; ambiguity intentionally costs Medium."""
    filename = image_path.stem.casefold()
    if any(token in filename for token in ("exterior", "outside", "front", "rear", "backyard", "landscape")):
        return WindowPullAssessment(NO_WINDOW_PULL, "Exterior image; interior window recovery is not applicable.")
    try:
        with Image.open(image_path) as image:
            preview = image.convert("RGB")
            preview.thumbnail((512, 512), Image.Resampling.BILINEAR)
            pixels = np.asarray(preview, dtype=np.float32) / 255.0
    except Exception:
        return WindowPullAssessment(UNCERTAIN, "Image could not be assessed locally; Medium selected for safety.")

    luminance = 0.2126 * pixels[..., 0] + 0.7152 * pixels[..., 1] + 0.0722 * pixels[..., 2]
    # A window may expose correctly while still requiring a stronger MLS pull.
    # Brightness therefore provides positive evidence, never the sole condition
    # for Medium.  We reserve Low for a uniformly dark, low-detail image where
    # there is no meaningful local signal of a window or exterior-view opening.
    bright = luminance >= 0.86
    bright_fraction = float(bright.mean())
    detail = np.abs(np.diff(luminance, axis=0)).mean() + np.abs(
        np.diff(luminance, axis=1)
    ).mean()
    luminance_span = float(np.percentile(luminance, 95) - np.percentile(luminance, 5))

    image_area = bright.size

    def has_large_window_like_component(mask: np.ndarray) -> bool:
        for area, min_x, min_y, max_x, max_y in _components(mask):
            component_fraction = area / image_area
            box_width = max_x - min_x + 1
            box_height = max_y - min_y + 1
            box_area = box_width * box_height
            fill = area / box_area
            if (
                component_fraction >= 0.035
                and fill >= 0.30
                and max(box_width, box_height) >= 0.18 * max(mask.shape)
            ):
                return True
        return False

    if has_large_window_like_component(bright):
        return WindowPullAssessment(
            WINDOW_PULL_REQUIRED,
            "Meaningful bright window-like opening detected; exterior recovery is likely valuable.",
        )

    # Correctly exposed windows are often not bright enough for the previous
    # threshold. A large, clearly separated light opening is still meaningful.
    window_candidate = luminance >= 0.55
    if has_large_window_like_component(window_candidate) and luminance_span >= 0.20:
        return WindowPullAssessment(
            WINDOW_PULL_REQUIRED,
            "Meaningful window-like opening detected even without blown highlights.",
        )

    if bright_fraction >= 0.008 or luminance_span >= 0.20 or detail >= 0.075:
        return WindowPullAssessment(
            UNCERTAIN,
            "Potential window or exterior-view opening cannot be ruled out locally; Medium selected for safety.",
        )
    return WindowPullAssessment(
        NO_WINDOW_PULL,
        "No meaningful window-pull indicators detected in a uniformly low-detail image.",
    )


def select_smart_quality(assessment: WindowPullAssessment) -> str:
    """Low is used only for a clear no-window-pull assessment; never returns High."""
    return "low" if assessment.classification == NO_WINDOW_PULL else "medium"
