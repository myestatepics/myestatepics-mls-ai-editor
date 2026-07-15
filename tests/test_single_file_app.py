from __future__ import annotations

import base64
import importlib.util
import sqlite3
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image


ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def app_module():
    name = "single_file_editor"
    spec = importlib.util.spec_from_file_location(name, ROOT / "myestatepics_ai_editor.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def textured_image(size=(120, 80)):
    width, height = size
    y, x = np.indices((height, width))
    arr = np.stack(
        ((x * 7 + y * 3) % 180 + 30, (x * 5) % 160 + 40, (y * 9) % 170 + 35),
        axis=2,
    ).astype(np.uint8)
    return Image.fromarray(arr)


def make_png(image):
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def configure_tmp(module, tmp_path):
    module.configure_runtime_paths(
        tmp_path / "Incoming",
        tmp_path / "Completed",
        tmp_path / "NeedsReview",
        tmp_path / "Error",
        tmp_path / "Logs",
    )
    module.PROMPT_FILE = ROOT / "legacy" / "myestatepics_mls_interior_prompt_v1_6.txt"
    for directory in (
        module.INPUT_DIR,
        module.OUTPUT_DIR,
        module.REVIEW_DIR,
        module.ERROR_DIR,
        module.LOG_DIR,
        module.DATA_DIR,
    ):
        directory.mkdir(parents=True, exist_ok=True)


def test_mocked_end_to_end_preserves_filename_jpeg_limit_and_exif(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "Living Room.JPG"
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "MyEstatePics"
    image = textured_image()
    image.save(source, format="JPEG", quality=95, exif=exif)
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(make_png(image)).decode())],
        usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    )

    class Images:
        def edit(self, **kwargs):
            assert kwargs["model"] == "gpt-image-2"
            assert kwargs["quality"] == "medium"
            assert kwargs["output_format"] == "png"
            return response

    summary = app_module.process_batch(SimpleNamespace(images=Images()))
    assert summary.completed == 1
    output = app_module.OUTPUT_DIR / source.name
    assert output.exists()
    assert source.exists()
    assert output.stat().st_size <= 2_000_000
    with Image.open(output) as result:
        assert result.format == "JPEG"
        assert result.getexif()[274] == 1
        assert result.getexif()[315] == "MyEstatePics"
    assert summary.total_tokens == 30
    assert summary.log_path and summary.log_path.exists()
    assert app_module.HISTORY_DB.exists()


def test_failed_original_is_retained(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "failed.jpg"
    textured_image().save(source, format="JPEG")

    class Images:
        def edit(self, **kwargs):
            raise ValueError("mock API failure")

    summary = app_module.process_batch(SimpleNamespace(images=Images()))
    assert summary.failed == 1
    assert source.exists()
    assert not (app_module.OUTPUT_DIR / source.name).exists()
    assert not (app_module.REVIEW_DIR / source.name).exists()
    assert (app_module.ERROR_DIR / "failed_error.txt").exists()


def test_review_decision_updates_learning_history(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    app_module.initialize_history_db()
    with sqlite3.connect(app_module.HISTORY_DB) as connection:
        connection.execute(
            """INSERT INTO image_history (
                run_id, processed_at, filename, program_version, prompt_version,
                model, quality, system_decision, implicit_final_label, destination
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("run", "now", "review.jpg", "1.6", "1.6", "gpt-image-2", "medium", "REVIEW", "UNRESOLVED", "NeedsReview"),
        )
        connection.commit()
    app_module.set_review_label("review.jpg", "ACCEPTED")
    with sqlite3.connect(app_module.HISTORY_DB) as connection:
        label = connection.execute(
            "SELECT implicit_final_label FROM image_history WHERE filename = ?",
            ("review.jpg",),
        ).fetchone()[0]
    assert label == "ACCEPTED"


def test_cancel_stops_between_images_without_partial_file(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    for name in ("a.jpg", "b.jpg"):
        textured_image().save(app_module.INPUT_DIR / name, format="JPEG", quality=95)
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(make_png(textured_image())).decode())],
        usage=None,
    )
    calls = 0

    class Images:
        def edit(self, **kwargs):
            nonlocal calls
            calls += 1
            return response

    summary = app_module.process_batch(
        SimpleNamespace(images=Images()), cancel_requested=lambda: calls >= 1
    )
    assert summary.cancelled
    assert calls == 1
    assert not list(tmp_path.rglob("*.tmp"))
