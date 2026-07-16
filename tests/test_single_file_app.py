from __future__ import annotations

import base64
import csv
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
            assert kwargs["quality"] == "low"
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
    assert summary.quality == "low"
    assert summary.images_processed == 1
    assert summary.elapsed_seconds >= 0
    assert summary.log_path and summary.log_path.exists()
    with summary.log_path.open(newline="", encoding="utf-8") as log_file:
        row = next(csv.DictReader(log_file))
    assert row["filename"] == source.name
    assert row["quality"] == "low"
    assert float(row["processing_time_seconds"]) >= 0
    assert float(row["api_cost"]) > 0
    assert row["destination"] == str(app_module.OUTPUT_DIR)
    assert row["needs_review_reason"] == ""
    assert app_module.HISTORY_DB.exists()


def test_env_overrides_stale_shell_key_and_reload(tmp_path, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "APP_DIR", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-stale-shell-key")
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-project-key-one\n", encoding="utf-8")
    key, message = app_module.load_project_api_key()
    assert key == "sk-project-key-one"
    assert "loaded" in message.lower()

    env_file.write_text("OPENAI_API_KEY=sk-project-key-two\n", encoding="utf-8")
    reloaded_key, _ = app_module.load_project_api_key()
    assert reloaded_key == "sk-project-key-two"


@pytest.mark.parametrize(
    "value",
    ["", "your_openai_api_key_here", "sk-placeholder-key", "sk-***masked***", "not-a-key"],
)
def test_invalid_api_keys_are_rejected(tmp_path, monkeypatch, app_module, value):
    monkeypatch.setattr(app_module, "APP_DIR", tmp_path)
    (tmp_path / ".env").write_text(f"OPENAI_API_KEY={value}\n", encoding="utf-8")
    key, message = app_module.load_project_api_key()
    assert key is None
    assert "invalid" in message.lower() or "empty" in message.lower()


def test_valid_api_key_is_accepted(tmp_path, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "APP_DIR", tmp_path)
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-valid-test-key\n", encoding="utf-8"
    )
    key, _ = app_module.load_project_api_key()
    assert key == "sk-valid-test-key"


def test_medium_quality_reaches_mocked_api(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "medium.jpg"
    image = textured_image()
    image.save(source, format="JPEG", quality=95)
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(make_png(image)).decode())],
        usage=None,
    )
    qualities = []

    class Images:
        def edit(self, **kwargs):
            qualities.append(kwargs["quality"])
            return response

    summary = app_module.process_batch(
        SimpleNamespace(images=Images()), quality="medium"
    )
    assert qualities == ["medium"]
    assert summary.quality == "medium"


def test_selected_file_processing_and_empty_selection_processes_all(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    image = textured_image()
    for name in ("one.jpg", "two.jpg", "three.jpg"):
        image.save(app_module.INPUT_DIR / name, format="JPEG", quality=95)
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(make_png(image)).decode())],
        usage=None,
    )
    calls = []

    class Images:
        def edit(self, **kwargs):
            calls.append(Path(kwargs["image"].name).name)
            return response

    selected = app_module.INPUT_DIR / "two.jpg"
    first = app_module.process_batch(
        SimpleNamespace(images=Images()), selected_files=[selected], quality="low"
    )
    assert first.images_processed == 1
    assert calls == ["two.jpg"]

    calls.clear()
    second = app_module.process_batch(
        SimpleNamespace(images=Images()), selected_files=[], quality="low"
    )
    assert second.images_processed == 2
    assert calls == ["one.jpg", "three.jpg"]


def test_advisory_difference_completes_but_hard_verifier_failure_needs_review(
    tmp_path, monkeypatch, app_module
):
    configure_tmp(app_module, tmp_path)
    image = textured_image()
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(make_png(image)).decode())],
        usage=None,
    )

    class Images:
        def edit(self, **kwargs):
            return response

    advisory = app_module.VerificationResult(
        "REVIEW", ["Ordinary edit difference."], 0.8, 0.1, 0.01, 0.0, 0.0, False
    )
    monkeypatch.setattr(app_module, "compare_images", lambda *args: advisory)
    image.save(app_module.INPUT_DIR / "advisory.jpg", format="JPEG", quality=95)
    summary = app_module.process_batch(SimpleNamespace(images=Images()), quality="low")
    assert summary.completed == 1
    assert summary.review == 0

    failure = app_module.VerificationResult(
        "FAIL", ["Severe normalized sharpness loss."], 0.2, 0.1, 0.01, 0.0, 0.0, False
    )
    monkeypatch.setattr(app_module, "compare_images", lambda *args: failure)
    image.save(app_module.INPUT_DIR / "failure.jpg", format="JPEG", quality=95)
    summary = app_module.process_batch(
        SimpleNamespace(images=Images()), selected_files=[app_module.INPUT_DIR / "failure.jpg"]
    )
    assert summary.review == 1
    assert (app_module.REVIEW_DIR / "failure.jpg").exists()
    with summary.log_path.open(newline="", encoding="utf-8") as log_file:
        row = list(csv.DictReader(log_file))[-1]
    assert row["needs_review_reason"] == "Severe normalized sharpness loss."


def configure_demo(module, tmp_path):
    module.APP_DIR = tmp_path / "Application"
    incoming = tmp_path / "Incoming"
    incoming.mkdir(parents=True)
    module.configure_demo_runtime_paths(incoming)
    return incoming, module.APP_DIR / "runtime" / "Demo"


def test_demo_mode_never_constructs_client_and_cost_is_zero(
    tmp_path, monkeypatch, app_module
):
    incoming, demo_root = configure_demo(app_module, tmp_path)
    for name in ("one.jpg", "two.jpg"):
        textured_image().save(incoming / name, format="JPEG", quality=95)

    def forbidden(*args, **kwargs):
        raise AssertionError("OpenAI client/API code must never run in Demo Mode")

    monkeypatch.setattr(app_module, "OpenAI", forbidden)
    monkeypatch.setattr(app_module, "call_image_editor", forbidden)
    summary = app_module.process_demo_batch(
        selected_files=[incoming / "one.jpg"],
        quality="medium",
        result_mode="All Pass",
        delay_seconds=0,
    )

    assert summary.images_processed == 1
    assert summary.completed == 1
    assert summary.api_calls == 0
    assert summary.fallback_cost == 0
    assert summary.average_cost_per_image == 0
    assert (demo_root / "Completed" / "one.jpg").read_bytes() == (
        incoming / "one.jpg"
    ).read_bytes()
    assert not (demo_root / "Completed" / "two.jpg").exists()
    assert summary.log_path.is_relative_to(demo_root / "Logs")
    with summary.log_path.open(newline="", encoding="utf-8") as log_file:
        row = next(csv.DictReader(log_file))
    assert row["model"] == "DEMO"
    assert row["api_cost"] == "0.000000"
    assert row["fallback_estimated_cost"] == "0.000000"
    assert "DEMO" in row["message"]
    with sqlite3.connect(demo_root / "Data" / "image_history.sqlite3") as connection:
        model, cost, message = connection.execute(
            "SELECT model, fallback_estimated_cost, message FROM image_history"
        ).fetchone()
    assert model == "DEMO"
    assert cost == 0
    assert "DEMO" in message


def test_demo_include_error_routes_pass_review_and_error_only_under_demo(
    tmp_path, app_module
):
    incoming, demo_root = configure_demo(app_module, tmp_path)
    for name in ("a.jpg", "b.jpg", "c.jpg"):
        textured_image().save(incoming / name, format="JPEG", quality=95)

    summary = app_module.process_demo_batch(
        quality="low", result_mode="Include Error", delay_seconds=0
    )

    assert (summary.completed, summary.review, summary.failed) == (1, 1, 1)
    assert (demo_root / "Completed" / "a.jpg").exists()
    assert (demo_root / "NeedsReview" / "b.jpg").exists()
    assert (demo_root / "Error" / "c_error.txt").exists()
    assert summary.fallback_cost == 0
    for path in demo_root.rglob("*"):
        if path.is_file():
            assert path.is_relative_to(demo_root)


def test_accept_moves_demo_review_output_and_updates_demo_history(tmp_path, app_module):
    incoming, demo_root = configure_demo(app_module, tmp_path)
    textured_image().save(incoming / "review.jpg", format="JPEG", quality=95)
    summary = app_module.process_demo_batch(
        result_mode="Some Need Review", delay_seconds=0
    )
    assert summary.review == 1
    review_file = demo_root / "NeedsReview" / "review.jpg"
    assert review_file.exists()

    destination = app_module.accept_review_output("review.jpg")
    assert destination == demo_root / "Completed" / "review.jpg"
    assert destination.exists()
    assert not review_file.exists()
    with sqlite3.connect(demo_root / "Data" / "image_history.sqlite3") as connection:
        label = connection.execute(
            "SELECT implicit_final_label FROM image_history WHERE filename = ?",
            ("review.jpg",),
        ).fetchone()[0]
    assert label == "ACCEPTED"


def test_demo_cancel_stops_between_images(tmp_path, app_module):
    incoming, _ = configure_demo(app_module, tmp_path)
    for name in ("a.jpg", "b.jpg"):
        textured_image().save(incoming / name, format="JPEG", quality=95)
    finished = 0

    def event(kind, payload):
        nonlocal finished
        if kind == "finished":
            finished += 1

    summary = app_module.process_demo_batch(
        result_mode="All Pass",
        cancel_requested=lambda: finished >= 1,
        event=event,
        delay_seconds=0,
    )
    assert summary.cancelled
    assert summary.completed == 1
    assert summary.api_calls == 0


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
