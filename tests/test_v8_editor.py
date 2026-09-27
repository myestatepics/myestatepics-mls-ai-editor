from __future__ import annotations

import base64
import importlib.util
import os
import plistlib
import sys
from pathlib import Path
from types import SimpleNamespace

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


def test_snapshot_survives_visible_output_deletion(monkeypatch, tmp_path):
    v8 = load_v8(monkeypatch); source = tmp_path / "source.jpg"; jpeg(source); cache = v8.ComparisonCache(); job = v8.V8Job(source, "interior")
    cache.snapshot_before(job); cache.snapshot_after(job, source.read_bytes())
    visible = tmp_path / "output.jpg"; visible.write_bytes(source.read_bytes()); visible.unlink()
    before, after = v8.generate_v8_reports(cache, [job], tmp_path)
    assert before.exists() and after.exists() and cache.after[job].exists()
    cache.cleanup()
