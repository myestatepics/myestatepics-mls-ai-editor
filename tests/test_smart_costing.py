from __future__ import annotations

import base64
import importlib.util
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from smart_costing import (
    NO_WINDOW_PULL,
    UNCERTAIN,
    WINDOW_PULL_REQUIRED,
    assess_window_pull,
    select_smart_quality,
)


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def app_module():
    spec = importlib.util.spec_from_file_location("smart_cost_editor", ROOT / "myestatepics_ai_editor.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["smart_cost_editor"] = module
    spec.loader.exec_module(module)
    return module


def save_dark(path: Path) -> Image.Image:
    image = Image.new("RGB", (300, 200), (55, 60, 65))
    image.save(path, "JPEG", quality=95)
    return image


def save_significant_window(path: Path) -> Image.Image:
    image = np.full((200, 300, 3), 55, dtype=np.uint8)
    image[25:175, 55:250] = 245
    result = Image.fromarray(image)
    result.save(path, "JPEG", quality=95)
    return result


def save_exposed_meaningful_window(path: Path) -> Image.Image:
    """A meaningful window that is exposed rather than blown out."""
    image = np.full((200, 300, 3), 55, dtype=np.uint8)
    image[25:175, 55:250] = 175
    result = Image.fromarray(image)
    result.save(path, "JPEG", quality=95)
    return result


def save_uncertain(path: Path) -> Image.Image:
    image = np.full((200, 300, 3), 70, dtype=np.uint8)
    # A medium-sized compact bright opening: too consequential to dismiss as
    # incidental, but below the substantial-window threshold.
    image[60:105, 100:160] = 245
    result = Image.fromarray(image)
    result.save(path, "JPEG", quality=95)
    return result


def test_local_window_assessment_reserves_medium_for_confirmed_window_and_never_high(tmp_path):
    dark = tmp_path / "Bedroom.jpg"
    bright = tmp_path / "Kitchen Window.jpg"
    uncertain = tmp_path / "Room.jpg"
    save_dark(dark)
    save_significant_window(bright)
    save_uncertain(uncertain)

    no_window = assess_window_pull(dark)
    required = assess_window_pull(bright)
    unsure = assess_window_pull(uncertain)

    assert no_window.classification == NO_WINDOW_PULL
    assert required.classification == WINDOW_PULL_REQUIRED
    assert unsure.classification == UNCERTAIN
    assert select_smart_quality(no_window) == "low"
    assert select_smart_quality(required) == "medium"
    assert select_smart_quality(unsure) == "low"
    assert "high" not in {select_smart_quality(no_window), select_smart_quality(required), select_smart_quality(unsure)}


def test_production_smart_cost_regressions_route_confirmed_windows_to_medium(tmp_path):
    image_35 = tmp_path / "Production-35.jpg"
    image_38 = tmp_path / "Production-38.jpg"
    image_44 = tmp_path / "Production-44.jpg"
    image_52 = tmp_path / "Production-52.jpg"
    save_significant_window(image_35)
    save_exposed_meaningful_window(image_38)
    save_uncertain(image_44)
    save_dark(image_52)

    assert select_smart_quality(assess_window_pull(image_35)) == "medium"
    assert select_smart_quality(assess_window_pull(image_38)) == "medium"
    assert select_smart_quality(assess_window_pull(image_44)) == "low"
    assert select_smart_quality(assess_window_pull(image_52)) == "low"


def test_clear_no_window_room_types_route_to_low(tmp_path):
    for room_name in ("Closet.jpg", "Hallway.jpg", "Basement.jpg"):
        image_path = tmp_path / room_name
        save_dark(image_path)
        assessment = assess_window_pull(image_path)
        assert assessment.classification == NO_WINDOW_PULL
        assert select_smart_quality(assessment) == "low"


def test_tiny_incidental_bright_area_routes_to_low(tmp_path):
    source = tmp_path / "Bathroom detail.jpg"
    image = np.full((200, 300, 3), 90, dtype=np.uint8)
    image[50:75, 125:150] = 245
    Image.fromarray(image).save(source, "JPEG", quality=95)

    assessment = assess_window_pull(source)

    assert assessment.classification == NO_WINDOW_PULL
    assert select_smart_quality(assessment) == "low"


def test_smart_resolver_maps_no_window_and_uncertain_to_low(tmp_path, app_module):
    dark = tmp_path / "Bedroom.jpg"
    uncertain = tmp_path / "Room.jpg"
    save_dark(dark)
    save_uncertain(uncertain)
    assert app_module.quality_for_image("smart", dark)[0] == "low"
    assert app_module.quality_for_image("smart", uncertain)[0] == "low"


def test_manual_quality_overrides_smart_classifier(tmp_path, app_module):
    source = tmp_path / "Kitchen Window.jpg"
    save_significant_window(source)
    for quality in ("low", "medium", "high"):
        selected, assessment = app_module.quality_for_image(quality, source)
        assert selected == quality
        assert assessment is None
    assert app_module.normalize_quality_mode("auto", "smart") == "smart"
    assert "auto" not in app_module.QUALITY_MODES


def test_smart_batch_makes_one_direct_edit_and_logs_assessment(tmp_path, app_module):
    app_module.configure_runtime_paths(
        tmp_path / "Incoming", tmp_path / "Completed", tmp_path / "NeedsReview",
        tmp_path / "Error", tmp_path / "Logs",
    )
    app_module.PROMPT_FILE = ROOT / "prompts" / "mls_production.txt"
    for directory in (app_module.INPUT_DIR, app_module.OUTPUT_DIR, app_module.REVIEW_DIR, app_module.ERROR_DIR, app_module.LOG_DIR, app_module.DATA_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    app_module.USER_DATA_DIR = tmp_path / "Application Support" / "MyEstatePics AI Editor - Direct"
    source = app_module.INPUT_DIR / "Kitchen Window.jpg"
    returned = save_significant_window(source)
    calls = []

    class Images:
        def edit(self, **kwargs):
            calls.append(kwargs)
            buffer = BytesIO()
            returned.save(buffer, "PNG")
            return SimpleNamespace(data=[SimpleNamespace(b64_json=base64.b64encode(buffer.getvalue()).decode())], usage=None)

    summary = app_module.process_batch(SimpleNamespace(images=Images()), quality="smart")
    assert summary.api_calls == 1
    assert len(calls) == 1
    assert calls[0]["model"] == "gpt-image-2"
    assert calls[0]["quality"] == "medium"
    assert calls[0]["quality"] != "high"
    with summary.log_path.open(encoding="utf-8") as handle:
        header, row = handle.read().splitlines()[:2]
    assert "window_pull_classification" in header
    assert "WINDOW_PULL_REQUIRED" in row
    assert "medium" in row
