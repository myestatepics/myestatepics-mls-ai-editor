"""
MyEstatePics MLS Interior Batch Editor — Production v1.6

Workflow:
    Incoming/*.jpg or *.jpeg
            ↓
    Exposure / contrast / conservative white-balance analysis
            ↓
    Fixed MLS prompt + image-specific adaptive instructions
            ↓
    GPT Image 2, medium quality, native output
            ↓
    Conservative verification and optional gentle sharpening
            ↓
    JPEG, same filename, <= 2 MB
            ↓
    Completed/ or NeedsReview/

Important:
- Processes every JPEG in Incoming.
- Keeps originals untouched.
- Skips a file if the same filename already exists in Completed or NeedsReview.
- Resets EXIF orientation to 1 on the generated upright pixels.
- Logs API usage fields when the API returns them.
- Uses the user's observed average cost only as a fallback estimate.
- Automatic sharpening is disabled by default.
- Oversized JPEGs are preserved and routed to NeedsReview instead of discarded.
"""

import base64
import csv
import os
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from openai import OpenAI
from PIL import Image, ImageFilter

from myestatepics_config_v1_6 import (
    DATA_DIR,
    DPI,
    ENABLE_AUTO_SHARPEN,
    ERROR_DIR,
    HISTORY_DB,
    INPUT_DIR,
    JPEG_MIN_QUALITY,
    JPEG_QUALITY_STEP,
    JPEG_START_QUALITY,
    LANDSCAPE_SIZE,
    LOG_DIR,
    MAX_FILE_SIZE_BYTES,
    MAX_GLOBAL_BRIGHTNESS_SHIFT,
    MAX_GLOBAL_CHROMATICITY_SHIFT,
    MAX_HIGHLIGHT_CLIP_FRACTION,
    MAX_RETRIES,
    MAX_SHADOW_CRUSH_FRACTION,
    MODEL,
    PROGRAM_VERSION,
    PROMPT_VERSION,
    NORMALIZED_LONG_EDGE,
    OBSERVED_ESTIMATED_COST_PER_IMAGE,
    OUTPUT_DIR,
    PORTRAIT_SIZE,
    PROMPT_FILE,
    QUALITY,
    RETRY_BASE_DELAY_SECONDS,
    REVIEW_DIR,
    SHARPNESS_AUTO_FIX_MAX_RATIO,
    SHARPNESS_AUTO_FIX_MIN_RATIO,
    SHARPNESS_FAIL_RATIO,
    SHARPNESS_REVIEW_RATIO,
    SQUARE_SIZE,
    SUPPORTED_EXTENSIONS,
    UNSHARP_PERCENT,
    UNSHARP_RADIUS,
    UNSHARP_THRESHOLD,
    WB_CAST_THRESHOLD,
    WB_MAX_CHANNEL_STD,
    WB_MAX_NEUTRAL_SATURATION,
    WB_MAX_VALUE,
    WB_MIN_NEUTRAL_FRACTION,
    WB_MIN_VALUE,
)


@dataclass
class ApiUsage:
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    raw_usage: str = ""


@dataclass
class VerificationResult:
    status: str
    messages: list[str]
    sharpness_ratio: float
    brightness_shift: float
    chromaticity_shift: float
    highlight_clip_fraction: float
    shadow_crush_fraction: float
    sharpened: bool


def load_prompt() -> str:
    if not PROMPT_FILE.exists():
        raise FileNotFoundError(
            f"Prompt file not found:\n{PROMPT_FILE}\n\n"
            "Keep myestatepics_mls_interior_prompt_v1_6.txt in the automation folder."
        )

    prompt = PROMPT_FILE.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError(f"Prompt file is empty: {PROMPT_FILE}")
    return prompt


def choose_native_size(input_file: Path) -> str:
    with Image.open(input_file) as image:
        width, height = image.size

    if width > height:
        return LANDSCAPE_SIZE
    if height > width:
        return PORTRAIT_SIZE
    return SQUARE_SIZE


def image_to_rgb_array(image: Image.Image, max_edge: int) -> np.ndarray:
    rgb = image.convert("RGB")
    width, height = rgb.size
    scale = min(1.0, max_edge / max(width, height))

    if scale < 1.0:
        rgb = rgb.resize(
            (max(1, round(width * scale)), max(1, round(height * scale))),
            Image.Resampling.LANCZOS,
        )

    return np.asarray(rgb, dtype=np.float32) / 255.0


def luminance_from_rgb(arr: np.ndarray) -> np.ndarray:
    return (
        0.2126 * arr[..., 0]
        + 0.7152 * arr[..., 1]
        + 0.0722 * arr[..., 2]
    )


def robust_mean_chromaticity(arr: np.ndarray) -> np.ndarray:
    """
    Return robust per-pixel RGB chromaticity, independent of uniform brightness.

    For each usable pixel:
        r = R / (R+G+B)
        g = G / (R+G+B)
        b = B / (R+G+B)

    Multiplying R, G, and B by the same exposure factor leaves these ratios
    unchanged, provided the channels are not clipped. Very dark and nearly
    clipped pixels are excluded because their ratios are unstable or distorted.
    """
    rgb = np.clip(arr, 0.0, 1.0)
    channel_sum = rgb.sum(axis=2)
    max_channel = rgb.max(axis=2)
    luminance = luminance_from_rgb(rgb)

    usable = (
        (channel_sum > 0.12)
        & (luminance > 0.05)
        & (luminance < 0.92)
        & (max_channel < 0.98)
    )

    if not np.any(usable):
        usable = channel_sum > 1e-6

    pixels = rgb[usable]
    sums = pixels.sum(axis=1, keepdims=True)
    chroma = pixels / np.maximum(sums, 1e-9)

    # Median is robust to windows, lamps, and large saturated objects.
    return np.median(chroma, axis=0).astype(np.float32)


def chromaticity_shift(source_arr: np.ndarray, output_arr: np.ndarray) -> float:
    """
    Brightness-independent global color-drift signal.

    This metric ignores overall luminance and compares only normalized RGB
    channel proportions.
    """
    source_chroma = robust_mean_chromaticity(source_arr)
    output_chroma = robust_mean_chromaticity(output_arr)
    return float(np.linalg.norm(output_chroma - source_chroma))


def normalized_edge_sharpness(arr: np.ndarray) -> float:
    luminance = luminance_from_rgb(arr)
    gy, gx = np.gradient(luminance)
    edge_energy = gx * gx + gy * gy
    return float(np.mean(edge_energy))


def analyze_white_balance(arr: np.ndarray) -> dict[str, Any]:
    """
    Conservative neutral-candidate analysis.

    It only produces an adaptive cast instruction when:
    - enough low-saturation, moderate-value pixels exist;
    - those candidates are reasonably consistent;
    - the channel imbalance is large enough to matter.

    Otherwise it returns no image-specific white-balance conclusion.
    """
    max_c = arr.max(axis=2)
    min_c = arr.min(axis=2)
    saturation = np.zeros_like(max_c)
    valid_max = max_c > 1e-6
    saturation[valid_max] = (
        (max_c[valid_max] - min_c[valid_max]) / max_c[valid_max]
    )
    value = max_c

    candidate_mask = (
        (saturation <= WB_MAX_NEUTRAL_SATURATION)
        & (value >= WB_MIN_VALUE)
        & (value <= WB_MAX_VALUE)
    )

    candidate_fraction = float(candidate_mask.mean())
    if candidate_fraction < WB_MIN_NEUTRAL_FRACTION:
        return {
            "confidence": "low",
            "instruction": "",
            "candidate_fraction": candidate_fraction,
            "cast": "undetermined",
        }

    candidates = arr[candidate_mask]
    channel_mean = candidates.mean(axis=0)
    channel_std = float(candidates.std(axis=0).mean())

    if channel_std > WB_MAX_CHANNEL_STD:
        return {
            "confidence": "low",
            "instruction": "",
            "candidate_fraction": candidate_fraction,
            "cast": "undetermined",
        }

    r, g, b = [float(x) for x in channel_mean]
    neutral_mean = (r + g + b) / 3.0
    deviations = {"red": r - neutral_mean, "green": g - neutral_mean, "blue": b - neutral_mean}
    dominant = max(deviations, key=lambda key: abs(deviations[key]))
    magnitude = abs(deviations[dominant])

    if magnitude < WB_CAST_THRESHOLD:
        return {
            "confidence": "high",
            "instruction": (
                "- Neutral-candidate pixels do not show a strong global cast. "
                "Keep white balance neutral and make no aggressive warm/cool correction."
            ),
            "candidate_fraction": candidate_fraction,
            "cast": "none",
        }

    if deviations[dominant] > 0:
        cast_map = {
            "red": "possible warm/red cast",
            "green": "possible green cast",
            "blue": "possible blue/cyan cast",
        }
    else:
        cast_map = {
            "red": "possible cyan cast",
            "green": "possible magenta cast",
            "blue": "possible yellow/warm cast",
        }

    cast = cast_map[dominant]
    instruction = (
        f"- High-confidence neutral-surface analysis indicates a {cast}. "
        "Correct that cast conservatively on genuinely neutral surfaces only. "
        "Do not neutralize beige, cream, greige, wood, carpet, tile, furniture, or fixtures."
    )

    return {
        "confidence": "high",
        "instruction": instruction,
        "candidate_fraction": candidate_fraction,
        "cast": cast,
    }


def analyze_input(input_file: Path) -> dict[str, Any]:
    with Image.open(input_file) as image:
        arr = image_to_rgb_array(image, 1200)

    luminance = luminance_from_rgb(arr)
    p10 = float(np.percentile(luminance, 10))
    p90 = float(np.percentile(luminance, 90))
    wb = analyze_white_balance(arr)

    return {
        "mean": float(luminance.mean()),
        "contrast_span": p90 - p10,
        "shadow_fraction": float((luminance < 0.18).mean()),
        "highlight_fraction": float((luminance > 0.92).mean()),
        "sharpness": normalized_edge_sharpness(arr),
        "wb": wb,
    }


def build_adaptive_addendum(metrics: dict[str, Any]) -> str:
    lines = ["IMAGE-SPECIFIC ADAPTIVE INSTRUCTIONS:"]

    mean = metrics["mean"]
    contrast_span = metrics["contrast_span"]
    shadow_fraction = metrics["shadow_fraction"]
    highlight_fraction = metrics["highlight_fraction"]

    if mean < 0.38 or shadow_fraction > 0.30:
        lines.append(
            "- This room is underexposed. Recover shadows and midtones moderately "
            "until it looks naturally bright, while retaining realistic black depth."
        )
    elif mean > 0.62 or highlight_fraction > 0.16:
        lines.append(
            "- This room is already bright. Protect white surfaces and window "
            "highlights; do not add unnecessary exposure."
        )
    else:
        lines.append(
            "- Exposure is close to usable. Apply only a restrained MLS brightness correction."
        )

    if contrast_span > 0.72:
        lines.append(
            "- The scene has high contrast. Balance room shadows and window highlights "
            "without flattening the room or creating HDR tonality."
        )
    elif contrast_span < 0.42:
        lines.append(
            "- The scene is relatively flat. Add restrained local contrast while "
            "preserving natural shadows and material texture."
        )
    else:
        lines.append("- Preserve the existing natural contrast structure and depth.")

    wb_instruction = metrics["wb"].get("instruction", "")
    if wb_instruction:
        lines.append(wb_instruction)
    else:
        lines.append(
            "- White balance must remain neutral. No reliable image-specific cast "
            "measurement was available, so avoid aggressive warm or cool correction."
        )

    lines.append(
        "- Genuine open sky visible through windows must appear soft natural light blue. "
        "Never tint glass, brick, roofs, buildings, trees, grass, frames, reflections, "
        "or interior surfaces blue."
    )

    return "\n".join(lines)


def is_transient_error(error: Exception) -> bool:
    status_code = getattr(error, "status_code", None)
    if status_code in {429, 500, 502, 503, 504}:
        return True

    return type(error).__name__ in {
        "RateLimitError",
        "APIConnectionError",
        "InternalServerError",
        "APITimeoutError",
    }


def get_value(obj: Any, key: str) -> Any:
    if obj is None:
        return None
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def extract_usage(response: Any) -> ApiUsage:
    usage = get_value(response, "usage")
    if usage is None:
        return ApiUsage(raw_usage="")

    input_tokens = get_value(usage, "input_tokens")
    output_tokens = get_value(usage, "output_tokens")
    total_tokens = get_value(usage, "total_tokens")

    try:
        raw_usage = usage.model_dump_json()
    except Exception:
        raw_usage = str(usage)

    return ApiUsage(
        input_tokens=int(input_tokens) if input_tokens is not None else None,
        output_tokens=int(output_tokens) if output_tokens is not None else None,
        total_tokens=int(total_tokens) if total_tokens is not None else None,
        raw_usage=raw_usage,
    )


def call_image_editor(
    client: OpenAI,
    input_file: Path,
    full_prompt: str,
) -> tuple[bytes, str, ApiUsage]:
    requested_size = choose_native_size(input_file)
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            with input_file.open("rb") as image_file:
                response = client.images.edit(
                    model=MODEL,
                    image=image_file,
                    prompt=full_prompt,
                    size=requested_size,
                    quality=QUALITY,
                    output_format="png",
                )

            image_bytes = base64.b64decode(response.data[0].b64_json)
            usage = extract_usage(response)
            return image_bytes, requested_size, usage

        except Exception as error:
            last_error = error
            if is_transient_error(error) and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                print(
                    f"    Temporary API error. Retrying in {delay} seconds "
                    f"({attempt}/{MAX_RETRIES})..."
                )
                time.sleep(delay)
                continue
            raise

    if last_error is not None:
        raise last_error

    raise RuntimeError("Image edit failed without an API error.")


def get_preserved_exif(input_file: Path, output_size: tuple[int, int]) -> bytes | None:
    """
    Preserve readable EXIF metadata, but reset Orientation to 1 because the
    generated pixels are already upright. Refresh EXIF pixel dimensions.
    """
    with Image.open(input_file) as image:
        exif = image.getexif()

    if not exif:
        return None

    exif[274] = 1  # Orientation
    exif[40962] = output_size[0]  # PixelXDimension
    exif[40963] = output_size[1]  # PixelYDimension

    try:
        return exif.tobytes()
    except Exception:
        return None


def get_icc_profile(input_file: Path) -> bytes | None:
    with Image.open(input_file) as image:
        return image.info.get("icc_profile")


def encode_jpeg_under_limit(
    image: Image.Image,
    input_file: Path,
) -> tuple[bytes, int, bool]:
    """
    Convert to baseline JPEG.

    Returns:
        jpeg_bytes, selected_quality, is_oversize

    If the image cannot fit below 2 MB at the minimum quality, preserve the
    best-quality minimum-quality JPEG and route it to NeedsReview instead of
    discarding an otherwise usable edit.
    """
    image = image.convert("RGB")
    exif = get_preserved_exif(input_file, image.size)
    icc_profile = get_icc_profile(input_file)

    last_bytes: bytes | None = None
    last_quality = JPEG_MIN_QUALITY

    for quality in range(
        JPEG_START_QUALITY,
        JPEG_MIN_QUALITY - 1,
        -JPEG_QUALITY_STEP,
    ):
        buffer = BytesIO()
        save_kwargs: dict[str, Any] = {
            "format": "JPEG",
            "quality": quality,
            "optimize": True,
            "progressive": False,
            "subsampling": 0,
            "dpi": DPI,
        }

        if exif:
            save_kwargs["exif"] = exif
        if icc_profile:
            save_kwargs["icc_profile"] = icc_profile

        image.save(buffer, **save_kwargs)
        jpeg_bytes = buffer.getvalue()
        last_bytes = jpeg_bytes
        last_quality = quality

        if len(jpeg_bytes) <= MAX_FILE_SIZE_BYTES:
            return jpeg_bytes, quality, False

    if last_bytes is None:
        raise RuntimeError("JPEG encoding failed before any output was created.")

    return last_bytes, last_quality, True



def allowed_brightness_shift(input_mean: float) -> float:
    """
    Allow stronger exposure correction for darker source images.

    This matches the actual MLS workflow: very dark rooms may need a large
    brightness increase, while already bright rooms should change much less.
    """
    if input_mean < 0.25:
        return 0.45
    if input_mean < 0.35:
        return 0.38
    if input_mean < 0.45:
        return 0.32
    if input_mean < 0.55:
        return 0.27
    return 0.22


def compare_images(
    input_file: Path,
    output_image: Image.Image,
    sharpened: bool,
) -> VerificationResult:
    with Image.open(input_file) as source:
        source_arr = image_to_rgb_array(source, NORMALIZED_LONG_EDGE)

    output_arr = image_to_rgb_array(output_image, NORMALIZED_LONG_EDGE)

    in_lum = luminance_from_rgb(source_arr)
    out_lum = luminance_from_rgb(output_arr)

    in_sharpness = max(normalized_edge_sharpness(source_arr), 1e-9)
    out_sharpness = normalized_edge_sharpness(output_arr)
    sharpness_ratio = out_sharpness / in_sharpness

    brightness_shift = float(abs(out_lum.mean() - in_lum.mean()))
    highlight_clip_fraction = float((out_lum > 0.985).mean())
    shadow_crush_fraction = float((out_lum < 0.03).mean())

    color_shift = chromaticity_shift(source_arr, output_arr)

    messages: list[str] = []
    status = "PASS"

    if sharpness_ratio < SHARPNESS_FAIL_RATIO:
        status = "FAIL"
        messages.append(
            f"Severe normalized sharpness loss: {sharpness_ratio:.2f}x input."
        )
    elif sharpness_ratio < SHARPNESS_REVIEW_RATIO:
        status = "REVIEW"
        messages.append(
            f"Moderate normalized sharpness loss: {sharpness_ratio:.2f}x input."
        )

    input_mean_brightness = float(in_lum.mean())
    brightness_limit = max(
        MAX_GLOBAL_BRIGHTNESS_SHIFT,
        allowed_brightness_shift(input_mean_brightness),
    )

    if brightness_shift > brightness_limit:
        status = "REVIEW" if status == "PASS" else status
        messages.append(
            f"Large global brightness shift: {brightness_shift:.3f} "
            f"(adaptive limit {brightness_limit:.3f})."
        )

    if highlight_clip_fraction > MAX_HIGHLIGHT_CLIP_FRACTION:
        status = "REVIEW" if status == "PASS" else status
        messages.append(
            f"High clipped-highlight fraction: {highlight_clip_fraction:.1%}."
        )

    if shadow_crush_fraction > MAX_SHADOW_CRUSH_FRACTION:
        status = "REVIEW" if status == "PASS" else status
        messages.append(
            f"High crushed-shadow fraction: {shadow_crush_fraction:.1%}."
        )

    if color_shift > MAX_GLOBAL_CHROMATICITY_SHIFT:
        status = "REVIEW" if status == "PASS" else status
        messages.append(
            f"Large brightness-independent chromaticity shift: {color_shift:.4f}. "
            "This is a review signal, not proof of a specific material-color change."
        )

    if not messages:
        messages.append("Deterministic checks passed.")

    return VerificationResult(
        status=status,
        messages=messages,
        sharpness_ratio=sharpness_ratio,
        brightness_shift=brightness_shift,
        chromaticity_shift=color_shift,
        highlight_clip_fraction=highlight_clip_fraction,
        shadow_crush_fraction=shadow_crush_fraction,
        sharpened=sharpened,
    )


def maybe_apply_gentle_sharpening(
    input_file: Path,
    generated_image: Image.Image,
) -> tuple[Image.Image, bool]:
    if not ENABLE_AUTO_SHARPEN:
        return generated_image, False

    with Image.open(input_file) as source:
        source_arr = image_to_rgb_array(source, NORMALIZED_LONG_EDGE)

    output_arr = image_to_rgb_array(generated_image, NORMALIZED_LONG_EDGE)

    input_sharpness = max(normalized_edge_sharpness(source_arr), 1e-9)
    output_sharpness = normalized_edge_sharpness(output_arr)
    ratio = output_sharpness / input_sharpness

    if SHARPNESS_AUTO_FIX_MIN_RATIO <= ratio < SHARPNESS_AUTO_FIX_MAX_RATIO:
        sharpened = generated_image.filter(
            ImageFilter.UnsharpMask(
                radius=UNSHARP_RADIUS,
                percent=UNSHARP_PERCENT,
                threshold=UNSHARP_THRESHOLD,
            )
        )
        return sharpened, True

    return generated_image, False


def write_error_report(input_file: Path, error: Exception) -> None:
    ERROR_DIR.mkdir(parents=True, exist_ok=True)
    report_path = ERROR_DIR / f"{input_file.stem}_error.txt"
    report_path.write_text(
        f"File: {input_file.name}\n"
        f"Time: {datetime.now().isoformat(timespec='seconds')}\n"
        f"Error: {type(error).__name__}: {error}\n",
        encoding="utf-8",
    )



def initialize_history_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    with sqlite3.connect(HISTORY_DB) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS image_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                processed_at TEXT NOT NULL,
                filename TEXT NOT NULL,
                program_version TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                model TEXT NOT NULL,
                quality TEXT NOT NULL,
                system_decision TEXT NOT NULL,
                implicit_final_label TEXT NOT NULL,
                destination TEXT NOT NULL,
                input_mean_luminance REAL,
                input_contrast_span REAL,
                input_shadow_fraction REAL,
                input_highlight_fraction REAL,
                wb_confidence TEXT,
                wb_cast TEXT,
                sharpness_ratio REAL,
                brightness_shift REAL,
                global_chromaticity_shift REAL,
                output_highlight_clip_fraction REAL,
                output_shadow_crush_fraction REAL,
                sharpened INTEGER,
                usage_input_tokens INTEGER,
                usage_output_tokens INTEGER,
                usage_total_tokens INTEGER,
                fallback_estimated_cost REAL,
                message TEXT
            )
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_history_filename
            ON image_history(filename)
            """
        )
        connection.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_history_run
            ON image_history(run_id)
            """
        )
        connection.commit()


def infer_implicit_label(filename: str, system_decision: str) -> str:
    """
    No manual labeling required.

    - A file in Completed is treated as ACCEPTED.
    - A file in NeedsReview remains UNRESOLVED until the user naturally moves it.
    - Failed files are FAILED.
    """
    if (OUTPUT_DIR / filename).exists():
        return "ACCEPTED"
    if (REVIEW_DIR / filename).exists():
        return "UNRESOLVED"
    if system_decision == "FAILED":
        return "FAILED"
    return "UNKNOWN"


def reconcile_history_labels() -> None:
    """
    Learn from normal folder actions without asking for manual labels.

    If a previously reviewed image is later moved into Completed, all matching
    unresolved records are automatically updated to ACCEPTED.
    """
    if not HISTORY_DB.exists():
        return

    with sqlite3.connect(HISTORY_DB) as connection:
        unresolved = connection.execute(
            """
            SELECT DISTINCT filename
            FROM image_history
            WHERE implicit_final_label = 'UNRESOLVED'
            """
        ).fetchall()

        for (filename,) in unresolved:
            if (OUTPUT_DIR / filename).exists():
                connection.execute(
                    """
                    UPDATE image_history
                    SET implicit_final_label = 'ACCEPTED'
                    WHERE filename = ?
                      AND implicit_final_label = 'UNRESOLVED'
                    """,
                    (filename,),
                )

        connection.commit()


def append_history(
    *,
    run_id: str,
    filename: str,
    system_decision: str,
    destination: str,
    metrics: dict[str, Any],
    verification: VerificationResult | None,
    usage: ApiUsage,
    message: str,
) -> None:
    implicit_label = infer_implicit_label(filename, system_decision)

    with sqlite3.connect(HISTORY_DB) as connection:
        connection.execute(
            """
            INSERT INTO image_history (
                run_id,
                processed_at,
                filename,
                program_version,
                prompt_version,
                model,
                quality,
                system_decision,
                implicit_final_label,
                destination,
                input_mean_luminance,
                input_contrast_span,
                input_shadow_fraction,
                input_highlight_fraction,
                wb_confidence,
                wb_cast,
                sharpness_ratio,
                brightness_shift,
                global_chromaticity_shift,
                output_highlight_clip_fraction,
                output_shadow_crush_fraction,
                sharpened,
                usage_input_tokens,
                usage_output_tokens,
                usage_total_tokens,
                fallback_estimated_cost,
                message
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                datetime.now().isoformat(timespec="seconds"),
                filename,
                PROGRAM_VERSION,
                PROMPT_VERSION,
                MODEL,
                QUALITY,
                system_decision,
                implicit_label,
                destination,
                metrics.get("mean") if metrics else None,
                metrics.get("contrast_span") if metrics else None,
                metrics.get("shadow_fraction") if metrics else None,
                metrics.get("highlight_fraction") if metrics else None,
                metrics.get("wb", {}).get("confidence") if metrics else None,
                metrics.get("wb", {}).get("cast") if metrics else None,
                verification.sharpness_ratio if verification else None,
                verification.brightness_shift if verification else None,
                verification.chromaticity_shift if verification else None,
                verification.highlight_clip_fraction if verification else None,
                verification.shadow_crush_fraction if verification else None,
                int(verification.sharpened) if verification else None,
                usage.input_tokens,
                usage.output_tokens,
                usage.total_tokens,
                OBSERVED_ESTIMATED_COST_PER_IMAGE
                if system_decision in {"PASS", "REVIEW"}
                else None,
                message,
            ),
        )
        connection.commit()


def print_history_summary() -> None:
    if not HISTORY_DB.exists():
        return

    with sqlite3.connect(HISTORY_DB) as connection:
        total = connection.execute(
            "SELECT COUNT(*) FROM image_history"
        ).fetchone()[0]
        accepted = connection.execute(
            """
            SELECT COUNT(*) FROM image_history
            WHERE implicit_final_label = 'ACCEPTED'
            """
        ).fetchone()[0]
        unresolved = connection.execute(
            """
            SELECT COUNT(*) FROM image_history
            WHERE implicit_final_label = 'UNRESOLVED'
            """
        ).fetchone()[0]

    print(
        f"Learning history: {total} image records | "
        f"accepted={accepted} | unresolved={unresolved}"
    )


def create_log_file() -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return LOG_DIR / f"run_{stamp}.csv"


def append_log(log_path: Path, row: dict[str, object]) -> None:
    fieldnames = [
        "filename",
        "program_version",
        "prompt_version",
        "model",
        "quality",
        "status",
        "destination",
        "requested_size",
        "output_width",
        "output_height",
        "jpeg_quality",
        "output_bytes",
        "input_mean_luminance",
        "input_contrast_span",
        "input_shadow_fraction",
        "input_highlight_fraction",
        "wb_confidence",
        "wb_cast",
        "wb_neutral_fraction",
        "sharpness_ratio",
        "brightness_shift",
        "global_chromaticity_shift",
        "output_highlight_clip_fraction",
        "output_shadow_crush_fraction",
        "sharpened",
        "usage_input_tokens",
        "usage_output_tokens",
        "usage_total_tokens",
        "usage_raw",
        "fallback_estimated_cost",
        "message",
    ]

    write_header = not log_path.exists()

    with log_path.open("a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        if write_header:
            writer.writeheader()
        writer.writerow(row)



def run_internal_regression_tests() -> None:
    """
    No-cost verifier regression tests.

    Reproduces the reported bug:
    a pure uniform brightness scaling must not trigger a color-shift warning.
    """
    rng = np.random.default_rng(20260711)

    # Keep channels below clipping after 1.7x scaling.
    source = rng.uniform(0.05, 0.50, size=(160, 240, 3)).astype(np.float32)
    brighter = source * 1.7

    shift = chromaticity_shift(source, brighter)

    if shift > 0.001:
        raise RuntimeError(
            "Internal regression test failed: pure brightness scaling produced "
            f"chromaticity shift {shift:.6f}, expected <= 0.001. "
            "No paid API calls were made."
        )

    # A deliberate channel imbalance must be detected.
    tinted = source.copy()
    tinted[..., 0] *= 1.18
    tint_shift = chromaticity_shift(source, tinted)

    if tint_shift < 0.01:
        raise RuntimeError(
            "Internal regression test failed: deliberate red-channel tint was "
            f"not detected (shift={tint_shift:.6f}). No paid API calls were made."
        )

    print(
        "Internal verifier tests: PASS "
        f"(brightness-only shift={shift:.6f}, deliberate tint shift={tint_shift:.6f})"
    )


def main() -> None:
    run_internal_regression_tests()

    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError(
            'OPENAI_API_KEY is not set.\n'
            'Run: export OPENAI_API_KEY="your_api_key_here"'
        )

    client = OpenAI(api_key=api_key)

    initialize_history_db()
    reconcile_history_labels()

    for directory in (INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, ERROR_DIR, LOG_DIR, DATA_DIR):
        directory.mkdir(parents=True, exist_ok=True)

    base_prompt = load_prompt()

    candidates = sorted(
        file
        for file in INPUT_DIR.iterdir()
        if file.is_file() and file.suffix.lower() in SUPPORTED_EXTENSIONS
    )

    if not candidates:
        print(f"No JPEG images found in: {INPUT_DIR}")
        return

    pending = [
        file
        for file in candidates
        if not (OUTPUT_DIR / file.name).exists()
        and not (REVIEW_DIR / file.name).exists()
    ]
    skipped = len(candidates) - len(pending)

    print("=" * 76)
    print("MYESTATEPICS MLS AI — PRODUCTION v1.6")
    print(f"Images found: {len(candidates)}")
    print(f"Already completed/reviewed and skipped: {skipped}")
    print(f"Images to process: {len(pending)}")
    print(f"Model: {MODEL} | Quality: {QUALITY}")
    print(
        f"Fallback estimated cost: "
        f"${len(pending) * OBSERVED_ESTIMATED_COST_PER_IMAGE:.2f}"
    )
    print("Outputs: JPEG, same filename, maximum 2 MB")
    print("Originals remain untouched in Incoming/")
    print("=" * 76)

    if not pending:
        print("Nothing to process.")
        return

    confirmation = input("Proceed and start spending? [y/N]: ").strip().lower()
    if confirmation not in {"y", "yes"}:
        print("Cancelled. No API calls were made.")
        return

    log_path = create_log_file()
    run_id = log_path.stem
    success = 0
    review_count = 0
    failed = 0

    usage_responses = 0
    usage_input_tokens_total = 0
    usage_output_tokens_total = 0
    usage_total_tokens_total = 0

    for index, input_file in enumerate(pending, start=1):
        print("-" * 76)
        print(f"[{index}/{len(pending)}] PROCESSING: {input_file.name}")

        requested_size = ""
        metrics: dict[str, Any] = {}
        usage = ApiUsage()

        try:
            metrics = analyze_input(input_file)
            adaptive_prompt = build_adaptive_addendum(metrics)
            full_prompt = f"{base_prompt}\n\n{adaptive_prompt}"

            print(
                "    Analysis: "
                f"brightness={metrics['mean']:.3f}, "
                f"contrast={metrics['contrast_span']:.3f}, "
                f"shadows={metrics['shadow_fraction']:.1%}, "
                f"highlights={metrics['highlight_fraction']:.1%}, "
                f"WB={metrics['wb']['cast']} "
                f"({metrics['wb']['confidence']})"
            )

            generated_bytes, requested_size, usage = call_image_editor(
                client,
                input_file,
                full_prompt,
            )

            if any(
                value is not None
                for value in (
                    usage.input_tokens,
                    usage.output_tokens,
                    usage.total_tokens,
                )
            ):
                usage_responses += 1
                usage_input_tokens_total += usage.input_tokens or 0
                usage_output_tokens_total += usage.output_tokens or 0
                usage_total_tokens_total += usage.total_tokens or 0

            with Image.open(BytesIO(generated_bytes)) as generated:
                generated_image = generated.convert("RGB")

            generated_image, sharpened = maybe_apply_gentle_sharpening(
                input_file,
                generated_image,
            )

            verification = compare_images(
                input_file,
                generated_image,
                sharpened,
            )

            jpeg_bytes, jpeg_quality, oversize = encode_jpeg_under_limit(
                generated_image,
                input_file,
            )

            if oversize:
                if verification.status == "PASS":
                    verification.status = "REVIEW"
                verification.messages.append(
                    f"JPEG remains above 2 MB at minimum quality {jpeg_quality}; "
                    "saved for manual Lightroom export."
                )

            if verification.status == "PASS":
                destination_dir = OUTPUT_DIR
                success += 1
            else:
                destination_dir = REVIEW_DIR
                review_count += 1

            destination_file = destination_dir / input_file.name
            destination_file.write_bytes(jpeg_bytes)

            output_width, output_height = generated_image.size

            print(
                f"    {verification.status}: {destination_file.name} | "
                f"{output_width}x{output_height} | "
                f"{len(jpeg_bytes) / 1_000_000:.2f} MB | "
                f"JPEG quality {jpeg_quality}"
            )
            for message in verification.messages:
                print(f"      - {message}")

            append_log(
                log_path,
                {
                    "filename": input_file.name,
                    "program_version": PROGRAM_VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "model": MODEL,
                    "quality": QUALITY,
                    "status": verification.status,
                    "destination": str(destination_dir),
                    "requested_size": requested_size,
                    "output_width": output_width,
                    "output_height": output_height,
                    "jpeg_quality": jpeg_quality,
                    "output_bytes": len(jpeg_bytes),
                    "input_mean_luminance": f"{metrics['mean']:.6f}",
                    "input_contrast_span": f"{metrics['contrast_span']:.6f}",
                    "input_shadow_fraction": f"{metrics['shadow_fraction']:.6f}",
                    "input_highlight_fraction": f"{metrics['highlight_fraction']:.6f}",
                    "wb_confidence": metrics["wb"]["confidence"],
                    "wb_cast": metrics["wb"]["cast"],
                    "wb_neutral_fraction": f"{metrics['wb']['candidate_fraction']:.6f}",
                    "sharpness_ratio": f"{verification.sharpness_ratio:.6f}",
                    "brightness_shift": f"{verification.brightness_shift:.6f}",
                    "global_chromaticity_shift": f"{verification.chromaticity_shift:.6f}",
                    "output_highlight_clip_fraction": (
                        f"{verification.highlight_clip_fraction:.6f}"
                    ),
                    "output_shadow_crush_fraction": (
                        f"{verification.shadow_crush_fraction:.6f}"
                    ),
                    "sharpened": verification.sharpened,
                    "usage_input_tokens": usage.input_tokens or "",
                    "usage_output_tokens": usage.output_tokens or "",
                    "usage_total_tokens": usage.total_tokens or "",
                    "usage_raw": usage.raw_usage,
                    "fallback_estimated_cost": (
                        f"{OBSERVED_ESTIMATED_COST_PER_IMAGE:.6f}"
                    ),
                    "message": " | ".join(verification.messages),
                },
            )

            append_history(
                run_id=run_id,
                filename=input_file.name,
                system_decision=verification.status,
                destination=str(destination_dir),
                metrics=metrics,
                verification=verification,
                usage=usage,
                message=" | ".join(verification.messages),
            )

        except Exception as error:
            failed += 1
            write_error_report(input_file, error)

            print(f"    FAILED: {input_file.name}")
            print(f"    Reason: {error}")
            print("    Original remains untouched in Incoming/.")

            append_log(
                log_path,
                {
                    "filename": input_file.name,
                    "program_version": PROGRAM_VERSION,
                    "prompt_version": PROMPT_VERSION,
                    "model": MODEL,
                    "quality": QUALITY,
                    "status": "FAILED",
                    "destination": str(ERROR_DIR),
                    "requested_size": requested_size,
                    "output_width": "",
                    "output_height": "",
                    "jpeg_quality": "",
                    "output_bytes": "",
                    "input_mean_luminance": (
                        f"{metrics['mean']:.6f}" if metrics else ""
                    ),
                    "input_contrast_span": (
                        f"{metrics['contrast_span']:.6f}" if metrics else ""
                    ),
                    "input_shadow_fraction": (
                        f"{metrics['shadow_fraction']:.6f}" if metrics else ""
                    ),
                    "input_highlight_fraction": (
                        f"{metrics['highlight_fraction']:.6f}" if metrics else ""
                    ),
                    "wb_confidence": (
                        metrics.get("wb", {}).get("confidence", "") if metrics else ""
                    ),
                    "wb_cast": (
                        metrics.get("wb", {}).get("cast", "") if metrics else ""
                    ),
                    "wb_neutral_fraction": (
                        f"{metrics.get('wb', {}).get('candidate_fraction', 0):.6f}"
                        if metrics else ""
                    ),
                    "sharpness_ratio": "",
                    "brightness_shift": "",
                    "global_chromaticity_shift": "",
                    "output_highlight_clip_fraction": "",
                    "output_shadow_crush_fraction": "",
                    "sharpened": "",
                    "usage_input_tokens": usage.input_tokens or "",
                    "usage_output_tokens": usage.output_tokens or "",
                    "usage_total_tokens": usage.total_tokens or "",
                    "usage_raw": usage.raw_usage,
                    "fallback_estimated_cost": "",
                    "message": str(error),
                },
            )
            append_history(
                run_id=run_id,
                filename=input_file.name,
                system_decision="FAILED",
                destination=str(ERROR_DIR),
                metrics=metrics,
                verification=None,
                usage=usage,
                message=str(error),
            )


    attempted_successfully = success + review_count
    fallback_cost = attempted_successfully * OBSERVED_ESTIMATED_COST_PER_IMAGE

    print("=" * 76)
    print("BATCH COMPLETE")
    print(f"Completed/PASS: {success}")
    print(f"NeedsReview: {review_count}")
    print(f"Failed: {failed}")
    print(f"Previously completed/reviewed and skipped: {skipped}")
    print(f"Fallback estimated spend: ${fallback_cost:.2f}")

    if usage_responses:
        print(
            "API-reported usage totals "
            f"({usage_responses} response(s)): "
            f"input={usage_input_tokens_total}, "
            f"output={usage_output_tokens_total}, "
            f"total={usage_total_tokens_total}"
        )
    else:
        print("API-reported token usage was not returned for this run.")

    print(
        "Token totals are logged when available, but the OpenAI usage "
        "dashboard remains the billing source of truth."
    )
    print(f"Completed folder: {OUTPUT_DIR}")
    print(f"NeedsReview folder: {REVIEW_DIR}")
    print(f"Run log: {log_path}")
    print(f"Learning database: {HISTORY_DB}")
    print_history_summary()
    print("=" * 76)


if __name__ == "__main__":
    main()
