from __future__ import annotations

import base64
from io import BytesIO
from types import SimpleNamespace

import numpy as np
from PIL import Image


def save_jpeg(path, size=(80, 60), color=(90, 110, 130), exif=None):
    image = Image.new("RGB", size, color)
    kwargs = {"format": "JPEG"}
    if exif is not None:
        kwargs["exif"] = exif
    image.save(path, **kwargs)


def png_bytes(size=(64, 48), color=(100, 120, 140)):
    buffer = BytesIO()
    Image.new("RGB", size, color).save(buffer, format="PNG")
    return buffer.getvalue()


def test_configuration_and_brightness_limits(legacy_module):
    assert legacy_module.MODEL == "gpt-image-2"
    assert legacy_module.QUALITY == "medium"
    assert legacy_module.MAX_FILE_SIZE_BYTES == 2_000_000
    assert legacy_module.allowed_brightness_shift(0.20) == 0.45
    assert legacy_module.allowed_brightness_shift(0.30) == 0.38
    assert legacy_module.allowed_brightness_shift(0.40) == 0.32
    assert legacy_module.allowed_brightness_shift(0.50) == 0.27
    assert legacy_module.allowed_brightness_shift(0.60) == 0.22


def test_native_size_preserves_orientation(tmp_path, legacy_module):
    landscape = tmp_path / "landscape.jpg"
    portrait = tmp_path / "portrait.jpg"
    square = tmp_path / "square.jpg"
    save_jpeg(landscape, (80, 60))
    save_jpeg(portrait, (60, 80))
    save_jpeg(square, (60, 60))
    assert legacy_module.choose_native_size(landscape) == "1536x1024"
    assert legacy_module.choose_native_size(portrait) == "1024x1536"
    assert legacy_module.choose_native_size(square) == "1024x1024"


def test_chromaticity_ignores_brightness_but_detects_tint(legacy_module):
    rng = np.random.default_rng(20260711)
    source = rng.uniform(0.05, 0.50, (80, 120, 3)).astype(np.float32)
    assert legacy_module.chromaticity_shift(source, source * 1.7) <= 0.001
    tinted = source.copy()
    tinted[..., 0] *= 1.18
    assert legacy_module.chromaticity_shift(source, tinted) >= 0.01


def test_adaptive_prompt_branches(legacy_module):
    metrics = {
        "mean": 0.2,
        "contrast_span": 0.8,
        "shadow_fraction": 0.4,
        "highlight_fraction": 0.01,
        "wb": {"instruction": "- WB marker"},
    }
    text = legacy_module.build_adaptive_addendum(metrics)
    assert "underexposed" in text
    assert "high contrast" in text
    assert "WB marker" in text
    assert "Never tint glass" in text


def test_api_call_parameters_decode_and_usage(tmp_path, monkeypatch, legacy_module):
    source = tmp_path / "same-name.jpg"
    save_jpeg(source)
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes()).decode())],
        usage={"input_tokens": 11, "output_tokens": 22, "total_tokens": 33},
    )

    class Images:
        def __init__(self):
            self.kwargs = None

        def edit(self, **kwargs):
            self.kwargs = kwargs
            return response

    images = Images()
    client = SimpleNamespace(images=images)
    decoded, requested_size, usage = legacy_module.call_image_editor(
        client, source, "prompt"
    )
    assert decoded == png_bytes()
    assert requested_size == "1536x1024"
    assert images.kwargs["model"] == "gpt-image-2"
    assert images.kwargs["quality"] == "medium"
    assert images.kwargs["output_format"] == "png"
    assert usage.total_tokens == 33


def test_transient_api_error_retries_without_sleep(tmp_path, monkeypatch, legacy_module):
    source = tmp_path / "retry.jpg"
    save_jpeg(source)
    response = SimpleNamespace(
        data=[SimpleNamespace(b64_json=base64.b64encode(png_bytes()).decode())],
        usage=None,
    )

    class Temporary(Exception):
        status_code = 503

    calls = []

    class Images:
        def edit(self, **kwargs):
            calls.append(kwargs)
            if len(calls) < 3:
                raise Temporary("later")
            return response

    sleeps = []
    monkeypatch.setattr(legacy_module.time, "sleep", sleeps.append)
    decoded, _, _ = legacy_module.call_image_editor(
        SimpleNamespace(images=Images()), source, "prompt"
    )
    assert decoded == png_bytes()
    assert len(calls) == 3
    assert sleeps == [3, 6]


def test_jpeg_export_preserves_exif_and_size(tmp_path, legacy_module):
    source = tmp_path / "original.jpg"
    exif = Image.Exif()
    exif[274] = 6
    exif[315] = "Photographer"
    save_jpeg(source, exif=exif)
    output = Image.new("RGB", (100, 70), (120, 130, 140))
    data, quality, oversize = legacy_module.encode_jpeg_under_limit(output, source)
    assert len(data) <= 2_000_000
    assert not oversize
    assert quality == 95
    with Image.open(BytesIO(data)) as result:
        assert result.format == "JPEG"
        assert result.getexif()[274] == 1
        assert result.getexif()[315] == "Photographer"
        assert result.getexif()[40962] == 100
        assert result.getexif()[40963] == 70


def test_verifier_passes_identical_image(tmp_path, legacy_module):
    source = tmp_path / "source.jpg"
    y, x = np.indices((80, 120))
    texture = np.stack(
        ((x * 7 + y * 3) % 180 + 30, (x * 5) % 160 + 40, (y * 9) % 170 + 35),
        axis=2,
    ).astype(np.uint8)
    Image.fromarray(texture).save(source, format="JPEG", quality=95)
    with Image.open(source) as image:
        result = legacy_module.compare_images(source, image.convert("RGB"), False)
    assert result.status == "PASS"
    assert result.messages == ["Deterministic checks passed."]


def test_error_report_does_not_move_original(tmp_path, monkeypatch, legacy_module):
    incoming = tmp_path / "Incoming"
    errors = tmp_path / "Error"
    incoming.mkdir()
    source = incoming / "failed.jpg"
    save_jpeg(source)
    monkeypatch.setattr(legacy_module, "ERROR_DIR", errors)
    legacy_module.write_error_report(source, ValueError("mock failure"))
    assert source.exists()
    assert (errors / "failed_error.txt").exists()
