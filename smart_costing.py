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
    """Use Medium only for a meaningful or genuinely ambiguous window opening."""
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
    # Brightness is therefore evidence, but broad room brightness, exposure, or
    # ordinary texture alone must not make every interior cost Medium.
    bright = luminance >= 0.86
    luminance_span = float(np.percentile(luminance, 95) - np.percentile(luminance, 5))
    image_area = bright.size

    def component_kind(mask: np.ndarray) -> str:
        """Classify local bright openings without inferring scene semantics."""
        ambiguous = False
        for area, min_x, min_y, max_x, max_y in _components(mask):
            component_fraction = area / image_area
            box_width = max_x - min_x + 1
            box_height = max_y - min_y + 1
            box_area = box_width * box_height
            fill = area / box_area
            if (
                component_fraction >= 0.065
                and fill >= 0.35
                and max(box_width, box_height) >= 0.28 * max(mask.shape)
            ):
                return WINDOW_PULL_REQUIRED
            if (
                component_fraction >= 0.025
                and fill >= 0.28
                and max(box_width, box_height) >= 0.18 * max(mask.shape)
            ):
                ambiguous = True
        return UNCERTAIN if ambiguous else NO_WINDOW_PULL

    bright_kind = component_kind(bright)
    if bright_kind == WINDOW_PULL_REQUIRED:
        return WindowPullAssessment(
            WINDOW_PULL_REQUIRED,
            "Substantial bright window-like opening detected; exterior recovery is likely valuable.",
        )
    if bright_kind == UNCERTAIN:
        return WindowPullAssessment(
            UNCERTAIN,
            "Medium-sized bright opening is not confirmed as MLS-relevant.",
        )

    # Correctly exposed windows are often not bright enough for the bright
    # threshold. A substantial, clearly separated light opening still merits
    # Medium, while a small incidental opening does not.
    window_candidate = luminance >= 0.55
    candidate_kind = component_kind(window_candidate)
    if candidate_kind == WINDOW_PULL_REQUIRED and luminance_span >= 0.16:
        return WindowPullAssessment(
            WINDOW_PULL_REQUIRED,
            "Substantial window-like opening detected even without blown highlights.",
        )
    if candidate_kind == UNCERTAIN and luminance_span >= 0.16:
        return WindowPullAssessment(
            UNCERTAIN,
            "Medium-sized light opening is not confirmed as MLS-relevant.",
        )

    room_hint = next(
        (
            label
            for label, tokens in {
                "Closet": ("closet",),
                "Hallway": ("hall", "hallway"),
                "Basement": ("basement",),
                "Detail": ("detail",),
                "Garage interior": ("garage",),
            }.items()
            if any(token in filename for token in tokens)
        ),
        "",
    )
    if room_hint:
        return WindowPullAssessment(
            NO_WINDOW_PULL,
            f"{room_hint} has no substantial locally detected window opening.",
        )
    return WindowPullAssessment(
        NO_WINDOW_PULL,
        "No substantial local window-pull indicator detected; Low selected.",
    )


def select_smart_quality(assessment: WindowPullAssessment) -> str:
    """Reserve Medium for a confirmed window pull; Smart never returns High."""
    return "medium" if assessment.classification == WINDOW_PULL_REQUIRED else "low"
