from __future__ import annotations

import base64
import importlib.util
import os
import plistlib
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]


def load_v8(monkeypatch):
    monkeypatch.setenv("MYESTATEPICS_APPLICATION_NAME", "MyEstatePics V8 Test")
    spec = importlib.util.spec_from_file_location("v8_test", ROOT / "v8_editor.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader
    sys.modules["v8_test"] = module
    spec.loader.exec_module(module); return module


def jpeg(path: Path, color=(100, 120, 140)):
    Image.new("RGB", (120, 80), color).save(path, quality=90)


def v8_window(monkeypatch, tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    v8 = load_v8(monkeypatch); monkeypatch.setattr(v8.core, "USER_DATA_DIR", tmp_path / "support")
    app = QApplication.instance() or QApplication([])
    window = v8.V8Window()
    return v8, app, window


def test_interior_prompt_is_frozen(monkeypatch):
    v8 = load_v8(monkeypatch)
    import hashlib
    assert hashlib.sha256(v8.INTERIOR_PROMPT.read_bytes()).hexdigest() == "065b48e3d0a42fb6dc7a7e13381323859efd0af16b778d1aa0fd00ebce5f4598"
    assert v8.MODEL == "gpt-image-2.5-sunburst"


def test_v8_reads_a_valid_v7_key_without_copying_it(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch)
    legacy = tmp_path / "Library" / "Application Support" / "MyEstatePics AI Editor - V7.0"
    legacy.mkdir(parents=True); (legacy / ".env").write_text("OPENAI_API_KEY=sk-v7-compatible-key\n", encoding="utf-8")
    monkeypatch.setattr(v8.core, "load_project_api_key", lambda: (None, "OPENAI_API_KEY is missing"))
    monkeypatch.setattr(v8.Path, "home", classmethod(lambda cls: tmp_path))
    key, message = v8.load_v8_api_key()
    assert key == "sk-v7-compatible-key" and message == "OpenAI API key loaded"
    assert not (tmp_path / "Library" / "Application Support" / "MyEstatePics AI Editor - V8.0" / ".env").exists()


def test_exterior_options_and_explicit_hero_job(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); interior = tmp_path / "i"; exterior = tmp_path / "e"; interior.mkdir(); exterior.mkdir(); jpeg(interior / "inside.jpg"); jpeg(exterior / "outside.jpg")
    hero = exterior / "outside.jpg"
    jobs, heroes = v8.build_jobs(interior, exterior, hero=hero)
    assert [j.kind for j in jobs] == ["interior", "exterior", "twilight"]
    assert jobs[-1].output_name == "outside-TWILIGHT.jpg" and len(heroes) == 1
    assert "Preserve actual grass" in v8.exterior_instruction("Natural", "Keep")
    assert "Conservatively polish" in v8.exterior_instruction("Enhanced", "Remove")


def test_no_hero_and_finder_metadata_have_zero_twilight_effect(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); exterior = tmp_path / "e"; exterior.mkdir(); jpeg(exterior / "a.jpg"); jpeg(exterior / "b.jpg")
    jobs, heroes = v8.build_jobs(None, exterior)
    assert [job.kind for job in jobs] == ["exterior", "exterior"] and heroes == []
    assert not hasattr(v8, "finder_tags") and not hasattr(v8, "red_tagged_images")


def test_hero_must_be_a_selected_eligible_exterior_file(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); interior = tmp_path / "i"; exterior = tmp_path / "e"; outside = tmp_path / "outside"; interior.mkdir(); exterior.mkdir(); outside.mkdir()
    jpeg(interior / "inside.jpg"); jpeg(exterior / "hero.jpg"); jpeg(outside / "other.jpg"); (exterior / "not-image.txt").write_text("x")
    assert v8.validate_hero_image(interior / "inside.jpg", exterior)[0] is False
    assert v8.validate_hero_image(outside / "other.jpg", exterior)[0] is False
    assert v8.validate_hero_image(exterior / "not-image.txt", exterior)[0] is False
    with pytest.raises(ValueError, match="must also be selected"):
        v8.build_jobs(None, exterior, selected=set(), hero=exterior / "hero.jpg")


def test_explicit_heic_hero_creates_one_additional_twilight_job(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); exterior = tmp_path / "e"; exterior.mkdir(); hero = exterior / "IMG_1060.HEIC"
    Image.new("RGB", (96, 64), (70, 120, 160)).save(hero, format="HEIF")
    jobs, heroes = v8.build_jobs(None, exterior, hero=hero)
    assert [job.kind for job in jobs] == ["exterior", "twilight"]
    assert heroes == [hero.resolve()] and jobs[-1].output_name == "IMG_1060-TWILIGHT.JPG"


@pytest.mark.parametrize("include_interior,include_exterior,expected", [
    (True, False, 1), (False, True, 1), (True, True, 2), (False, False, 0),
])
def test_optional_inputs_build_exactly_the_selected_workflow(monkeypatch, tmp_path, include_interior, include_exterior, expected):
    v8 = load_v8(monkeypatch); interior = tmp_path / "interior"; exterior = tmp_path / "exterior"; interior.mkdir(); exterior.mkdir()
    if include_interior: jpeg(interior / "inside.jpg")
    if include_exterior: jpeg(exterior / "outside.jpg")
    jobs, _heroes = v8.build_jobs(interior if include_interior else None, exterior if include_exterior else None)
    assert len(jobs) == expected


def test_output_validation_requires_a_real_writable_explicit_directory(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); output = tmp_path / "output"; output.mkdir(); file_path = tmp_path / "not-a-directory"; file_path.write_text("x")
    assert v8.validate_output_folder(None)[0] is False
    assert v8.validate_output_folder(tmp_path / "missing")[0] is False
    assert v8.validate_output_folder(file_path)[0] is False
    assert v8.validate_output_folder(output) == (True, "")
    monkeypatch.setattr(v8.os, "access", lambda *_args: False)
    assert v8.validate_output_folder(output)[0] is False


def test_backend_rechecks_output_before_any_api_call(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); interior = tmp_path / "interior"; interior.mkdir(); jpeg(interior / "one.jpg")
    calls = []
    monkeypatch.setattr(v8, "call_v8_image_editor", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(ValueError, match="Select a valid Output folder before processing"):
        v8.process_v8_batch(object(), interior_folder=interior, exterior_folder=None, output_folder=None)
    assert calls == []


def test_output_invalidated_after_batch_start_still_blocks_first_paid_call(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); interior = tmp_path / "interior"; output = tmp_path / "output"; interior.mkdir(); output.mkdir(); jpeg(interior / "one.jpg")
    calls = []; validations = iter([(True, ""), (False, "Select a valid Output folder before processing.")])
    monkeypatch.setattr(v8, "validate_output_folder", lambda _folder: next(validations))
    monkeypatch.setattr(v8, "call_v8_image_editor", lambda *_args, **_kwargs: calls.append(True))
    with pytest.raises(ValueError, match="Select a valid Output folder before processing"):
        v8.process_v8_batch(object(), interior_folder=interior, exterior_folder=None, output_folder=output)
    assert calls == []


def test_output_selection_is_session_only_and_rescan_does_not_change_it(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); interior = tmp_path / "interior"; output = tmp_path / "output"; interior.mkdir(); output.mkdir(); jpeg(interior / "one.jpg")
    # This models a stale preferences value: output is never supplied to the new session.
    assert v8.validate_output_folder(None)[0] is False
    assert len(v8.supported_images(interior)) == 1
    selected_output = output
    assert v8.validate_output_folder(selected_output)[0] is True
    assert len(v8.supported_images(interior)) == 1
    selected_output = None
    assert v8.validate_output_folder(selected_output)[0] is False


def test_choose_hero_autoselects_and_deselecting_or_clearing_removes_it(monkeypatch, tmp_path):
    v8, app, window = v8_window(monkeypatch, tmp_path); exterior = tmp_path / "exterior"; exterior.mkdir(); hero = exterior / "hero.jpg"; jpeg(hero)
    window.exterior = exterior; window.rescan(); window.clear_all()
    monkeypatch.setattr(v8.QFileDialog, "getOpenFileName", lambda *_args: (str(hero), "Images (*.jpg)"))
    window.choose_hero(); app.processEvents()
    assert window.hero_image == hero.resolve() and hero.resolve() in window.selected_files and window.hero.text() == "hero.jpg"
    window.selected_files.discard(hero.resolve()); window.rescan(preserve_empty=True); window.selection_changed(window.images.item(0, 2))
    # Explicitly exercising the checkbox callback's deselection path clears the hero.
    window.images.item(0, 2).setCheckState(v8.Qt.Unchecked); app.processEvents()
    assert window.hero_image is None
    window.hero_image = hero.resolve(); window.clear_hero()
    assert window.hero_image is None and window.hero.text() == "Not selected"
    window.close()


def test_exterior_change_or_missing_hero_clears_hero_but_interior_change_does_not(monkeypatch, tmp_path):
    _v8, _app, window = v8_window(monkeypatch, tmp_path); exterior = tmp_path / "exterior"; replacement = tmp_path / "replacement"; interior = tmp_path / "interior"; exterior.mkdir(); replacement.mkdir(); interior.mkdir(); hero = exterior / "hero.jpg"; jpeg(hero)
    window.exterior = exterior; window.hero_image = hero.resolve(); window.rescan(); window.interior = interior; window.rescan()
    assert window.hero_image == hero.resolve()
    window.exterior = replacement; window.rescan()
    assert window.hero_image is None
    window.close()


def test_snapshot_survives_visible_output_deletion(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); source = tmp_path / "source.jpg"; jpeg(source); cache = v8.ComparisonCache(); job = v8.V8Job(source, "interior")
    cache.snapshot_before(job); cache.snapshot_after(job, source.read_bytes())
    visible = tmp_path / "output.jpg"; visible.write_bytes(source.read_bytes()); visible.unlink()
    before, after = v8.generate_v8_reports(cache, [job], tmp_path)
    assert before.exists() and after.exists() and cache.after[job].exists()
    cache.cleanup()


def test_cancel_stops_before_the_next_image_and_never_calls_api(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); interior = tmp_path / "interior"; output = tmp_path / "output"; interior.mkdir(); output.mkdir()
    jpeg(interior / "one.jpg"); jpeg(interior / "two.jpg")
    calls = []
    monkeypatch.setattr(v8, "call_v8_image_editor", lambda *_args, **_kwargs: (calls.append(True), None)[1])
    events = []
    summary = v8.process_v8_batch(object(), interior_folder=interior, exterior_folder=tmp_path / "none", output_folder=output,
        cancel_requested=lambda: True, event=lambda kind, payload: events.append(kind))
    assert calls == [] and summary.api_calls == 0 and events == ["cancelled"]


@pytest.mark.parametrize("suffix", [".HEIC", ".heic", ".HEIF", ".heif"])
def test_heic_and_heif_discovery_and_normalized_upload(monkeypatch, tmp_path, suffix):
    v8 = load_v8(monkeypatch)
    source = tmp_path / f"iphone{suffix}"
    Image.new("RGB", (96, 64), (70, 120, 160)).save(source, format="HEIF")
    original = source.read_bytes()
    assert source in v8.supported_images(tmp_path)
    upload = v8.prepare_v8_api_upload(source)
    try:
        with Image.open(upload) as normalized:
            assert normalized.format == "JPEG"
            assert normalized.mode == "RGB"
            assert normalized.size == (96, 64)
        assert source.read_bytes() == original
    finally:
        upload.unlink(missing_ok=True)


def test_v8_workflow_controls_remain_usable_at_default_and_minimum_size(monkeypatch, tmp_path):
    _v8, app, window = v8_window(monkeypatch, tmp_path)
    try:
        for width, height in ((1200, 820), (1050, 800)):
            window.resize(width, height); window.show(); app.processEvents()
            assert window.choose_hero_button.isVisible()
            assert window.clear_hero_button.isVisible()
            assert window.choose_hero_button.width() >= 170
            assert window.clear_hero_button.width() >= 78
            assert window.start.isVisible() and window.start.width() >= 230
            assert window.clear_output_button.isVisible()
            assert window.select_all_button.isVisible() and window.clear_all_button.isVisible()
            assert window.images.viewport().height() >= 170
            assert window.choose_hero_button.geometry().bottom() <= window.centralWidget().height()
            assert window.clear_hero_button.geometry().bottom() <= window.centralWidget().height()
    finally:
        window.close()


def test_v8_ui_contains_no_finder_tag_language(monkeypatch, tmp_path):
    _v8, app, window = v8_window(monkeypatch, tmp_path)
    try:
        window.show(); app.processEvents()
        visible_text = "\n".join(label.text() for label in window.findChildren(type(window.hero)))
        assert "FINDER" not in visible_text.upper()
        assert "RED TAG" not in visible_text.upper()
    finally:
        window.close()


def test_v8_ui_rescan_and_selection_support_interior_exterior_and_mixed_jobs(monkeypatch, tmp_path):
    _v8, app, window = v8_window(monkeypatch, tmp_path)
    try:
        interior = tmp_path / "interior"; exterior = tmp_path / "exterior"; output = tmp_path / "output"
        interior.mkdir(); exterior.mkdir(); output.mkdir()
        jpeg(interior / "inside.jpg"); jpeg(exterior / "outside.jpg")
        window.interior = interior; window.output = output; window.rescan(); app.processEvents()
        assert window.images.rowCount() == 1 and len(window.selected_files) == 1 and window.start.isEnabled()
        window.exterior = exterior; window.rescan(); app.processEvents()
        assert window.images.rowCount() == 2 and len(window.selected_files) == 1 and window.start.isEnabled()
        window.select_all(); app.processEvents(); assert len(window.selected_files) == 2
        window.clear_all(); app.processEvents(); assert not window.selected_files and not window.start.isEnabled()
        window.images.item(0, 2).setCheckState(_v8.Qt.Checked); app.processEvents()
        assert len(window.selected_files) == 1
        window.select_all(); app.processEvents(); assert len(window.selected_files) == 2
        window.clear_output(); app.processEvents(); assert window.output is None and not window.start.isEnabled()
    finally:
        window.close()
