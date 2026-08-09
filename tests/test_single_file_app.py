from __future__ import annotations

import base64
import configparser
import csv
import importlib.util
import inspect
import sqlite3
import sys
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, JpegImagePlugin


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


def test_mocked_end_to_end_preserves_filename_exif_and_quality_100(tmp_path, app_module):
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
            assert "Never create windows inside mirror reflections." in kwargs["prompt"]
            assert (
                "Never transform blank walls or bright regions into windows or openings."
                in kwargs["prompt"]
            )
            assert "Never treat mirrors, shower glass, reflections" in kwargs["prompt"]
            assert "Never create a window." in kwargs["prompt"]
            return response

    summary = app_module.process_batch(SimpleNamespace(images=Images()))
    assert summary.completed == 1
    output = app_module.OUTPUT_DIR / source.name
    assert output.exists()
    assert source.exists()
    with Image.open(output) as result:
        assert result.format == "JPEG"
        assert JpegImagePlugin.get_sampling(result) == 0
        assert all(
            value == 1
            for quantization_table in result.quantization.values()
            for value in quantization_table
        )
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


def test_external_production_prompt_preserves_foundation_and_adds_fidelity_rules(
    app_module,
):
    external_prompt = ROOT / "prompts" / "mls_production.txt"
    assert app_module.PROMPT_FILE == external_prompt
    loaded_prompt = app_module.load_prompt()
    assert loaded_prompt.startswith(
        "MYESTATEPICS MLS INTERIOR — PRODUCTION BASE PROMPT v1.6\n\n"
        "PHOTO CORRECTION ONLY"
    )
    assert "STRICTLY NEUTRAL WHITE BALANCE" in loaded_prompt
    assert "EXPOSURE AND DEPTH" in loaded_prompt
    assert "OUTPUT QUALITY" in loaded_prompt
    assert "ARCHITECTURAL FIDELITY" in loaded_prompt
    assert "Mirror reflections must remain physically accurate" in loaded_prompt
    assert "Never create windows inside mirror reflections." in loaded_prompt
    assert "Never invent architecture that does not exist." in loaded_prompt
    assert (
        "Never transform blank walls or bright regions into windows or openings."
        in loaded_prompt
    )
    assert "Never treat mirrors, shower glass, reflections" in loaded_prompt
    assert "Never create blue sky unless editing an existing" in loaded_prompt
    assert "leave that region unchanged" in loaded_prompt
    assert "strong, natural MLS-quality window pull" in loaded_prompt
    assert "Only when identifiable sky pixels genuinely exist" in loaded_prompt
    assert "Preserve every real exterior object" in loaded_prompt
    assert "Do not allow blue to bleed" in loaded_prompt
    assert "complete exterior view." in loaded_prompt
    assert "Never create a fake window" in loaded_prompt
    assert "outdoor scenery inside a mirror" in loaded_prompt
    assert "HARDWOOD FLOOR CONTINUITY" in loaded_prompt
    assert "WALL AND CEILING CONTINUITY" in loaded_prompt
    assert "MIRROR AND PHOTOGRAPHY-EQUIPMENT REFLECTIONS" in loaded_prompt
    assert "LOCAL MATERIAL HIGHLIGHT PROTECTION" in loaded_prompt
    assert "Do not globally darken the photograph" in loaded_prompt
    assert "preserve the real directional light and natural transition into shade" in loaded_prompt
    assert "Never remove legitimate sunlight" in loaded_prompt
    assert "original paint color" in loaded_prompt
    assert "subtle natural illumination gradient" in loaded_prompt
    assert "Do not add clarity, sharpening, microcontrast" in loaded_prompt
    assert "V3.1 RESTRAINED NATURAL WINDOW SKY" in loaded_prompt
    assert "A subtle, light, naturally" in loaded_prompt
    assert "Avoid royal blue, electric blue, deep blue" in loaded_prompt
    assert "Never create a dramatic AI sky" in loaded_prompt
    assert "Keep the existing strong window pull" in loaded_prompt
    assert "V3.1.1 WINDOW / EXTERIOR FACTUAL FIDELITY" in loaded_prompt
    assert "Never reconstruct, infer, complete, replace, imagine, or invent exterior" in loaded_prompt
    assert "An imperfect window is always preferable to" in loaded_prompt
    assert "mild cloud visibility is permitted only where the corresponding sky pixels" in loaded_prompt
    assert "HARDWOOD FLOOR CONTINUITY" in loaded_prompt
    assert "WALL AND CEILING CONTINUITY" in loaded_prompt


def test_direct_images_edit_is_the_only_production_request(
    tmp_path, app_module, caplog
):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "direct.jpg"
    image = textured_image()
    image.save(source, format="JPEG", quality=95)
    calls = []

    class Images:
        def edit(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[
                    SimpleNamespace(
                        b64_json=base64.b64encode(make_png(image)).decode()
                    )
                ],
                usage=None,
            )

    class Responses:
        def create(self, **kwargs):
            raise AssertionError("Responses API must not be used")

    with caplog.at_level("INFO"):
        result, requested_size, usage = app_module.call_image_editor(
            SimpleNamespace(images=Images(), responses=Responses()),
            source,
            "Edit conservatively.",
        )

    assert result
    assert requested_size == "1536x1024"
    assert usage.total_tokens is None
    assert len(calls) == 1
    request = calls[0]
    assert request["model"] == "gpt-image-2"
    assert request["quality"] == "low"
    assert request["size"] == "1536x1024"
    assert request["output_format"] == "png"
    assert "api_path=/v1/images/edits" in caplog.text
    assert "requested_size=1536x1024" in caplog.text
    assert "returned_size=120x80" in caplog.text
    assert "cost_basis=estimated" in caplog.text
    assert "gpt-5.6" not in caplog.text


def test_application_and_prompt_versions_are_independent(app_module):
    assert app_module.PROGRAM_VERSION == "3.1.1"
    assert app_module.PROMPT_VERSION == "V3.1.1"


def test_final_jpeg_is_quality_100_444_without_a_file_size_limit(tmp_path, app_module):
    source = tmp_path / "source.jpg"
    noise = np.random.default_rng(7).integers(
        0, 256, size=(1600, 2000, 3), dtype=np.uint8
    )
    Image.fromarray(noise).save(source, format="JPEG", quality=95)

    jpeg_bytes, quality = app_module.encode_final_jpeg(Image.fromarray(noise), source)

    assert quality == 100
    assert len(jpeg_bytes) > 2_000_000
    with Image.open(BytesIO(jpeg_bytes)) as result:
        assert result.size == (2000, 1600)
        assert JpegImagePlugin.get_sampling(result) == 0
        assert all(
            value == 1
            for quantization_table in result.quantization.values()
            for value in quantization_table
        )


def test_large_jpeg_is_completed_without_size_based_review(tmp_path, app_module):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "large.jpg"
    noise = np.random.default_rng(11).integers(
        0, 256, size=(1200, 1600, 3), dtype=np.uint8
    )
    Image.fromarray(noise).save(source, format="JPEG", quality=95)
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(make_png(Image.fromarray(noise))).decode())],
        usage=None,
    )

    class Images:
        def __init__(self):
            self.calls = 0

        def edit(self, **kwargs):
            self.calls += 1
            return response

    images = Images()
    summary = app_module.process_batch(SimpleNamespace(images=images))

    output = app_module.OUTPUT_DIR / source.name
    assert images.calls == 1
    assert summary.completed == 1
    assert summary.review == 0
    assert output.exists() and output.stat().st_size > 2_000_000


def test_batch_review_pdfs_are_local_ordered_and_preserve_failed_position(
    tmp_path, app_module
):
    configure_tmp(app_module, tmp_path)
    names = ["zeta.jpg", "alpha.jpg", "middle.jpg"]
    inputs = []
    outputs = {}
    for index, name in enumerate(names):
        source = app_module.INPUT_DIR / name
        textured_image((240 + index, 160 + index)).save(source, format="JPEG")
        inputs.append(source)
        if name != "middle.jpg":
            output = app_module.OUTPUT_DIR / name
            textured_image((240 + index, 160 + index)).save(output, format="JPEG")
            outputs[source.resolve()] = output

    before_pdf, after_pdf = app_module.generate_batch_review_pdfs(
        inputs, outputs, "test-review-pdfs"
    )

    assert before_pdf.name == "MyEstatePics_V3.1.1_BEFORE.pdf"
    assert after_pdf.name == "MyEstatePics_V3.1.1_AFTER.pdf"
    assert before_pdf.parent == after_pdf.parent
    assert before_pdf.read_bytes().startswith(b"%PDF")
    assert after_pdf.read_bytes().startswith(b"%PDF")
    before_text = before_pdf.read_bytes()
    after_text = after_pdf.read_bytes()
    assert before_text.index(b"alpha.jpg") < before_text.index(b"middle.jpg") < before_text.index(b"zeta.jpg")
    assert after_text.index(b"alpha.jpg") < after_text.index(b"middle.jpg") < after_text.index(b"zeta.jpg")
    assert b"PROCESSING FAILED" in after_text


def test_review_pdf_failure_does_not_retry_or_invalidate_completed_output(
    tmp_path, app_module, monkeypatch
):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "pdf-failure.jpg"
    image = textured_image()
    image.save(source, format="JPEG")
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(make_png(image)).decode())],
        usage=None,
    )
    calls = []

    class Images:
        def edit(self, **kwargs):
            calls.append(kwargs)
            return response

    def fail_pdf_generation(*args, **kwargs):
        raise RuntimeError("local PDF test failure")

    monkeypatch.setattr(app_module, "generate_batch_review_pdfs", fail_pdf_generation)
    summary = app_module.process_batch(SimpleNamespace(images=Images()))

    assert len(calls) == 1
    assert summary.completed == 1
    assert (app_module.OUTPUT_DIR / source.name).exists()
    assert "local PDF test failure" in summary.review_pdf_error


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


class IniSettings:
    def __init__(self, path):
        self.parser = configparser.ConfigParser(interpolation=None)
        self.parser.read(path, encoding="utf-8")

    def value(self, key, default=None):
        section, option = key.split("/", 1)
        return self.parser.get(section, option, fallback=default)


def make_primary_folders(tmp_path):
    incoming = tmp_path / "Property-Prefinal"
    completed = tmp_path / "Property-Final"
    incoming.mkdir()
    completed.mkdir()
    return incoming, completed


def test_primary_folder_atomic_round_trip_preserves_roles(tmp_path, app_module):
    incoming, completed = make_primary_folders(tmp_path)
    preferences = tmp_path / "preferences.ini"
    app_module.save_primary_folder_settings(
        preferences, incoming=incoming, completed=completed
    )
    loaded_incoming, loaded_completed, result = app_module.load_primary_folder_settings(
        IniSettings(preferences), incoming, completed
    )
    assert result.valid and not result.warnings
    assert loaded_incoming == incoming
    assert loaded_completed == completed


def test_primary_folder_save_requires_named_arguments(tmp_path, app_module):
    incoming, completed = make_primary_folders(tmp_path)
    with pytest.raises(TypeError):
        app_module.save_primary_folder_settings(
            tmp_path / "preferences.ini", incoming, completed
        )


def test_generic_index_writer_cannot_write_primary_folder_keys(app_module):
    class Settings:
        def setValue(self, key, value):
            raise AssertionError("primary key must not reach generic writer")

    with pytest.raises(ValueError, match="explicit setters"):
        app_module.save_folder_setting(Settings(), 0, Path("Incoming"))
    with pytest.raises(ValueError, match="explicit setters"):
        app_module.save_folder_setting(Settings(), 1, Path("Completed"))


def test_runtime_paths_use_canonical_state_not_widget_text(app_module):
    source = inspect.getsource(app_module.launch_gui)
    assert "configure_runtime_paths(*self.folder_paths)" in source
    assert "configure_runtime_paths(*(Path(label.text())" not in source


def test_incoming_update_cannot_change_completed_value(tmp_path, app_module):
    incoming, completed = make_primary_folders(tmp_path)
    replacement = tmp_path / "Replacement-Prefinal"
    replacement.mkdir()
    preferences = tmp_path / "preferences.ini"
    app_module.save_primary_folder_settings(
        preferences, incoming=incoming, completed=completed
    )
    app_module.save_primary_folder_settings(
        preferences, incoming=replacement, completed=completed
    )
    settings = IniSettings(preferences)
    assert Path(settings.value(app_module.INCOMING_FOLDER_SETTING)) == replacement
    assert Path(settings.value(app_module.COMPLETED_FOLDER_SETTING)) == completed


def test_completed_update_cannot_change_incoming_value(tmp_path, app_module):
    incoming, completed = make_primary_folders(tmp_path)
    replacement = tmp_path / "Replacement-Final"
    replacement.mkdir()
    preferences = tmp_path / "preferences.ini"
    app_module.save_primary_folder_settings(
        preferences, incoming=incoming, completed=completed
    )
    app_module.save_primary_folder_settings(
        preferences, incoming=incoming, completed=replacement
    )
    settings = IniSettings(preferences)
    assert Path(settings.value(app_module.INCOMING_FOLDER_SETTING)) == incoming
    assert Path(settings.value(app_module.COMPLETED_FOLDER_SETTING)) == replacement


def test_identical_primary_folders_are_rejected(tmp_path, app_module):
    folder = tmp_path / "same"
    folder.mkdir()
    result = app_module.validate_primary_folders(folder, folder)
    assert not result.valid
    assert "different" in result.error


def test_michael_taylor_reversal_is_flagged(tmp_path, app_module):
    root = tmp_path / "15060 Michael St Taylor"
    prefinal = root / "15060 Michael St Taylor-Prefinal"
    final = root / "15060 Michael St Taylor-Final"
    prefinal.mkdir(parents=True)
    final.mkdir()
    textured_image().save(prefinal / "B-15060 Michael St Taylor-1.jpg", "JPEG")
    result = app_module.validate_primary_folders(final, prefinal)
    assert result.valid
    assert any("reversed" in warning for warning in result.warnings)
    assert any("no supported images" in warning for warning in result.warnings)


def test_reversed_startup_state_uses_last_known_good_and_reports_warning(
    tmp_path, app_module
):
    incoming, completed = make_primary_folders(tmp_path)
    reversed_incoming = tmp_path / "House-Final"
    reversed_completed = tmp_path / "House-Prefinal"
    reversed_incoming.mkdir()
    reversed_completed.mkdir()
    textured_image().save(reversed_completed / "one.JPG", "JPEG")
    preferences = tmp_path / "preferences.ini"
    app_module._atomic_update_preferences(
        preferences,
        {
            app_module.INCOMING_FOLDER_SETTING: str(reversed_incoming),
            app_module.COMPLETED_FOLDER_SETTING: str(reversed_completed),
            app_module.LAST_GOOD_INCOMING_SETTING: str(incoming),
            app_module.LAST_GOOD_COMPLETED_SETTING: str(completed),
        },
    )
    loaded_incoming, loaded_completed, result = app_module.load_primary_folder_settings(
        IniSettings(preferences), incoming, completed
    )
    assert result.warnings
    assert (loaded_incoming, loaded_completed) == (incoming, completed)


def test_atomic_save_interruption_preserves_original_preferences(
    tmp_path, app_module, monkeypatch
):
    incoming, completed = make_primary_folders(tmp_path)
    preferences = tmp_path / "preferences.ini"
    preferences.write_text("[folders]\nincoming=original\ncompleted=original-out\n")
    original = preferences.read_bytes()

    def interrupted_replace(source, destination):
        raise OSError("simulated interrupted replace")

    monkeypatch.setattr(app_module.os, "replace", interrupted_replace)
    with pytest.raises(OSError, match="interrupted"):
        app_module.save_primary_folder_settings(
            preferences, incoming=incoming, completed=completed
        )
    assert preferences.read_bytes() == original
    assert not list(tmp_path.glob(".preferences.ini.*.tmp"))


def test_loading_valid_preferences_does_not_rewrite_them(tmp_path, app_module):
    incoming, completed = make_primary_folders(tmp_path)
    preferences = tmp_path / "preferences.ini"
    app_module.save_primary_folder_settings(
        preferences, incoming=incoming, completed=completed
    )
    before = preferences.read_bytes()
    app_module.load_primary_folder_settings(IniSettings(preferences), incoming, completed)
    assert preferences.read_bytes() == before


def test_scanning_message_contains_exact_incoming_path(tmp_path, app_module):
    incoming = tmp_path / "Folder With Spaces" / "Incoming"
    incoming.mkdir(parents=True)
    message = app_module.scanning_status_text(incoming, 0)
    assert str(incoming) in message
    assert "0 supported images found" in message


def test_paid_confirmation_summarizes_only_checked_images(app_module):
    text = app_module.paid_confirmation_text(2, "medium", 0.32)
    assert "Images: 2" in text
    assert "Quality: Medium" in text
    assert "Estimated cost: $0.32" in text
    assert "Demo Mode: Off" in text
    assert "Prompt: MLS Production V3" in text


def test_retry_confirmation_queues_without_claiming_to_start(app_module):
    text = app_module.retry_confirmation_text("Kitchen.jpg", "low")
    assert "Kitchen.jpg" in text
    assert "Quality: Low" in text
    assert "Estimated additional cost:" in text
    assert "will not start automatically" in text


@pytest.mark.parametrize(
    "value, expected",
    [
        ("low", "low"),
        ("MEDIUM", "medium"),
        ("HIGH", "high"),
        ("auto", "low"),
        (None, "low"),
    ],
)
def test_v3_quality_normalization_never_allows_auto(app_module, value, expected):
    assert app_module.normalize_quality_setting(value) == expected


@pytest.mark.parametrize(
    "quality",
    ["low", "medium", "high"],
)
def test_each_explicit_v3_quality_makes_one_edit_request(
    tmp_path, app_module, quality
):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / f"{quality}.jpg"
    image = textured_image()
    image.save(source, format="JPEG")
    calls = []

    class Images:
        def edit(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(make_png(image)).decode())],
                usage=None,
            )

    summary = app_module.process_batch(
        SimpleNamespace(images=Images()), quality=quality
    )
    assert summary.api_calls == 1
    assert len(calls) == 1
    assert calls[0]["quality"] == quality
    assert calls[0]["quality"] != "auto"


def test_path_normalization_preserves_commas_and_punctuation(app_module):
    exact = Path(
        "/Users/subratmohapatra/Documents/MyestatePics/2026/29501 brown ct , gardencity/test"
    )
    for value in (str(exact), [str(exact)], (str(exact),)):
        assert app_module.normalize_path_setting(value, Path("/fallback"), "folders/incoming") == exact
    assert app_module.normalize_path_setting(
        ["first", "second"], Path("/fallback"), "folders/incoming"
    ) == Path("/fallback")


def test_primary_folder_settings_use_raw_ini_scalar_for_comma_paths(tmp_path, app_module):
    incoming = tmp_path / "29501 brown ct , gardencity-Prefinal"
    completed = tmp_path / "29501 brown ct , gardencity-Final"
    incoming.mkdir()
    completed.mkdir()
    preferences = tmp_path / "preferences.ini"
    preferences.write_text(
        "[folders]\n"
        f"incoming={incoming}\n"
        f"completed={completed}\n"
        f"last_good_incoming={incoming}\n"
        f"last_good_completed={completed}\n",
        encoding="utf-8",
    )

    class QtListSettings:
        def fileName(self):
            return str(preferences)

        def value(self, key, default):
            return str(default).split(",")

    loaded_incoming, loaded_completed, result = app_module.load_primary_folder_settings(
        QtListSettings(), tmp_path / "fallback-incoming", tmp_path / "fallback-completed"
    )
    assert result.valid
    assert loaded_incoming == incoming
    assert loaded_completed == completed


def test_premium_finish_is_local_and_preserves_dimensions(app_module):
    image = textured_image((121, 79))
    finished = app_module.apply_premium_finish(image)
    assert finished.size == image.size
    assert finished.mode == "RGB"


def test_premium_finish_failure_never_retries_successful_api_response(
    tmp_path, app_module, monkeypatch
):
    configure_tmp(app_module, tmp_path)
    source = app_module.INPUT_DIR / "premium-failure.jpg"
    image = textured_image()
    image.save(source, format="JPEG")
    calls = []

    class Images:
        def edit(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(
                data=[SimpleNamespace(b64_json=base64.b64encode(make_png(image)).decode())],
                usage=None,
            )

    monkeypatch.setattr(
        app_module,
        "apply_premium_finish",
        lambda image: (_ for _ in ()).throw(RuntimeError("local finish failure")),
    )
    summary = app_module.process_batch(SimpleNamespace(images=Images()), quality="low")
    assert len(calls) == 1
    assert summary.api_calls == 1
    assert summary.review == 1
    assert (app_module.REVIEW_DIR / source.name).exists()


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


def test_direct_test_app_copies_legacy_env_once(
    tmp_path, monkeypatch, app_module
):
    legacy = tmp_path / "MyEstatePics AI Editor"
    direct = tmp_path / "MyEstatePics AI Editor - Direct"
    legacy.mkdir()
    legacy_env = legacy / ".env"
    legacy_env.write_text("OPENAI_API_KEY=sk-legacy-test-key\n", encoding="utf-8")
    original = legacy_env.read_bytes()
    monkeypatch.setattr(
        app_module,
        "APPLICATION_NAME",
        app_module.DIRECT_TEST_APPLICATION_NAME,
    )

    assert app_module.copy_legacy_env_if_needed(direct, legacy)
    assert (direct / ".env").read_bytes() == original
    assert legacy_env.read_bytes() == original
    assert (direct / ".legacy_env_migration_complete").exists()

    (direct / ".env").unlink()
    assert not app_module.copy_legacy_env_if_needed(direct, legacy)
    assert not (direct / ".env").exists()
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


def test_direct_first_launch_without_legacy_env_never_copies_later(
    tmp_path, monkeypatch, app_module
):
    legacy = tmp_path / "MyEstatePics AI Editor"
    direct = tmp_path / "MyEstatePics AI Editor - Direct"
    legacy.mkdir()
    monkeypatch.setattr(
        app_module,
        "APPLICATION_NAME",
        app_module.DIRECT_TEST_APPLICATION_NAME,
    )

    assert not app_module.copy_legacy_env_if_needed(direct, legacy)
    (legacy / ".env").write_text(
        "OPENAI_API_KEY=sk-added-later\n", encoding="utf-8"
    )
    assert not app_module.copy_legacy_env_if_needed(direct, legacy)
    assert not (direct / ".env").exists()


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
    calls = 0
    cancellation = app_module.CancellationToken()
    events = []
    image = textured_image()

    class Images:
        def edit(self, **kwargs):
            nonlocal calls
            calls += 1
            cancellation.cancel()
            return SimpleNamespace(
                data=[
                    SimpleNamespace(
                        b64_json=base64.b64encode(make_png(image)).decode()
                    )
                ],
                usage=None,
            )

    summary = app_module.process_batch(
        SimpleNamespace(images=Images()),
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
