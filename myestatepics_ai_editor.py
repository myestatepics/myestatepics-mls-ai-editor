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
from dotenv import load_dotenv


APP_DIR = Path(__file__).resolve().parent
PROJECT_DIR = APP_DIR
RUNTIME_DIR = APP_DIR / "runtime"
INPUT_DIR = RUNTIME_DIR / "Incoming"
OUTPUT_DIR = RUNTIME_DIR / "Completed"
REVIEW_DIR = RUNTIME_DIR / "NeedsReview"
ERROR_DIR = RUNTIME_DIR / "Error"
LOG_DIR = RUNTIME_DIR / "Logs"
DATA_DIR = RUNTIME_DIR / "Data"
HISTORY_DB = DATA_DIR / "image_history.sqlite3"
PROMPT_FILE = APP_DIR / "legacy" / "myestatepics_mls_interior_prompt_v1_6.txt"

PROGRAM_VERSION = "1.6"
PROMPT_VERSION = "1.6"
MODEL = "gpt-image-2"
QUALITY = "low"
QUALITY_OPTIONS = ("low", "medium")
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
LANDSCAPE_SIZE = "1536x1024"
PORTRAIT_SIZE = "1024x1536"
SQUARE_SIZE = "1024x1024"
MAX_FILE_SIZE_BYTES = 2_000_000
JPEG_START_QUALITY = 95
JPEG_MIN_QUALITY = 78
JPEG_QUALITY_STEP = 2
DPI = (300, 300)
OBSERVED_ESTIMATED_COST_PER_IMAGE = 0.28 / 6.0
LOW_ESTIMATED_COST_PER_IMAGE = OBSERVED_ESTIMATED_COST_PER_IMAGE * 0.5
MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 3
NORMALIZED_LONG_EDGE = 1024
SHARPNESS_REVIEW_RATIO = 0.60
SHARPNESS_FAIL_RATIO = 0.45
SHARPNESS_AUTO_FIX_MIN_RATIO = 0.55
SHARPNESS_AUTO_FIX_MAX_RATIO = 0.85
MAX_GLOBAL_BRIGHTNESS_SHIFT = 0.34
MAX_HIGHLIGHT_CLIP_FRACTION = 0.24
MAX_SHADOW_CRUSH_FRACTION = 0.28
MAX_GLOBAL_CHROMATICITY_SHIFT = 0.055
ENABLE_AUTO_SHARPEN = False
UNSHARP_RADIUS = 0.8
UNSHARP_PERCENT = 45
UNSHARP_THRESHOLD = 3
WB_MIN_NEUTRAL_FRACTION = 0.08
WB_MAX_NEUTRAL_SATURATION = 0.12
WB_MIN_VALUE = 0.25
WB_MAX_VALUE = 0.90
WB_CAST_THRESHOLD = 0.025
WB_MAX_CHANNEL_STD = 0.16


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


def validate_api_key(api_key: str | None) -> tuple[bool, str]:
    """Validate without ever exposing the key or any fragment of it."""
    if not api_key or not api_key.strip():
        return False, "OPENAI_API_KEY is empty. Add a valid key to the project .env file."
    key = api_key.strip()
    lowered = key.lower()
    if (
        not key.startswith("sk-")
        or "placeholder" in lowered
        or "your_openai" in lowered
        or "replace" in lowered
        or "*" in key
        or "…" in key
        or "..." in key
    ):
        return False, "OPENAI_API_KEY in the project .env file is invalid or masked."
    return True, "API key loaded from the project .env file."


def load_project_api_key() -> tuple[str | None, str]:
    """Reload the application-local .env and override stale shell values."""
    load_dotenv(dotenv_path=APP_DIR / ".env", override=True)
    api_key = os.getenv("OPENAI_API_KEY")
    valid, message = validate_api_key(api_key)
    return (api_key.strip() if valid and api_key else None), message


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
                estimated_cost_per_image(QUALITY)
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
        "processing_time_seconds",
        "api_cost",
        "status",
        "destination",
        "needs_review_reason",
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

    api_key, key_message = load_project_api_key()
    if not api_key:
        raise RuntimeError(key_message)

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


@dataclass
class BatchSummary:
    total: int = 0
    completed: int = 0
    review: int = 0
    failed: int = 0
    skipped: int = 0
    cancelled: bool = False
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    usage_responses: int = 0
    api_calls: int = 0
    fallback_cost: float = 0.0
    log_path: Path | None = None
    quality: str = "low"
    elapsed_seconds: float = 0.0

    @property
    def images_processed(self) -> int:
        return self.completed + self.review + self.failed

    @property
    def average_cost_per_image(self) -> float:
        if not self.images_processed:
            return 0.0
        return self.fallback_cost / self.images_processed


def estimated_cost_per_image(quality: str) -> float:
    """Fallback estimate scaled from the preserved observed medium-quality cost."""
    if quality == "low":
        return LOW_ESTIMATED_COST_PER_IMAGE
    return OBSERVED_ESTIMATED_COST_PER_IMAGE


def configure_runtime_paths(
    input_dir: Path,
    output_dir: Path,
    review_dir: Path,
    error_dir: Path,
    log_dir: Path,
) -> None:
    """Set user-selected runtime paths without embedding a machine username."""
    global INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, ERROR_DIR, LOG_DIR, DATA_DIR, HISTORY_DB
    INPUT_DIR = Path(input_dir)
    OUTPUT_DIR = Path(output_dir)
    REVIEW_DIR = Path(review_dir)
    ERROR_DIR = Path(error_dir)
    LOG_DIR = Path(log_dir)
    DATA_DIR = LOG_DIR.parent / "Data"
    HISTORY_DB = DATA_DIR / "image_history.sqlite3"


def pending_images(selected_files: list[Path] | None = None) -> tuple[list[Path], int]:
    INPUT_DIR.mkdir(parents=True, exist_ok=True)
    if selected_files:
        incoming = INPUT_DIR.resolve()
        candidates = sorted(
            {
                Path(file).resolve()
                for file in selected_files
                if Path(file).is_file()
                and Path(file).suffix.lower() in SUPPORTED_EXTENSIONS
                and Path(file).resolve().parent == incoming
            }
        )
    else:
        candidates = sorted(
            file
            for file in INPUT_DIR.iterdir()
            if file.is_file() and file.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    pending = [
        file
        for file in candidates
        if not (OUTPUT_DIR / file.name).exists()
        and not (REVIEW_DIR / file.name).exists()
    ]
    return pending, len(candidates) - len(pending)


def _atomic_write(destination: Path, data: bytes) -> None:
    """Prevent cancellation or crashes from leaving a partial JPEG."""
    temporary = destination.with_name(f".{destination.name}.tmp")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def _log_row(
    *,
    filename: str,
    status: str,
    destination: Path,
    requested_size: str = "",
    metrics: dict[str, Any] | None = None,
    verification: VerificationResult | None = None,
    usage: ApiUsage | None = None,
    jpeg_quality: int | str = "",
    output_bytes: int | str = "",
    output_size: tuple[int, int] | None = None,
    message: str = "",
    processing_time_seconds: float = 0.0,
    api_cost: float = 0.0,
    needs_review_reason: str = "",
) -> dict[str, object]:
    metrics = metrics or {}
    usage = usage or ApiUsage()
    wb = metrics.get("wb", {})
    return {
        "filename": filename,
        "program_version": PROGRAM_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "quality": QUALITY,
        "processing_time_seconds": f"{processing_time_seconds:.3f}",
        "api_cost": f"{api_cost:.6f}",
        "status": status,
        "destination": str(destination),
        "needs_review_reason": needs_review_reason,
        "requested_size": requested_size,
        "output_width": output_size[0] if output_size else "",
        "output_height": output_size[1] if output_size else "",
        "jpeg_quality": jpeg_quality,
        "output_bytes": output_bytes,
        "input_mean_luminance": f"{metrics['mean']:.6f}" if metrics else "",
        "input_contrast_span": f"{metrics['contrast_span']:.6f}" if metrics else "",
        "input_shadow_fraction": f"{metrics['shadow_fraction']:.6f}" if metrics else "",
        "input_highlight_fraction": f"{metrics['highlight_fraction']:.6f}" if metrics else "",
        "wb_confidence": wb.get("confidence", ""),
        "wb_cast": wb.get("cast", ""),
        "wb_neutral_fraction": f"{wb.get('candidate_fraction', 0):.6f}" if wb else "",
        "sharpness_ratio": f"{verification.sharpness_ratio:.6f}" if verification else "",
        "brightness_shift": f"{verification.brightness_shift:.6f}" if verification else "",
        "global_chromaticity_shift": f"{verification.chromaticity_shift:.6f}" if verification else "",
        "output_highlight_clip_fraction": f"{verification.highlight_clip_fraction:.6f}" if verification else "",
        "output_shadow_crush_fraction": f"{verification.shadow_crush_fraction:.6f}" if verification else "",
        "sharpened": verification.sharpened if verification else "",
        "usage_input_tokens": usage.input_tokens or "",
        "usage_output_tokens": usage.output_tokens or "",
        "usage_total_tokens": usage.total_tokens or "",
        "usage_raw": usage.raw_usage,
        "fallback_estimated_cost": (
            f"{estimated_cost_per_image(QUALITY):.6f}"
            if status in {"PASS", "REVIEW", "FAIL"}
            else ""
        ),
        "message": message,
    }


def process_batch(
    client: OpenAI,
    *,
    selected_files: list[Path] | None = None,
    quality: str = "low",
    cancel_requested=lambda: False,
    event=lambda kind, payload: None,
) -> BatchSummary:
    """Run the v1.6 engine sequentially; designed for a GUI worker thread."""
    if quality not in QUALITY_OPTIONS:
        raise ValueError(f"Unsupported quality: {quality}")
    global QUALITY
    QUALITY = quality
    batch_started = time.perf_counter()
    run_internal_regression_tests()
    for directory in (INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, ERROR_DIR, LOG_DIR, DATA_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    initialize_history_db()
    reconcile_history_labels()
    base_prompt = load_prompt()
    files, skipped = pending_images(selected_files)
    summary = BatchSummary(total=len(files), skipped=skipped, quality=quality)
    if not files:
        summary.elapsed_seconds = time.perf_counter() - batch_started
        return summary
    log_path = create_log_file()
    summary.log_path = log_path
    run_id = log_path.stem

    for index, input_file in enumerate(files, 1):
        if cancel_requested():
            summary.cancelled = True
            event("cancelled", input_file.name)
            break
        event("started", {"index": index, "total": len(files), "filename": input_file.name})
        requested_size = ""
        metrics: dict[str, Any] = {}
        usage = ApiUsage()
        image_started = time.perf_counter()
        api_call_completed = False
        try:
            metrics = analyze_input(input_file)
            prompt = f"{base_prompt}\n\n{build_adaptive_addendum(metrics)}"
            generated_bytes, requested_size, usage = call_image_editor(
                client, input_file, prompt
            )
            api_call_completed = True
            summary.api_calls += 1
            with Image.open(BytesIO(generated_bytes)) as generated:
                generated_image = generated.convert("RGB")
            generated_image, sharpened = maybe_apply_gentle_sharpening(
                input_file, generated_image
            )
            verification = compare_images(input_file, generated_image, sharpened)
            jpeg_bytes, jpeg_quality, oversize = encode_jpeg_under_limit(
                generated_image, input_file
            )
            review_reasons: list[str] = []
            if min(generated_image.size) < 64:
                review_reasons.append(
                    f"Invalid generated dimensions: {generated_image.size[0]}x"
                    f"{generated_image.size[1]}; each dimension must be at least 64 pixels."
                )
            if verification.status == "FAIL":
                review_reasons.extend(verification.messages)
            if oversize:
                review_reasons.append(
                    f"JPEG remains above 2 MB at minimum quality {jpeg_quality}; "
                    "saved for manual Lightroom export."
                )
            needs_review_reason = " | ".join(dict.fromkeys(review_reasons))
            routing_status = "REVIEW" if needs_review_reason else "PASS"
            destination = REVIEW_DIR if needs_review_reason else OUTPUT_DIR
            _atomic_write(destination / input_file.name, jpeg_bytes)
            if destination == OUTPUT_DIR:
                summary.completed += 1
            else:
                summary.review += 1
            if any((usage.input_tokens, usage.output_tokens, usage.total_tokens)):
                summary.usage_responses += 1
            summary.input_tokens += usage.input_tokens or 0
            summary.output_tokens += usage.output_tokens or 0
            summary.total_tokens += usage.total_tokens or 0
            message = needs_review_reason or " | ".join(verification.messages)
            image_elapsed = time.perf_counter() - image_started
            image_cost = estimated_cost_per_image(quality)
            append_log(
                log_path,
                _log_row(
                    filename=input_file.name,
                    status=routing_status,
                    destination=destination,
                    requested_size=requested_size,
                    metrics=metrics,
                    verification=verification,
                    usage=usage,
                    jpeg_quality=jpeg_quality,
                    output_bytes=len(jpeg_bytes),
                    output_size=generated_image.size,
                    message=message,
                    processing_time_seconds=image_elapsed,
                    api_cost=image_cost,
                    needs_review_reason=needs_review_reason,
                ),
            )
            append_history(
                run_id=run_id,
                filename=input_file.name,
                system_decision=routing_status,
                destination=str(destination),
                metrics=metrics,
                verification=verification,
                usage=usage,
                message=message,
            )
            event(
                "finished",
                {
                    "filename": input_file.name,
                    "status": routing_status,
                    "messages": [message],
                    "metrics": verification,
                    "processing_time_seconds": image_elapsed,
                    "api_cost": image_cost,
                    "destination": str(destination),
                    "needs_review_reason": needs_review_reason,
                },
            )
        except Exception as error:
            summary.failed += 1
            write_error_report(input_file, error)
            append_log(
                log_path,
                _log_row(
                    filename=input_file.name,
                    status="FAILED",
                    destination=ERROR_DIR,
                    requested_size=requested_size,
                    metrics=metrics,
                    usage=usage,
                    message=str(error),
                    processing_time_seconds=time.perf_counter() - image_started,
                    api_cost=(estimated_cost_per_image(quality) if api_call_completed else 0.0),
                ),
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
            event(
                "failed",
                {
                    "filename": input_file.name,
                    "error": str(error),
                    "processing_time_seconds": time.perf_counter() - image_started,
                    "api_cost": (
                        estimated_cost_per_image(quality) if api_call_completed else 0.0
                    ),
                    "destination": str(ERROR_DIR),
                    "needs_review_reason": "",
                },
            )
    summary.fallback_cost = summary.api_calls * estimated_cost_per_image(quality)
    summary.elapsed_seconds = time.perf_counter() - batch_started
    return summary


def set_review_label(filename: str, label: str) -> None:
    with sqlite3.connect(HISTORY_DB) as connection:
        connection.execute(
            """
            UPDATE image_history SET implicit_final_label = ?
            WHERE filename = ? AND implicit_final_label = 'UNRESOLVED'
            """,
            (label, filename),
        )
        connection.commit()


def launch_gui() -> int:
    try:
        from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot, QUrl
        from PySide6.QtGui import QDesktopServices, QPixmap
        from PySide6.QtWidgets import (
            QApplication,
            QComboBox,
            QFileDialog,
            QFormLayout,
            QGridLayout,
            QHBoxLayout,
            QLabel,
            QMainWindow,
            QMessageBox,
            QPlainTextEdit,
            QProgressBar,
            QPushButton,
            QScrollArea,
            QSplitter,
            QTableWidget,
            QTableWidgetItem,
            QVBoxLayout,
            QWidget,
        )
    except ImportError as error:
        raise RuntimeError(
            "PySide6 is required. Install dependencies with: pip install -r requirements.txt"
        ) from error

    class Worker(QObject):
        event_signal = Signal(str, object)
        complete = Signal(object)

        def __init__(self, client, selected_files, quality):
            super().__init__()
            self.client = client
            self.selected_files = selected_files
            self.quality = quality
            self.cancelled = False

        @Slot()
        def run(self):
            summary = process_batch(
                self.client,
                selected_files=self.selected_files,
                quality=self.quality,
                cancel_requested=lambda: self.cancelled,
                event=lambda kind, payload: self.event_signal.emit(kind, payload),
            )
            self.complete.emit(summary)

        @Slot()
        def cancel(self):
            self.cancelled = True

    class ReviewWindow(QMainWindow):
        reprocess = Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Review — MyEstatePics AI Editor")
            self.resize(1100, 700)
            self.files: list[Path] = []
            self.index = 0
            root = QWidget()
            layout = QVBoxLayout(root)
            self.title = QLabel()
            self.reason = QLabel()
            self.reason.setWordWrap(True)
            self.metrics = QLabel()
            images = QSplitter()
            self.original = QLabel("Original")
            self.processed = QLabel("Processed")
            for label in (self.original, self.processed):
                label.setAlignment(Qt.AlignCenter)
                label.setMinimumSize(400, 400)
                images.addWidget(label)
            buttons = QHBoxLayout()
            for text, handler in (
                ("Previous", self.previous),
                ("Next", self.next),
                ("Accept", self.accept_image),
                ("Reject", self.reject_image),
                ("Reprocess", self.reprocess_image),
            ):
                button = QPushButton(text)
                button.clicked.connect(handler)
                buttons.addWidget(button)
            layout.addWidget(self.title)
            layout.addWidget(images, 1)
            layout.addWidget(self.reason)
            layout.addWidget(self.metrics)
            layout.addLayout(buttons)
            self.setCentralWidget(root)

        def refresh_files(self):
            self.files = sorted(
                p for p in REVIEW_DIR.glob("*") if p.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            self.index = min(self.index, max(0, len(self.files) - 1))
            self.show_current()

        def show_current(self):
            if not self.files:
                self.title.setText("No images need review")
                self.original.clear()
                self.processed.clear()
                return
            processed = self.files[self.index]
            source = INPUT_DIR / processed.name
            self.title.setText(f"{self.index + 1}/{len(self.files)} — {processed.name}")
            for label, path in ((self.original, source), (self.processed, processed)):
                pixmap = QPixmap(str(path))
                label.setPixmap(pixmap.scaled(label.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation))
            record = None
            if HISTORY_DB.exists():
                with sqlite3.connect(HISTORY_DB) as connection:
                    record = connection.execute(
                        """SELECT message, sharpness_ratio, brightness_shift,
                        global_chromaticity_shift FROM image_history
                        WHERE filename = ? ORDER BY id DESC LIMIT 1""",
                        (processed.name,),
                    ).fetchone()
            self.reason.setText(f"Verifier reason: {record[0] if record else 'Review requested'}")
            self.metrics.setText(
                "Sharpness: {:.3f}   Brightness shift: {:.3f}   Chromaticity: {:.4f}".format(
                    *(record[1:] if record else (0, 0, 0))
                )
            )

        def previous(self):
            if self.files:
                self.index = (self.index - 1) % len(self.files)
                self.show_current()

        def next(self):
            if self.files:
                self.index = (self.index + 1) % len(self.files)
                self.show_current()

        def accept_image(self):
            if not self.files:
                return
            source = self.files[self.index]
            destination = OUTPUT_DIR / source.name
            if destination.exists():
                QMessageBox.warning(self, "Existing file", f"Completed already contains {source.name}.")
                return
            source.replace(destination)
            set_review_label(source.name, "ACCEPTED")
            self.refresh_files()

        def reject_image(self):
            if self.files:
                set_review_label(self.files[self.index].name, "REJECTED")
                self.reason.setText("Rejected; output remains in NeedsReview.")

        def reprocess_image(self):
            if not self.files:
                return
            filename = self.files[self.index].name
            answer = QMessageBox.question(
                self,
                "Paid reprocess",
                f"Reprocess {filename}? This makes another paid API call.",
            )
            if answer == QMessageBox.Yes:
                self.files[self.index].unlink()
                self.reprocess.emit(filename)
                self.refresh_files()

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle("MyEstatePics AI Editor")
            self.resize(980, 760)
            self.thread = None
            self.worker = None
            self.processing_active = False
            self.api_key: str | None = None
            self.selected_files: set[Path] = set()
            self.review_window = ReviewWindow(self)
            self.review_window.reprocess.connect(lambda _filename: self.start())
            root = QWidget()
            layout = QVBoxLayout(root)
            form = QGridLayout()
            defaults = [INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, ERROR_DIR, LOG_DIR]
            labels = ["Incoming Folder", "Completed", "NeedsReview", "Error", "Logs"]
            self.path_labels = []
            for row, (name, path) in enumerate(zip(labels, defaults)):
                display_row = row if row == 0 else row + 1
                label = QLabel(str(path))
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                choose = QPushButton("Choose Folder…" if row == 0 else "Choose…")
                choose.clicked.connect(lambda checked=False, i=row: self.choose_folder(i))
                open_button = QPushButton("Open in Finder")
                open_button.clicked.connect(lambda checked=False, i=row: self.open_folder(i))
                form.addWidget(QLabel(name), display_row, 0)
                form.addWidget(label, display_row, 1)
                form.addWidget(choose, display_row, 2)
                form.addWidget(open_button, display_row, 3)
                self.path_labels.append(label)
            selection_help = QLabel(
                "Select specific images, or leave the selection empty to process all "
                "supported images in the Incoming folder."
            )
            selection_help.setWordWrap(True)
            form.addWidget(selection_help, 1, 1, 1, 3)
            self.model_label = QLabel(MODEL)
            self.quality = QComboBox()
            self.quality.addItems(["Low", "Medium"])
            self.quality.setCurrentText("Low")
            form.addWidget(QLabel("Model"), 6, 0)
            form.addWidget(self.model_label, 6, 1)
            form.addWidget(QLabel("Quality"), 6, 2)
            form.addWidget(self.quality, 6, 3)
            self.api_key_status = QLabel()
            self.open_env_button = QPushButton("Open .env")
            self.open_env_button.clicked.connect(self.open_env)
            self.reload_key_button = QPushButton("Reload API Key")
            self.reload_key_button.clicked.connect(self.reload_api_key)
            form.addWidget(QLabel("API Key"), 7, 0)
            form.addWidget(self.api_key_status, 7, 1)
            form.addWidget(self.open_env_button, 7, 2)
            form.addWidget(self.reload_key_button, 7, 3)
            layout.addLayout(form)
            selection_actions = QHBoxLayout()
            for text, handler in (
                ("Select Images…", self.select_images),
                ("Select All", self.select_all),
                ("Clear Selection", self.clear_selection),
            ):
                button = QPushButton(text)
                button.clicked.connect(handler)
                selection_actions.addWidget(button)
            selection_actions.addStretch(1)
            layout.addLayout(selection_actions)
            self.selected_table = QTableWidget(0, 3)
            self.selected_table.setHorizontalHeaderLabels(
                ["Filename", "File Size", "Selection Status"]
            )
            self.selected_table.horizontalHeader().setStretchLastSection(True)
            self.selected_table.setMaximumHeight(170)
            layout.addWidget(self.selected_table)
            stats = QHBoxLayout()
            self.image_count = QLabel("Images: 0")
            self.cost = QLabel("Estimated cost: $0.00")
            self.current = QLabel("Current: —")
            stats.addWidget(self.image_count)
            stats.addWidget(self.cost)
            stats.addWidget(self.current, 1)
            layout.addLayout(stats)
            self.progress = QProgressBar()
            layout.addWidget(self.progress)
            self.counts = QLabel("Completed: 0   NeedsReview: 0   Error: 0")
            layout.addWidget(self.counts)
            actions = QHBoxLayout()
            for text, handler in (
                ("Analyze", self.analyze),
                ("Start Processing", self.start),
                ("Cancel", self.cancel),
                ("Review Images", self.open_review),
            ):
                button = QPushButton(text)
                button.clicked.connect(handler)
                actions.addWidget(button)
                if text == "Start Processing":
                    self.start_button = button
                elif text == "Cancel":
                    self.cancel_button = button
            self.cancel_button.setEnabled(False)
            layout.addLayout(actions)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            layout.addWidget(self.log, 1)
            self.setCentralWidget(root)
            self.quality.currentTextChanged.connect(self.on_quality_changed)
            self.reload_api_key(show_error=False)
            self.analyze()

        def apply_paths(self):
            configure_runtime_paths(*(Path(label.text()) for label in self.path_labels))

        def choose_folder(self, index):
            chosen = QFileDialog.getExistingDirectory(self, "Choose folder", self.path_labels[index].text())
            if chosen:
                self.path_labels[index].setText(chosen)
                if index == 0:
                    self.selected_files.clear()
                    self.refresh_selection_table()
                self.analyze()

        def select_images(self):
            self.apply_paths()
            INPUT_DIR.mkdir(parents=True, exist_ok=True)
            filenames, _ = QFileDialog.getOpenFileNames(
                self,
                "Select images from Incoming",
                str(INPUT_DIR),
                "Images (*.jpg *.jpeg *.png)",
            )
            if not filenames:
                return
            incoming = INPUT_DIR.resolve()
            outside = [Path(name) for name in filenames if Path(name).resolve().parent != incoming]
            if outside:
                QMessageBox.warning(
                    self,
                    "Images must be inside Incoming",
                    "Only files directly inside the current Incoming folder can be selected:\n\n"
                    + "\n".join(path.name for path in outside),
                )
            self.selected_files.update(
                Path(name).resolve()
                for name in filenames
                if Path(name).resolve().parent == incoming
                and Path(name).suffix.lower() in SUPPORTED_EXTENSIONS
            )
            self.refresh_selection_table()
            self.analyze()

        def select_all(self):
            self.apply_paths()
            INPUT_DIR.mkdir(parents=True, exist_ok=True)
            self.selected_files = {
                path.resolve()
                for path in INPUT_DIR.iterdir()
                if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
            }
            self.refresh_selection_table()
            self.analyze()

        def clear_selection(self):
            self.selected_files.clear()
            self.refresh_selection_table()
            self.analyze()

        def refresh_selection_table(self):
            files = sorted(self.selected_files)
            self.selected_table.setRowCount(len(files))
            for row, path in enumerate(files):
                if not path.exists():
                    size = "—"
                    status = "Missing"
                else:
                    size = f"{path.stat().st_size / 1_000_000:.2f} MB"
                    if (OUTPUT_DIR / path.name).exists():
                        status = "Skipped — already completed"
                    elif (REVIEW_DIR / path.name).exists():
                        status = "Skipped — already reviewed"
                    else:
                        status = "Selected — ready"
                for column, value in enumerate((path.name, size, status)):
                    self.selected_table.setItem(row, column, QTableWidgetItem(value))

        def selected_or_all(self) -> list[Path] | None:
            return sorted(self.selected_files) if self.selected_files else None

        def on_quality_changed(self, quality):
            global QUALITY
            QUALITY = quality.lower()
            self.log.appendPlainText(f"Quality selected: {quality}")
            self.analyze()

        def open_env(self):
            env_path = APP_DIR / ".env"
            if not env_path.exists():
                QMessageBox.warning(
                    self,
                    "Project .env not found",
                    "Create .env in the application folder, then click Reload API Key.",
                )
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(APP_DIR)))
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(env_path)))

        def reload_api_key(self, checked=False, show_error=True):
            del checked
            self.api_key, message = load_project_api_key()
            valid = self.api_key is not None
            self.api_key_status.setText(message)
            self.api_key_status.setStyleSheet(
                "color: #187a33;" if valid else "color: #b42318;"
            )
            if hasattr(self, "start_button"):
                self.start_button.setEnabled(valid and not self.processing_active)
            if valid:
                if hasattr(self, "log"):
                    self.log.appendPlainText("API key reloaded successfully from project .env.")
            elif show_error:
                QMessageBox.critical(self, "Invalid API key", message)
            return valid

        def open_folder(self, index):
            path = Path(self.path_labels[index].text())
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        def analyze(self):
            self.apply_paths()
            files, skipped = pending_images(self.selected_or_all())
            quality = self.quality.currentText().lower()
            self.image_count.setText(f"Images to process: {len(files)} (existing skipped: {skipped})")
            self.cost.setText(
                f"Estimated cost: ${len(files) * estimated_cost_per_image(quality):.2f}"
            )
            self.progress.setRange(0, max(1, len(files)))
            self.progress.setValue(0)
            self.refresh_selection_table()

        def start(self):
            self.analyze()
            selected = self.selected_or_all()
            files, skipped = pending_images(selected)
            quality = self.quality.currentText().lower()
            if not files:
                QMessageBox.information(self, "Nothing to process", "No pending supported images were found.")
                return
            if skipped:
                QMessageBox.information(
                    self,
                    "Existing outputs",
                    f"{skipped} image(s) already exist in Completed or NeedsReview and will not be overwritten.",
                )
            answer = QMessageBox.question(
                self,
                "Confirm paid processing",
                f"Process {len(files)} image(s) at {quality} quality? "
                f"Estimated cost: ${len(files) * estimated_cost_per_image(quality):.2f}.",
            )
            if answer != QMessageBox.Yes:
                return
            if not self.reload_api_key(show_error=True):
                return
            api_key = self.api_key
            if api_key is None:
                return
            client = OpenAI(api_key=api_key)
            self.thread = QThread(self)
            self.log.appendPlainText(f"Starting batch with quality: {quality}")
            self.worker = Worker(client, selected, quality)
            self.worker.moveToThread(self.thread)
            self.thread.started.connect(self.worker.run)
            self.worker.event_signal.connect(self.on_event)
            self.worker.complete.connect(self.on_complete)
            self.worker.complete.connect(self.thread.quit)
            self.thread.finished.connect(self.worker.deleteLater)
            self.thread.finished.connect(self.thread.deleteLater)
            self.processing_active = True
            self.start_button.setEnabled(False)
            self.cancel_button.setEnabled(True)
            self.thread.start()

        def cancel(self):
            if self.worker:
                self.worker.cancelled = True
                self.log.appendPlainText("Cancellation requested; the current image will finish safely.")
                self.cancel_button.setEnabled(False)

        def on_event(self, kind, payload):
            if kind == "started":
                self.current.setText(f"Current: {payload['filename']}")
                self.progress.setMaximum(payload["total"])
                self.log.appendPlainText(f"Processing {payload['filename']}…")
            elif kind == "finished":
                self.progress.setValue(self.progress.value() + 1)
                review_reason = payload["needs_review_reason"] or "—"
                self.log.appendPlainText(
                    f"Filename: {payload['filename']} | Quality: {QUALITY} | "
                    f"Processing time: {payload['processing_time_seconds']:.2f}s | "
                    f"API cost: ${payload['api_cost']:.4f} | "
                    f"Destination: {payload['destination']} | "
                    f"NeedsReview reason: {review_reason}"
                )
                self.selected_files.discard((INPUT_DIR / payload["filename"]).resolve())
                self.refresh_selection_table()
            elif kind == "failed":
                self.progress.setValue(self.progress.value() + 1)
                self.log.appendPlainText(
                    f"Filename: {payload['filename']} | Quality: {QUALITY} | "
                    f"Processing time: {payload['processing_time_seconds']:.2f}s | "
                    f"API cost: ${payload['api_cost']:.4f} | "
                    f"Destination: {payload['destination']} | NeedsReview reason: — | "
                    f"Error: {payload['error']}"
                )
            elif kind == "cancelled":
                self.log.appendPlainText("Batch cancelled between images.")

        def on_complete(self, summary):
            self.processing_active = False
            self.start_button.setEnabled(self.api_key is not None)
            self.cancel_button.setEnabled(False)
            self.current.setText("Current: —")
            self.counts.setText(
                f"Completed: {summary.completed}   NeedsReview: {summary.review}   Error: {summary.failed}"
            )
            usage = (
                f"API tokens: input {summary.input_tokens}, output {summary.output_tokens}, total {summary.total_tokens}"
                if summary.usage_responses
                else "API token usage was not returned"
            )
            QMessageBox.information(
                self,
                "Batch summary",
                f"Images processed: {summary.images_processed}\n"
                f"Quality: {summary.quality.title()}\n"
                f"Total API cost: ${summary.fallback_cost:.2f}\n"
                f"Average cost per image: ${summary.average_cost_per_image:.4f}\n"
                f"Elapsed time: {summary.elapsed_seconds:.2f} seconds\n"
                f"{usage}\n"
                f"Completed: {summary.completed}\nNeedsReview: {summary.review}\n"
                f"Errors: {summary.failed}",
            )
            self.review_window.refresh_files()

        def open_review(self):
            self.apply_paths()
            self.review_window.refresh_files()
            self.review_window.show()
            self.review_window.raise_()

    app = QApplication.instance() or QApplication([])
    app.setApplicationName("MyEstatePics AI Editor")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(launch_gui())
