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
    module.PROMPT_FILE = ROOT / "prompts" / "mls_production.txt"
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
        output=[
            SimpleNamespace(
                type="image_generation_call",
                result=base64.b64encode(make_png(image)).decode(),
            )
        ],
        usage={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
    )

    class Responses:
        def create(self, **kwargs):
            assert kwargs["model"] == "gpt-5.6"
            tool = kwargs["tools"][0]
            assert tool["model"] == "gpt-image-2"
            assert tool["quality"] == "low"
            assert tool["output_format"] == "png"
            prompt = kwargs["input"][0]["content"][0]["text"]
            assert "Never create windows inside mirror reflections." in prompt
            assert (
                "Never transform blank walls or bright regions into windows or openings."
                in prompt
            )
            return response

    class Images:
        def edit(self, **kwargs):
            raise AssertionError("Responses must be the primary production path")

    summary = app_module.process_batch(
        SimpleNamespace(responses=Responses(), images=Images())
    )
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
    assert summary.api_calls == 1
    assert summary.fallback_cost > 0
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
    assert row["model"] == "gpt-image-2"
    assert row["destination"] == str(app_module.OUTPUT_DIR)
    assert row["needs_review_reason"] == ""
    assert app_module.HISTORY_DB.exists()


def test_external_production_prompt_preserves_baseline_and_adds_architectural_fidelity(
    app_module,
):
    external_prompt = ROOT / "prompts" / "mls_production.txt"
    legacy_prompt = (
        ROOT / "legacy" / "myestatepics_mls_interior_prompt_v1_6.txt"
    ).read_text(encoding="utf-8").strip()

    assert app_module.PROMPT_FILE == external_prompt
    loaded_prompt = app_module.load_prompt()
    assert loaded_prompt.startswith(legacy_prompt)
    assert loaded_prompt[len(legacy_prompt) :].startswith(
        "\n\nARCHITECTURAL FIDELITY"
    )
    assert "Mirror reflections must remain physically accurate" in loaded_prompt
    assert "Never create windows inside mirror reflections." in loaded_prompt
    assert "Never invent architecture that does not exist." in loaded_prompt
    assert (
        "a physical window frame, an exterior wall boundary, and a visible"
        in loaded_prompt
    )
    assert "Never treat mirrors, shower glass, reflections, cabinet glass" in loaded_prompt
    assert "Never create blue sky unless editing an existing" in loaded_prompt
    assert "leave that region unchanged" in loaded_prompt
    assert (
        "Never transform blank walls or bright regions into windows or openings."
        in loaded_prompt
    )
def test_application_and_prompt_versions_are_independent(app_module):
    assert app_module.PROGRAM_VERSION == "1.0.0"
    assert app_module.PROMPT_VERSION == "1.0"


def test_responses_api_is_primary_image_edit_path(tmp_path, app_module, caplog):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "response-edit.jpg"
    image = textured_image((120, 80))
    image.save(source, format="JPEG", quality=95)
    calls = []

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="image_generation_call",
                        result=base64.b64encode(make_png(image)).decode(),
                    )
                ],
                usage={"input_tokens": 11, "output_tokens": 22, "total_tokens": 33},
            )

    client = SimpleNamespace(responses=Responses())
    with caplog.at_level("INFO"):
        result, requested_size, usage = app_module.call_image_editor(
            client, source, "Edit this image conservatively."
        )

    assert result
    assert requested_size == "1008x672"
    assert usage.total_tokens == 33
    assert len(calls) == 1
    request = calls[0]
    assert request["model"] == "gpt-5.6"
    assert request["tool_choice"] == {"type": "image_generation"}
    assert request["store"] is False
    assert request["input"][0]["content"][1]["detail"] == "original"
    assert request["input"][0]["content"][1]["image_url"].startswith(
        "data:image/jpeg;base64,"
    )
    tool = request["tools"][0]
    assert tool == {
        "type": "image_generation",
        "action": "edit",
        "model": "gpt-image-2",
        "quality": "low",
        "size": "1008x672",
        "output_format": "png",
    }
    assert "api_path=/v1/responses image_generation" in caplog.text
    assert "responses_model=gpt-5.6" in caplog.text
    assert "image_tool_model=gpt-image-2" in caplog.text
    assert "action=edit" in caplog.text
    assert "quality=low" in caplog.text
    assert "requested_size=1008x672" in caplog.text
    assert "returned_size=120x80" in caplog.text
    assert "output_format=png" in caplog.text


@pytest.mark.parametrize(
    "source_size",
    [(1200, 800), (1200, 900), (800, 1200), (1000, 1000)],
)
def test_api_size_preserves_source_aspect_within_official_constraints(
    tmp_path, app_module, source_size
):
    source = tmp_path / "source.jpg"
    textured_image(source_size).save(source, format="JPEG", quality=95)

    requested = app_module.choose_native_size(source)
    width, height = (int(value) for value in requested.split("x"))
    source_ratio = source_size[0] / source_size[1]

    assert width % 16 == 0
    assert height % 16 == 0
    assert max(width, height) <= 3840
    assert 655_360 <= width * height <= 8_294_400
    assert max(width, height) / min(width, height) <= 3
    assert abs((width / height) / source_ratio - 1) < 0.01


def test_api_size_rejects_only_officially_unsupported_panorama(
    tmp_path, app_module
):
    source = tmp_path / "panorama.jpg"
    textured_image((1600, 400)).save(source, format="JPEG", quality=95)

    with pytest.raises(ValueError, match="OpenAI gpt-image-2 limit"):
        app_module.choose_native_size(source)


def test_jpeg_export_starts_at_quality_95_and_reduces_only_when_needed(
    tmp_path, app_module
):
    source = tmp_path / "source.jpg"
    image = textured_image((120, 80))
    image.save(source, format="JPEG", quality=95)

    jpeg_bytes, quality, oversize = app_module.encode_jpeg_under_limit(image, source)

    assert jpeg_bytes
    assert quality == 95
    assert not oversize


def test_jpeg_quality_reduces_only_after_quality_95_exceeds_limit(
    tmp_path, monkeypatch, app_module
):
    source = tmp_path / "source.jpg"
    image = textured_image((320, 240))
    image.save(source, format="JPEG", quality=95)
    monkeypatch.setattr(app_module, "MAX_FILE_SIZE_BYTES", 20_000)

    jpeg_bytes, quality, _ = app_module.encode_jpeg_under_limit(image, source)

    assert jpeg_bytes
    assert quality < 95


def test_folder_scan_auto_loads_supported_images(tmp_path, app_module):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    textured_image().save(incoming / "one.jpg", format="JPEG")
    textured_image().save(incoming / "two.png", format="PNG")
    (incoming / "notes.txt").write_text("ignore", encoding="utf-8")

    found = app_module.scan_supported_images(incoming)

    assert [path.name for path in found] == ["one.jpg", "two.png"]
    assert set(found) == set(found)  # the GUI's initial checked selection


def test_restored_incoming_folder_selects_every_supported_image_by_default(
    tmp_path, app_module
):
    incoming = tmp_path / "Incoming"
    incoming.mkdir()
    for index in range(3):
        textured_image().save(incoming / f"image-{index}.jpg", format="JPEG")
    (incoming / "ignore.txt").write_text("not an image", encoding="utf-8")

    available, selected = app_module.scan_and_select_all(incoming)

    assert len(available) == 3
    assert selected == set(available)
    assert app_module.selected_batch_cost(selected, "low", False) == (
        3 * app_module.LOW_ESTIMATED_COST_PER_IMAGE
    )


def test_checked_selection_drives_count_and_cost(app_module):
    files = [Path("one.jpg"), Path("two.jpg")]
    low = app_module.selected_batch_cost(files[:1], "low", False)
    medium = app_module.selected_batch_cost(files, "medium", False)

    assert low == app_module.LOW_ESTIMATED_COST_PER_IMAGE
    assert medium == 2 * app_module.OBSERVED_ESTIMATED_COST_PER_IMAGE
    assert app_module.selected_batch_cost(files, "medium", True) == 0.0
    assert app_module.selected_batch_cost([], "medium", False) == 0.0


def test_individual_check_uncheck_and_single_selection(app_module):
    first = Path("one.jpg")
    second = Path("two.jpg")
    selected = {first, second}

    selected = app_module.update_checked_selection(selected, second, False)
    assert selected == {first}
    selected = app_module.update_checked_selection(selected, first, False)
    assert selected == set()
    selected = app_module.update_checked_selection(selected, second, True)
    assert selected == {second}


def test_paid_confirmation_is_bypassed_in_demo_mode(app_module):
    assert app_module.requires_paid_confirmation(False)
    assert not app_module.requires_paid_confirmation(True)


@pytest.mark.parametrize(
    "paths, expected_names",
    [
        (("same", "same", "review", "error"), ("Incoming", "Completed")),
        (("incoming", "completed", "same", "same"), ("NeedsReview", "Error")),
        (("incoming", "same", "same", "error"), ("Completed", "NeedsReview")),
    ],
)
def test_folder_conflicts_are_rejected(tmp_path, app_module, paths, expected_names):
    valid, message = app_module.validate_folder_configuration(
        *(tmp_path / name for name in paths)
    )
    assert not valid
    assert all(name in message for name in expected_names)


def test_distinct_folder_configuration_is_valid(tmp_path, app_module):
    valid, message = app_module.validate_folder_configuration(
        *(tmp_path / name for name in ("Incoming", "Completed", "Review", "Error"))
    )
    assert valid
    assert "valid" in message.lower()


def test_folder_paths_and_advanced_state_are_remembered(tmp_path, app_module):
    class Settings:
        def __init__(self):
            self.values = {}

        def value(self, key, default=None):
            return self.values.get(key, default)

        def setValue(self, key, value):
            self.values[key] = value

    settings = Settings()
    defaults = tuple(tmp_path / name for name in ("a", "b", "c", "d", "e"))
    changed = tmp_path / "chosen"
    app_module.save_folder_setting(settings, 2, changed)
    settings.setValue(app_module.ADVANCED_FOLDERS_SETTING, True)

    loaded = app_module.load_folder_settings(settings, defaults)
    assert loaded[2] == changed
    assert loaded[0] == defaults[0]
    assert app_module.load_boolean_setting(
        settings, app_module.ADVANCED_FOLDERS_SETTING
    )


def test_paid_confirmation_summarizes_only_checked_images(app_module):
    text = app_module.paid_confirmation_text(2, "medium", 0.32)
    assert "Images: 2" in text
    assert "Quality: Medium" in text
    assert "Estimated cost: $0.32" in text
    assert "Demo Mode: Off" in text
    assert "Prompt: MLS Production v1.0" in text


def test_retry_confirmation_queues_without_claiming_to_start(app_module):
    text = app_module.retry_confirmation_text("Kitchen.jpg", "low")
    assert "Kitchen.jpg" in text
    assert "Quality: Low" in text
    assert "Estimated additional cost:" in text
    assert "will not start automatically" in text


def test_review_actions_move_accept_and_delete_outputs(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    app_module.initialize_history_db()
    completed = app_module.OUTPUT_DIR / "room.jpg"
    completed.write_bytes(b"result")

    moved = app_module.move_output_to_review(completed.name)
    assert moved == app_module.REVIEW_DIR / completed.name
    assert moved.exists()
    assert not completed.exists()

    accepted = app_module.accept_review_output(moved.name)
    assert accepted == completed
    assert completed.exists()
    assert not moved.exists()

    app_module.delete_active_output(completed.name)
    assert not completed.exists()


def test_shell_environment_key_takes_precedence(tmp_path, monkeypatch, app_module):
    monkeypatch.setattr(app_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell-key")
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-project-key\n", encoding="utf-8")
    key, message = app_module.load_project_api_key()
    assert key == "sk-shell-key"
    assert "loaded" in message.lower()


def test_project_env_key_loads_without_shell_export(tmp_path, monkeypatch, app_module):
    env_file = tmp_path / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-project-key\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    key, message = app_module.load_project_api_key()

    assert key == "sk-project-key"
    assert message == "OpenAI API key loaded"


def test_project_env_load_is_independent_of_working_directory(
    tmp_path, monkeypatch, app_module
):
    project = tmp_path / "project"
    project.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    env_file = project / ".env"
    env_file.write_text("OPENAI_API_KEY=sk-project-key\n", encoding="utf-8")
    monkeypatch.setattr(app_module, "PROJECT_ROOT", project)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(elsewhere)

    key, _ = app_module.load_project_api_key()

    assert key == "sk-project-key"


@pytest.mark.parametrize(
    "value",
    ["", "your_openai_api_key_here", "sk-placeholder-key", "sk-***masked***", "not-a-key"],
)
def test_invalid_api_keys_are_rejected(tmp_path, monkeypatch, app_module, value):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(app_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file.write_text(f"OPENAI_API_KEY={value}\n", encoding="utf-8")
    key, message = app_module.load_project_api_key()
    assert key is None
    assert "OPENAI_API_KEY is missing" in message


def test_valid_api_key_is_accepted(tmp_path, monkeypatch, app_module):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(app_module, "PROJECT_ROOT", tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    env_file.write_text(
        "OPENAI_API_KEY=sk-valid-test-key\n", encoding="utf-8"
    )
    key, _ = app_module.load_project_api_key()
    assert key == "sk-valid-test-key"


def test_missing_key_blocks_production_but_not_demo(app_module):
    assert not app_module.api_key_allows_processing(None, demo_mode=False)
    assert app_module.api_key_allows_processing(None, demo_mode=True)


def test_responses_test_app_copies_legacy_env_once(
    tmp_path, monkeypatch, app_module
):
    legacy = tmp_path / "MyEstatePics AI Editor"
    responses = tmp_path / "MyEstatePics AI Editor - Responses"
    legacy.mkdir()
    legacy_env = legacy / ".env"
    legacy_env.write_text("OPENAI_API_KEY=sk-legacy-test-key\n", encoding="utf-8")
    original = legacy_env.read_bytes()
    monkeypatch.setattr(
        app_module,
        "APPLICATION_NAME",
        app_module.RESPONSES_TEST_APPLICATION_NAME,
    )

    assert app_module.copy_legacy_env_if_needed(responses, legacy)
    assert (responses / ".env").read_bytes() == original
    assert legacy_env.read_bytes() == original
    assert (responses / ".legacy_env_migration_complete").exists()

    (responses / ".env").unlink()
    assert not app_module.copy_legacy_env_if_needed(responses, legacy)
    assert not (responses / ".env").exists()
    assert legacy_env.read_bytes() == original


def test_default_app_never_copies_legacy_env(
    tmp_path, monkeypatch, app_module
):
    legacy = tmp_path / "MyEstatePics AI Editor"
    destination = tmp_path / "default"
    legacy.mkdir()
    (legacy / ".env").write_text(
        "OPENAI_API_KEY=sk-legacy-test-key\n", encoding="utf-8"
    )
    monkeypatch.setattr(
        app_module, "APPLICATION_NAME", app_module.DEFAULT_APPLICATION_NAME
    )

    assert not app_module.copy_legacy_env_if_needed(destination, legacy)
    assert not (destination / ".env").exists()


def test_responses_first_launch_without_legacy_env_never_copies_later(
    tmp_path, monkeypatch, app_module
):
    legacy = tmp_path / "MyEstatePics AI Editor"
    responses = tmp_path / "MyEstatePics AI Editor - Responses"
    legacy.mkdir()
    monkeypatch.setattr(
        app_module,
        "APPLICATION_NAME",
        app_module.RESPONSES_TEST_APPLICATION_NAME,
    )

    assert not app_module.copy_legacy_env_if_needed(responses, legacy)
    (legacy / ".env").write_text(
        "OPENAI_API_KEY=sk-added-later\n", encoding="utf-8"
    )
    assert not app_module.copy_legacy_env_if_needed(responses, legacy)
    assert not (responses / ".env").exists()


def test_packaged_mode_uses_only_application_support_env(
    tmp_path, monkeypatch, app_module
):
    project = tmp_path / "project"
    support = tmp_path / "Application Support" / "MyEstatePics AI Editor"
    project.mkdir()
    support.mkdir(parents=True)
    (project / ".env").write_text(
        "OPENAI_API_KEY=sk-repository-key\n", encoding="utf-8"
    )
    (support / ".env").write_text(
        "OPENAI_API_KEY=sk-packaged-key\n", encoding="utf-8"
    )
    monkeypatch.setattr(app_module, "IS_PACKAGED", True)
    monkeypatch.setattr(app_module, "PROJECT_ROOT", project)
    monkeypatch.setattr(app_module, "USER_DATA_DIR", support)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell-key")

    key, message = app_module.load_project_api_key()

    assert key == "sk-packaged-key"
    assert message == "OpenAI API key loaded"
    assert app_module.api_environment_path() == support / ".env"


def test_packaged_mode_never_falls_back_to_repository_or_shell_key(
    tmp_path, monkeypatch, app_module
):
    project = tmp_path / "project"
    support = tmp_path / "support"
    project.mkdir()
    support.mkdir()
    (project / ".env").write_text(
        "OPENAI_API_KEY=sk-repository-key\n", encoding="utf-8"
    )
    monkeypatch.setattr(app_module, "IS_PACKAGED", True)
    monkeypatch.setattr(app_module, "PROJECT_ROOT", project)
    monkeypatch.setattr(app_module, "USER_DATA_DIR", support)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-shell-key")

    key, message = app_module.load_project_api_key()

    assert key is None
    assert str(support / ".env") in message
    assert "Demo Mode can still be used" in message


def test_api_configuration_log_never_contains_key(
    tmp_path, monkeypatch, caplog, app_module
):
    support = tmp_path / "support"
    support.mkdir()
    secret = "sk-never-log-this-value"
    (support / ".env").write_text(
        f"OPENAI_API_KEY={secret}\n", encoding="utf-8"
    )
    monkeypatch.setattr(app_module, "IS_PACKAGED", True)
    monkeypatch.setattr(app_module, "USER_DATA_DIR", support)

    with caplog.at_level("INFO"):
        key, _ = app_module.load_project_api_key()

    assert key == secret
    assert secret not in caplog.text
    assert "key_found=True" in caplog.text


def test_medium_quality_reaches_mocked_responses_api(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "medium.jpg"
    image = textured_image()
    image.save(source, format="JPEG", quality=95)
    qualities = []

    class Responses:
        def create(self, **kwargs):
            qualities.append(kwargs["tools"][0]["quality"])
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="image_generation_call",
                        result=base64.b64encode(make_png(image)).decode(),
                    )
                ],
                usage=None,
            )

    summary = app_module.process_batch(
        SimpleNamespace(responses=Responses()), quality="medium"
    )
    assert qualities == ["medium"]
    assert summary.quality == "medium"


def test_selected_file_processing_and_empty_selection_processes_all(
    tmp_path, app_module
):
    configure_tmp(app_module, tmp_path)
    image = textured_image()
    for name in ("one.jpg", "two.jpg", "three.jpg"):
        image.save(app_module.INPUT_DIR / name, format="JPEG", quality=95)
    calls = []

    class Responses:
        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="image_generation_call",
                        result=base64.b64encode(make_png(image)).decode(),
                    )
                ],
                usage=None,
            )

    client = SimpleNamespace(responses=Responses())

    selected = app_module.INPUT_DIR / "two.jpg"
    first = app_module.process_batch(
        client, selected_files=[selected], quality="low"
    )
    assert first.images_processed == 1
    assert len(calls) == 1

    calls.clear()
    second = app_module.process_batch(
        client, selected_files=[], quality="low"
    )
    assert second.images_processed == 2
    assert len(calls) == 2


@pytest.mark.parametrize(
    ("directory_name", "expected_status"),
    [
        ("OUTPUT_DIR", "Already exists in Completed"),
        ("REVIEW_DIR", "Already exists in NeedsReview"),
        ("ERROR_DIR", "Already exists in Error"),
    ],
)
def test_current_output_file_skips_source_by_actual_destination(
    tmp_path, app_module, directory_name, expected_status
):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "same-name.jpg"
    textured_image().save(source, format="JPEG", quality=95)
    destination = getattr(app_module, directory_name) / source.name
    destination.write_bytes(source.read_bytes())

    pending, skipped = app_module.pending_images([source])
    assert pending == []
    assert skipped == 1
    assert app_module.selection_status(source) == expected_status


def test_deleted_output_immediately_makes_source_pending(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "retry.jpg"
    textured_image().save(source, format="JPEG", quality=95)
    output = app_module.OUTPUT_DIR / source.name
    output.write_bytes(source.read_bytes())
    assert app_module.pending_images([source]) == ([], 1)
    assert app_module.selection_status(source) == "Already exists in Completed"

    output.unlink()

    pending, skipped = app_module.pending_images([source])
    assert pending == [source.resolve()]
    assert skipped == 0
    assert app_module.selection_status(source) == "Selected — ready"


def test_sqlite_history_alone_never_blocks_reprocessing(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "history-only.jpg"
    textured_image().save(source, format="JPEG", quality=95)
    app_module.initialize_history_db()
    with sqlite3.connect(app_module.HISTORY_DB) as connection:
        connection.execute(
            """INSERT INTO image_history (
                run_id, processed_at, filename, program_version, prompt_version,
                model, quality, system_decision, implicit_final_label, destination
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "old-run",
                "yesterday",
                source.name,
                "1.6",
                "1.6",
                "gpt-image-2",
                "low",
                "PASS",
                "ACCEPTED",
                str(app_module.OUTPUT_DIR),
            ),
        )
        connection.commit()

    assert app_module.pending_images([source]) == ([source.resolve()], 0)


def test_csv_history_alone_never_blocks_reprocessing(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "csv-only.jpg"
    textured_image().save(source, format="JPEG", quality=95)
    app_module.LOG_DIR.mkdir(parents=True, exist_ok=True)
    (app_module.LOG_DIR / "old_run.csv").write_text(
        f"filename,status,destination\n{source.name},PASS,{app_module.OUTPUT_DIR}\n",
        encoding="utf-8",
    )

    assert app_module.pending_images([source]) == ([source.resolve()], 0)


def test_real_and_demo_output_skip_states_are_isolated(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "isolated.jpg"
    textured_image().save(source, format="JPEG", quality=95)
    real_paths = (
        app_module.INPUT_DIR,
        app_module.OUTPUT_DIR,
        app_module.REVIEW_DIR,
        app_module.ERROR_DIR,
        app_module.LOG_DIR,
    )
    real_output = app_module.OUTPUT_DIR / source.name
    real_output.write_bytes(source.read_bytes())
    assert app_module.pending_images([source]) == ([], 1)

    app_module.APP_DIR = tmp_path / "Application"
    app_module.configure_demo_runtime_paths(source.parent)
    assert app_module.pending_images([source]) == ([source.resolve()], 0)
    demo_output = app_module.OUTPUT_DIR / source.name
    demo_output.parent.mkdir(parents=True, exist_ok=True)
    demo_output.write_bytes(source.read_bytes())
    assert app_module.pending_images([source]) == ([], 1)

    app_module.configure_runtime_paths(*real_paths)
    real_output.unlink()
    assert app_module.pending_images([source]) == ([source.resolve()], 0)
    assert demo_output.exists()


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

    class Responses:
        def create(self, **kwargs):
            raise ValueError("mock API failure")

    summary = app_module.process_batch(SimpleNamespace(responses=Responses()))
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


def test_cancel_stops_between_images_without_partial_file(
    tmp_path, app_module
):
    configure_tmp(app_module, tmp_path)
    for name in ("a.jpg", "b.jpg"):
        textured_image().save(app_module.INPUT_DIR / name, format="JPEG", quality=95)
    calls = 0
    image = textured_image()
    cancellation = app_module.CancellationToken()
    events = []

    class Responses:
        def create(self, **kwargs):
            nonlocal calls
            calls += 1
            cancellation.cancel()
            return SimpleNamespace(
                output=[
                    SimpleNamespace(
                        type="image_generation_call",
                        result=base64.b64encode(make_png(image)).decode(),
                    )
                ],
                usage=None,
            )

    summary = app_module.process_batch(
        SimpleNamespace(responses=Responses()),
        cancel_requested=cancellation.is_cancelled,
        event=lambda kind, payload: events.append((kind, payload)),
    )
    assert summary.cancelled
    assert calls == 1
    assert summary.completed == 1
    assert (app_module.OUTPUT_DIR / "a.jpg").exists()
    assert not (app_module.OUTPUT_DIR / "b.jpg").exists()
    assert (app_module.INPUT_DIR / "a.jpg").exists()
    assert (app_module.INPUT_DIR / "b.jpg").exists()
    assert ("cancelled", "b.jpg") in events
    assert not list(tmp_path.rglob("*.tmp"))
