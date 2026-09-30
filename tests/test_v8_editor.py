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

ROOT = Path(__file__).resolve().parents[1]


def load_v8(monkeypatch):
    monkeypatch.setenv("MYESTATEPICS_APPLICATION_NAME", "MyEstatePics V8 Test")
    spec = importlib.util.spec_from_file_location("v8_test", ROOT / "v8_editor.py")
    module = importlib.util.module_from_spec(spec); assert spec.loader
    sys.modules["v8_test"] = module
    spec.loader.exec_module(module); return module


def jpeg(path: Path, color=(100, 120, 140)):
    Image.new("RGB", (120, 80), color).save(path, quality=90)


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


def test_exterior_options_and_twilight_job(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); interior = tmp_path / "i"; exterior = tmp_path / "e"; interior.mkdir(); exterior.mkdir(); jpeg(interior / "inside.jpg"); jpeg(exterior / "outside.jpg")
    monkeypatch.setattr(v8, "finder_tags", lambda path: ("Red",) if path.name == "outside.jpg" else ())
    jobs, heroes = v8.build_jobs(interior, exterior)
    assert [j.kind for j in jobs] == ["interior", "exterior", "twilight"]
    assert jobs[-1].output_name == "outside-TWILIGHT.jpg" and len(heroes) == 1
    assert "Preserve actual grass" in v8.exterior_instruction("Natural", "Keep")
    assert "Conservatively polish" in v8.exterior_instruction("Enhanced", "Remove")


def test_multiple_red_tags_block_before_requests(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); exterior = tmp_path / "e"; exterior.mkdir(); jpeg(exterior / "a.jpg"); jpeg(exterior / "b.jpg")
    monkeypatch.setattr(v8, "finder_tags", lambda _path: ("Red",))
    ok, heroes, message = v8.twilight_preflight(exterior)
    assert not ok and len(heroes) == 2 and "a.jpg" in message and "b.jpg" in message


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
