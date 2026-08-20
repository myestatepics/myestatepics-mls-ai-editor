"""
MyEstatePics MLS Interior Batch Editor — Direct V4.0.3

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
    JPEG, same filename, quality 100 / 4:4:4
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
- JPEG file size does not affect routing or output quality.
"""

import base64
import atexit
import configparser
import csv
import hashlib
import logging
import os
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Any

import numpy as np
from openai import OpenAI
from PIL import Image, ImageFilter
from dotenv import dotenv_values, load_dotenv
from editing_agent import EditingAgent, RuleSelection
from smart_costing import (
    NO_WINDOW_PULL,
    UNCERTAIN,
    WINDOW_PULL_REQUIRED,
    WindowPullAssessment,
    assess_window_pull,
    select_smart_quality,
)
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas


DEFAULT_APPLICATION_NAME = "MyEstatePics AI Editor"
DIRECT_TEST_APPLICATION_NAME = "MyEstatePics AI Editor - Direct"
# The bundle/Finder name is versioned, but this identity deliberately remains
# stable so V4.0.3 reuses the established Direct Application Support settings.
DISPLAY_APPLICATION_NAME = "MyEstatePics AI Editor - Direct V4.0.3"
APPLICATION_NAME = os.environ.get(
    "MYESTATEPICS_APPLICATION_NAME", DEFAULT_APPLICATION_NAME
)
IS_PACKAGED = bool(getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"))


def resource_path(relative_path: str | Path) -> Path:
    """Locate bundled read-only resources in development and PyInstaller builds."""
    root = (
        Path(getattr(sys, "_MEIPASS"))
        if IS_PACKAGED
        else Path(__file__).resolve().parent
    )
    return root / Path(relative_path)


def copy_legacy_env_if_needed(
    new_data_dir: Path, legacy_data_dir: Path | None = None
) -> bool:
    """Copy the legacy key configuration once without modifying its source."""
    if APPLICATION_NAME != DIRECT_TEST_APPLICATION_NAME:
        return False
    migration_marker = new_data_dir / ".legacy_env_migration_complete"
    if migration_marker.exists():
        return False
    legacy_dir = legacy_data_dir or (
        Path.home() / "Library" / "Application Support" / DEFAULT_APPLICATION_NAME
    )
    source = legacy_dir / ".env"
    destination = new_data_dir / ".env"
    new_data_dir.mkdir(parents=True, exist_ok=True)
    copied = False
    try:
        if not destination.exists() and source.is_file():
            with source.open("rb") as source_file, destination.open(
                "xb"
            ) as destination_file:
                shutil.copyfileobj(source_file, destination_file)
            destination.chmod(0o600)
            copied = True
    except FileExistsError:
        copied = False
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    migration_marker.touch(mode=0o600, exist_ok=True)
    return copied


def application_data_dir() -> Path:
    """Return the writable macOS Application Support directory for packaged data."""
    path = Path.home() / "Library" / "Application Support" / APPLICATION_NAME
    for directory in (
        path,
        path / "Logs",
        path / "Cache",
        path / "runtime" / "Incoming",
        path / "runtime" / "Completed",
        path / "runtime" / "NeedsReview",
        path / "runtime" / "Error",
        path / "runtime" / "Logs",
        path / "runtime" / "Data",
    ):
        directory.mkdir(parents=True, exist_ok=True)
    copy_legacy_env_if_needed(path)
    return path


PROJECT_ROOT = Path(__file__).resolve().parent
APP_DIR = PROJECT_ROOT
PROJECT_DIR = PROJECT_ROOT
RESOURCE_DIR = resource_path(".")
USER_DATA_DIR = application_data_dir()
RUNTIME_DIR = USER_DATA_DIR / "runtime" if IS_PACKAGED else APP_DIR / "runtime"
INPUT_DIR = RUNTIME_DIR / "Incoming"
OUTPUT_DIR = RUNTIME_DIR / "Completed"
REVIEW_DIR = RUNTIME_DIR / "NeedsReview"
ERROR_DIR = RUNTIME_DIR / "Error"
LOG_DIR = RUNTIME_DIR / "Logs"
DATA_DIR = RUNTIME_DIR / "Data"
HISTORY_DB = DATA_DIR / "image_history.sqlite3"
PROMPT_FILE = resource_path("prompts/mls_production.txt")
LEARNED_RULES_FILE = USER_DATA_DIR / "learned_rules.json"
FEEDBACK_HISTORY_FILE = USER_DATA_DIR / "feedback_history.jsonl"

PROGRAM_VERSION = "4.0.3"
RELEASE_DATE = "2026-08-20"
PROMPT_VERSION = "V4.0.3"
MODEL = "gpt-image-2"
QUALITY = "low"
QUALITY_OPTIONS = ("low", "medium", "high")
QUALITY_MODES = ("smart",) + QUALITY_OPTIONS
QUALITY_SETTING = "ui/quality"
SUPPORTED_EXTENSIONS = {".jpg", ".jpeg", ".png"}
LANDSCAPE_SIZE = "1536x1024"
PORTRAIT_SIZE = "1024x1536"
SQUARE_SIZE = "1024x1024"
IMAGES_EDIT_API_PATH = "/v1/images/edits"
API_OUTPUT_FORMAT = "png"
JPEG_OUTPUT_QUALITY = 100
REVIEW_PDF_VERSION = "V4.0.3"
REVIEW_PDF_MAX_IMAGE_EDGE = 1200
DPI = (300, 300)
OBSERVED_ESTIMATED_COST_PER_IMAGE = 0.28 / 6.0
LOW_ESTIMATED_COST_PER_IMAGE = OBSERVED_ESTIMATED_COST_PER_IMAGE * 0.5
HIGH_ESTIMATED_COST_PER_IMAGE = OBSERVED_ESTIMATED_COST_PER_IMAGE * 2.0
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


def api_environment_path() -> Path:
    """Return the sole API-key file for the current execution mode."""
    return (
        USER_DATA_DIR / ".env"
        if IS_PACKAGED
        else PROJECT_ROOT / ".env"
    )


def execution_mode_name() -> str:
    return "packaged" if IS_PACKAGED else "source"


def missing_api_key_message() -> str:
    if IS_PACKAGED:
        return (
            "OpenAI API key not configured.\n\n"
            f"Add your API key in:\n{api_environment_path()}\n\n"
            "Required format:\nOPENAI_API_KEY=your_key_here\n\n"
            "Demo Mode can still be used without an API key."
        )
    return (
        "OPENAI_API_KEY is missing.\n\n"
        f"Add it to the repository environment file:\n{api_environment_path()}"
    )


def configure_startup_logging() -> Path:
    """Create Finder-friendly diagnostics outside the read-only app bundle."""
    startup_log_dir = USER_DATA_DIR / "Logs"
    startup_log_dir.mkdir(parents=True, exist_ok=True)
    log_path = startup_log_dir / "application.log"
    logging.basicConfig(
        filename=log_path,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    logging.info("Application start: %s v%s", APPLICATION_NAME, PROGRAM_VERSION)
    logging.info("PyInstaller environment: %s", IS_PACKAGED)
    logging.info("Resource directory: %s", RESOURCE_DIR)
    logging.info("User data directory: %s", USER_DATA_DIR)
    logging.info("Prompt resource: %s", PROMPT_FILE)
    if PROMPT_FILE.is_file():
        logging.info(
            "Production Prompt Version: %s source=%s sha256=%s",
            PROMPT_VERSION,
            PROMPT_FILE,
            hashlib.sha256(PROMPT_FILE.read_bytes()).hexdigest(),
        )

    def log_uncaught_exception(exception_type, exception, traceback):
        logging.critical(
            "Uncaught exception",
            exc_info=(exception_type, exception, traceback),
        )
        sys.__excepthook__(exception_type, exception, traceback)

    sys.excepthook = log_uncaught_exception
    atexit.register(lambda: logging.info("Application exit"))
    return log_path


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
        return False, missing_api_key_message()
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
        return False, missing_api_key_message()
    return True, "OpenAI API key loaded"


def load_project_api_key() -> tuple[str | None, str]:
    """Load one mode-specific .env without exposing or logging its secret."""
    env_path = api_environment_path()
    if IS_PACKAGED:
        # Finder-launched builds must never inherit a Terminal or repository key.
        api_key = str(dotenv_values(env_path).get("OPENAI_API_KEY", "") or "").strip()
    else:
        load_dotenv(env_path, override=False)
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
    valid, message = validate_api_key(api_key)
    logging.info(
        "API configuration: mode=%s env_path=%s key_found=%s",
        execution_mode_name(),
        env_path,
        valid,
    )
    return (api_key if valid else None), message


def api_key_allows_processing(api_key: str | None, demo_mode: bool) -> bool:
    return demo_mode or api_key is not None


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


def editing_agent() -> EditingAgent:
    """Return the zero-API V4.0.3 rule-memory layer using stable app support."""
    return EditingAgent(USER_DATA_DIR)


def build_edit_instruction(base_prompt: str, input_file: Path) -> RuleSelection:
    """Append only relevant approved local lessons; master prompt remains first."""
    selection = editing_agent().build_instruction(base_prompt, input_file)
    logging.info(
        "Local editing agent: filename=%s context=%s applied_rules=%s suppressed_rules=%s "
        "database_hash=%s schema_version=%s conflicts=%s api_calls=0",
        input_file.name,
        ",".join(selection.context_categories),
        ",".join(selection.applied_rule_ids) or "none",
        ",".join(selection.suppressed_rule_ids) or "none",
        selection.database_hash,
        selection.database_version,
        ",".join(selection.conflicts) or "none",
    )
    return selection


def normalize_quality_setting(value: Any, default: str = "low") -> str:
    """Keep an existing explicit choice; never infer a more expensive quality."""
    candidate = str(value).strip().lower() if value is not None else ""
    return candidate if candidate in QUALITY_OPTIONS else default


def normalize_quality_mode(value: Any, default: str = "low") -> str:
    """Normalize the UI mode without allowing the OpenAI API's Auto quality."""
    candidate = str(value).strip().lower() if value is not None else ""
    return candidate if candidate in QUALITY_MODES else default


def quality_for_image(quality_mode: str, input_file: Path) -> tuple[str, WindowPullAssessment | None]:
    """Resolve Smart locally; explicit Low/Medium/High always override it."""
    mode = normalize_quality_mode(quality_mode)
    if mode != "smart":
        return mode, None
    assessment = assess_window_pull(input_file)
    return select_smart_quality(assessment), assessment


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
    quality: str | None = None,
) -> tuple[bytes, str, ApiUsage]:
    quality = normalize_quality_setting(quality, QUALITY)
    requested_size = choose_native_size(input_file)
    last_error: Exception | None = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            logging.info(
                "OpenAI direct image edit request: api_path=%s model=%s "
                "quality=%s requested_size=%s output_format=%s attempt=%d/%d",
                IMAGES_EDIT_API_PATH,
                MODEL,
                quality,
                requested_size,
                API_OUTPUT_FORMAT,
                attempt,
                MAX_RETRIES,
            )
            with input_file.open("rb") as image_file:
                response = client.images.edit(
                    model=MODEL,
                    image=image_file,
                    prompt=full_prompt,
                    size=requested_size,
                    quality=quality,
                    output_format=API_OUTPUT_FORMAT,
                )

            image_bytes = base64.b64decode(response.data[0].b64_json)
            with Image.open(BytesIO(image_bytes)) as returned_image:
                returned_size = (
                    f"{returned_image.width}x{returned_image.height}"
                )
                returned_format = returned_image.format or API_OUTPUT_FORMAT
            logging.info(
                "OpenAI direct image edit response: api_path=%s model=%s "
                "quality=%s requested_size=%s returned_size=%s "
                "output_format=%s attempt=%d/%d cost_basis=estimated",
                IMAGES_EDIT_API_PATH,
                MODEL,
                quality,
                requested_size,
                returned_size,
                returned_format.lower(),
                attempt,
                MAX_RETRIES,
            )
            usage = extract_usage(response)
            return image_bytes, requested_size, usage

        except Exception as error:
            last_error = error
            if is_transient_error(error) and attempt < MAX_RETRIES:
                delay = RETRY_BASE_DELAY_SECONDS * (2 ** (attempt - 1))
                logging.warning(
                    "Direct image edit attempt failed transiently; retrying: "
                    "api_path=%s attempt=%d/%d delay_seconds=%d error_type=%s",
                    IMAGES_EDIT_API_PATH,
                    attempt,
                    MAX_RETRIES,
                    delay,
                    type(error).__name__,
                )
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


def encode_final_jpeg(
    image: Image.Image,
    input_file: Path,
) -> tuple[bytes, int]:
    """
    Convert to one maximum-quality JPEG without a file-size target.

    The output retains the supplied image dimensions.  JPEG is inherently
    compressed, but no quality reduction, resize, or repeat encode is applied
    to make the file smaller.
    """
    image = image.convert("RGB")
    exif = get_preserved_exif(input_file, image.size)
    icc_profile = get_icc_profile(input_file)
    buffer = BytesIO()
    save_kwargs: dict[str, Any] = {
        "format": "JPEG",
        "quality": JPEG_OUTPUT_QUALITY,
        "optimize": False,
        "progressive": False,
        "subsampling": 0,
        "dpi": DPI,
    }

    if exif:
        save_kwargs["exif"] = exif
    if icc_profile:
        save_kwargs["icc_profile"] = icc_profile

    image.save(buffer, **save_kwargs)
    return buffer.getvalue(), JPEG_OUTPUT_QUALITY



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


def apply_premium_finish(generated_image: Image.Image) -> Image.Image:
    """Apply the conservative, local-only V3 finish without changing geometry.

    This intentionally uses only Pillow and a restrained existing UnsharpMask
    configuration. It performs no analysis, networking, resizing, cropping,
    object removal, or content generation.
    """
    rgb = generated_image.convert("RGB")
    # A light blend avoids the aggressive texture amplification rejected in
    # earlier experiments while improving edge definition on finished output.
    refined = rgb.filter(
        ImageFilter.UnsharpMask(radius=0.45, percent=12, threshold=12)
    )
    return Image.blend(rgb, refined, 0.12)


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
                message TEXT,
                learned_rule_ids TEXT,
                learned_rules_hash TEXT,
                learned_rules_schema_version INTEGER,
                api_request_count INTEGER
            )
            """
        )
        existing_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(image_history)")
        }
        for name, sql_type in (
            ("learned_rule_ids", "TEXT"),
            ("learned_rules_hash", "TEXT"),
            ("learned_rules_schema_version", "INTEGER"),
            ("api_request_count", "INTEGER"),
        ):
            if name not in existing_columns:
                connection.execute(f"ALTER TABLE image_history ADD COLUMN {name} {sql_type}")
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
    rule_selection: RuleSelection | None = None,
    api_request_count: int = 0,
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
                message,
                learned_rule_ids,
                learned_rules_hash,
                learned_rules_schema_version,
                api_request_count
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                ",".join(rule_selection.applied_rule_ids) if rule_selection else "",
                rule_selection.database_hash if rule_selection else "",
                rule_selection.database_version if rule_selection else None,
                api_request_count,
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
        "window_pull_classification",
        "window_pull_reason",
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
        "image_preparation_seconds",
        "api_latency_seconds",
        "response_decode_seconds",
        "premium_finish_seconds",
        "filesystem_write_seconds",
        "learned_rule_ids",
        "learned_rules_hash",
        "learned_rules_schema_version",
        "api_request_count",
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
    print(f"MYESTATEPICS MLS AI — PRODUCTION v{PROGRAM_VERSION}")
    print(f"Images found: {len(candidates)}")
    print(f"Already completed/reviewed and skipped: {skipped}")
    print(f"Images to process: {len(pending)}")
    print(f"Model: {MODEL} | Quality: {QUALITY}")
    print(
        f"Fallback estimated cost: "
        f"${len(pending) * OBSERVED_ESTIMATED_COST_PER_IMAGE:.2f}"
    )
    print("Outputs: JPEG, same filename, quality 100 / 4:4:4")
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
    local_agent = editing_agent()
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
        rule_selection: RuleSelection | None = None

        try:
            metrics = analyze_input(input_file)
            rule_selection = build_edit_instruction(base_prompt, input_file)
            adaptive_prompt = build_adaptive_addendum(metrics)
            full_prompt = f"{rule_selection.instruction}\n\n{adaptive_prompt}"

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

            jpeg_bytes, jpeg_quality = encode_final_jpeg(
                generated_image,
                input_file,
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
                    "learned_rule_ids": ",".join(rule_selection.applied_rule_ids),
                    "learned_rules_hash": rule_selection.database_hash,
                    "learned_rules_schema_version": rule_selection.database_version,
                    "api_request_count": 1,
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
                rule_selection=rule_selection,
                api_request_count=1,
            )
            local_agent.record_applied(
                rule_selection.applied_rule_ids,
                filename=input_file.name,
                batch_id=run_id,
                quality=QUALITY,
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
    selected: int = 0
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
    before_pdf: Path | None = None
    after_pdf: Path | None = None
    before_pdf_error: str = ""
    after_pdf_error: str = ""
    review_pdf_error: str = ""
    quality: str = "low"
    elapsed_seconds: float = 0.0

    @property
    def images_processed(self) -> int:
        return self.completed + self.review + self.failed

    @property
    def successful(self) -> int:
        return self.completed + self.review

    @property
    def failed_or_skipped(self) -> int:
        return self.failed + self.skipped

    @property
    def average_cost_per_image(self) -> float:
        if not self.images_processed:
            return 0.0
        return self.fallback_cost / self.images_processed


@dataclass(frozen=True)
class OutputWriteEvidence:
    """Immutable filesystem evidence captured after an atomic output write."""

    path: Path
    exists: bool
    byte_size: int
    sha256: str
    timestamp: str
    error: str = ""


def _review_pdf_image_bytes(image_path: Path) -> tuple[bytes, int, int]:
    """Prepare a locally downsampled review copy without altering any JPEG output."""
    with Image.open(image_path) as image:
        review_image = image.convert("RGB")
        review_image.thumbnail(
            (REVIEW_PDF_MAX_IMAGE_EDGE, REVIEW_PDF_MAX_IMAGE_EDGE),
            Image.Resampling.LANCZOS,
        )
        buffer = BytesIO()
        review_image.save(
            buffer,
            format="JPEG",
            quality=95,
            subsampling=0,
            optimize=False,
        )
        return buffer.getvalue(), review_image.width, review_image.height


def _draw_review_filename(
    pdf: canvas.Canvas, filename: str, x: float, y: float, width: float
) -> None:
    """Draw a compact, deterministic filename caption below a contact-sheet item."""
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica", 8)
    max_chars = max(18, int(width / 4.5))
    lines = [filename[index : index + max_chars] for index in range(0, len(filename), max_chars)]
    for line_index, line in enumerate(lines[:3]):
        pdf.drawCentredString(x + width / 2, y - (line_index * 9), line)


def _draw_after_review_audit(
    pdf: canvas.Canvas,
    filename: str,
    audit: dict[str, str] | None,
    x: float,
    y: float,
    width: float,
) -> None:
    """Render saved per-image audit facts without re-running Smart analysis."""
    audit = audit or {}
    quality = audit.get("quality", "—").upper() or "—"
    classification = audit.get("window_pull_classification", "MANUAL_OVERRIDE")
    smart = "MANUAL" if classification == "MANUAL_OVERRIDE" else classification
    reason = audit.get("failure_reason") or audit.get(
        "window_pull_reason", "Manual quality selection."
    )
    max_chars = max(24, int(width / 4.7))
    filename_lines = [filename[index : index + max_chars] for index in range(0, len(filename), max_chars)] or [filename]
    reason = reason if len(reason) <= max_chars else f"{reason[:max_chars - 1]}…"
    pdf.setFillColor(colors.black)
    pdf.setFont("Helvetica", 7.5)
    lines = [*filename_lines[:2], f"Quality: {quality}", f"Smart: {smart}", f"Reason: {reason}"]
    for line_index, line in enumerate(lines):
        pdf.drawCentredString(x + width / 2, y - (line_index * 8.5), line)


def _draw_review_image_slot(
    pdf: canvas.Canvas,
    image_path: Path | None,
    filename: str,
    x: float,
    y: float,
    width: float,
    height: float,
    *,
    failed: bool,
    after: bool,
    audit: dict[str, str] | None,
) -> None:
    """Draw one image or a failed-processing placeholder without changing order."""
    caption_height = 64 if after else 30
    image_y = y + caption_height
    image_height = height - caption_height
    output_missing = image_path is None or not image_path.is_file()
    failed = failed or output_missing
    effective_audit = dict(audit or {})
    if output_missing and not effective_audit.get("failure_reason"):
        effective_audit["failure_reason"] = (
            "OUTPUT MISSING: Output file unavailable when review PDF was generated."
        )
    if failed:
        pdf.setStrokeColor(colors.HexColor("#b91c1c"))
        pdf.setFillColor(colors.HexColor("#fef2f2"))
        pdf.rect(x, image_y, width, image_height, fill=1, stroke=1)
        pdf.setFillColor(colors.HexColor("#991b1b"))
        pdf.setFont("Helvetica-Bold", 15)
        pdf.drawCentredString(x + width / 2, image_y + image_height / 2, "PROCESSING FAILED")
    else:
        image_bytes, pixel_width, pixel_height = _review_pdf_image_bytes(image_path)
        scale = min(width / pixel_width, image_height / pixel_height)
        draw_width = pixel_width * scale
        draw_height = pixel_height * scale
        draw_x = x + (width - draw_width) / 2
        draw_y = image_y + (image_height - draw_height) / 2
        pdf.drawImage(
            ImageReader(BytesIO(image_bytes)),
            draw_x,
            draw_y,
            width=draw_width,
            height=draw_height,
            preserveAspectRatio=True,
            mask="auto",
        )
    if after:
        _draw_after_review_audit(pdf, filename, effective_audit, x, y + 50, width)
    else:
        _draw_review_filename(pdf, filename, x, y + 18, width)


def _write_review_contact_sheet(
    pdf_path: Path,
    ordered_inputs: list[Path],
    output_paths: dict[Path, Path],
    audit_records: dict[Path, dict[str, str]],
    *,
    after: bool,
) -> None:
    """Write a four-images-per-page Before or After PDF with stable positions."""
    page_width, page_height = landscape(letter)
    margin = 24
    gap = 12
    columns = 2
    rows = 2
    cell_width = (page_width - (2 * margin) - gap) / columns
    cell_height = (page_height - (2 * margin) - gap) / rows
    pdf = canvas.Canvas(str(pdf_path), pagesize=(page_width, page_height), pageCompression=0)
    for index, input_path in enumerate(ordered_inputs):
        if index and index % (columns * rows) == 0:
            pdf.showPage()
        slot = index % (columns * rows)
        row = slot // columns
        column = slot % columns
        x = margin + column * (cell_width + gap)
        y = page_height - margin - ((row + 1) * cell_height) - (row * gap)
        output_path = output_paths.get(input_path.resolve()) if after else input_path
        _draw_review_image_slot(
            pdf,
            output_path,
            input_path.name,
            x,
            y,
            cell_width,
            cell_height,
            failed=after and output_path is None,
            after=after,
            audit=audit_records.get(input_path.resolve()),
        )
    pdf.save()


def load_review_pdf_audit(log_path: Path | None) -> dict[Path, dict[str, str]]:
    """Read existing batch CSV audit rows; this function never analyzes images."""
    if log_path is None or not log_path.is_file():
        return {}
    try:
        with log_path.open(newline="", encoding="utf-8") as file:
            rows = csv.DictReader(file)
            return {
                (INPUT_DIR / row["filename"]).resolve(): {
                    "quality": row.get("quality", ""),
                    "window_pull_classification": row.get("window_pull_classification", "MANUAL_OVERRIDE"),
                    "window_pull_reason": row.get("window_pull_reason", "Manual quality selection."),
                    "failure_reason": (
                        row.get("message", "")
                        if row.get("status") == "FAILED"
                        else ""
                    ),
                }
                for row in rows
                if row.get("filename")
            }
    except Exception as error:
        logging.warning("Review PDF audit unavailable; captions will omit Smart details: %s", error)
        return {}


def generate_batch_review_pdfs(
    input_files: list[Path], output_paths: dict[Path, Path], run_id: str,
    audit_records: dict[Path, dict[str, str]] | None = None,
) -> tuple[Path, Path]:
    """Create local-only Before/After review PDFs for a completed batch."""
    ordered_inputs = sorted((path.resolve() for path in input_files), key=lambda path: path.name.casefold())
    review_root = OUTPUT_DIR / "Batch Reviews"
    review_directory = review_root / run_id
    suffix = 2
    while review_directory.exists():
        review_directory = review_root / f"{run_id}-{suffix}"
        suffix += 1
    review_directory.mkdir(parents=True, exist_ok=False)
    before_pdf = review_directory / f"MyEstatePics_{REVIEW_PDF_VERSION}_BEFORE.pdf"
    after_pdf = review_directory / f"MyEstatePics_{REVIEW_PDF_VERSION}_AFTER.pdf"
    audit_records = audit_records or {}
    _write_review_contact_sheet(before_pdf, ordered_inputs, output_paths, audit_records, after=False)
    _write_review_contact_sheet(after_pdf, ordered_inputs, output_paths, audit_records, after=True)
    logging.info(
        "Batch review PDFs created locally: before=%s after=%s images=%d",
        before_pdf,
        after_pdf,
        len(ordered_inputs),
    )
    return before_pdf, after_pdf


def _review_pdf_paths(run_id: str) -> tuple[Path, Path]:
    """Reserve the local review directory once, keeping both PDF names aligned."""
    review_root = OUTPUT_DIR / "Batch Reviews"
    review_directory = review_root / run_id
    suffix = 2
    while review_directory.exists():
        review_directory = review_root / f"{run_id}-{suffix}"
        suffix += 1
    review_directory.mkdir(parents=True, exist_ok=False)
    return (
        review_directory / f"MyEstatePics_{REVIEW_PDF_VERSION}_BEFORE.pdf",
        review_directory / f"MyEstatePics_{REVIEW_PDF_VERSION}_AFTER.pdf",
    )


def finalize_batch_review_pdfs(
    summary: BatchSummary,
    input_files: list[Path],
    output_paths: dict[Path, Path],
    run_id: str,
    successful_output_evidence: dict[Path, OutputWriteEvidence] | None = None,
) -> None:
    """Finalize each local review PDF independently without affecting outputs."""
    ordered_inputs = sorted(
        (path.resolve() for path in input_files), key=lambda path: path.name.casefold()
    )
    audit_records = load_review_pdf_audit(summary.log_path)
    for input_path, write_evidence in (successful_output_evidence or {}).items():
        resolved_input = input_path.resolve()
        final_evidence = _output_file_evidence(
            write_evidence.path, stage="finalization"
        )
        if write_evidence.exists and not final_evidence.exists:
            message = (
                "OUTPUT_MISSING_AFTER_SUCCESS: "
                f"successful_write_exists=true successful_write_bytes="
                f"{write_evidence.byte_size} successful_write_sha256="
                f"{write_evidence.sha256 or 'unavailable'} successful_write_timestamp="
                f"{write_evidence.timestamp} final_path={write_evidence.path}"
            )
            logging.error("%s", message)
            audit_records.setdefault(resolved_input, {})["failure_reason"] = message
    try:
        before_pdf, after_pdf = _review_pdf_paths(run_id)
    except Exception as error:
        message = f"Review PDF directory creation failed: {type(error).__name__}: {error}"
        summary.before_pdf_error = message
        summary.after_pdf_error = message
        summary.review_pdf_error = message
        logging.exception(message)
        logging.info(
            "Batch completion: selected=%d successful=%d failed_skipped=%d "
            "before_pdf=failed after_pdf=failed",
            summary.selected,
            summary.successful,
            summary.failed_or_skipped,
        )
        return

    for label, pdf_path, after in (
        ("BEFORE", before_pdf, False),
        ("AFTER", after_pdf, True),
    ):
        try:
            _write_review_contact_sheet(
                pdf_path, ordered_inputs, output_paths, audit_records, after=after
            )
            if after:
                summary.after_pdf = pdf_path
            else:
                summary.before_pdf = pdf_path
            logging.info(
                "Batch review PDF created locally: kind=%s path=%s images=%d",
                label,
                pdf_path,
                len(ordered_inputs),
            )
        except Exception as error:
            message = (
                f"{label} review PDF generation failed: {type(error).__name__}: {error}"
            )
            if after:
                summary.after_pdf_error = message
            else:
                summary.before_pdf_error = message
            logging.exception(message)

    summary.review_pdf_error = " | ".join(
        error
        for error in (summary.before_pdf_error, summary.after_pdf_error)
        if error
    )
    logging.info(
        "Batch completion: selected=%d successful=%d failed_skipped=%d "
        "before_pdf=%s after_pdf=%s",
        summary.selected,
        summary.successful,
        summary.failed_or_skipped,
        "created" if summary.before_pdf else "failed",
        "created" if summary.after_pdf else "failed",
    )


class CancellationToken:
    """Thread-safe cooperative cancellation for a sequential image batch."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()


def estimated_cost_per_image(quality: str) -> float:
    """Fallback estimate scaled from the preserved observed medium-quality cost."""
    if quality == "low":
        return LOW_ESTIMATED_COST_PER_IMAGE
    if quality == "high":
        return HIGH_ESTIMATED_COST_PER_IMAGE
    return OBSERVED_ESTIMATED_COST_PER_IMAGE


FOLDER_SETTING_KEYS = (
    "folders/incoming",
    "folders/completed",
    "folders/needs_review",
    "folders/error",
    "folders/logs",
)
INCOMING_FOLDER_SETTING = "folders/incoming"
COMPLETED_FOLDER_SETTING = "folders/completed"
LAST_GOOD_INCOMING_SETTING = "folders/last_good_incoming"
LAST_GOOD_COMPLETED_SETTING = "folders/last_good_completed"
ADVANCED_FOLDERS_SETTING = "ui/advanced_folders_expanded"


@dataclass(frozen=True)
class FolderIntegrityResult:
    valid: bool
    warnings: tuple[str, ...] = ()
    error: str = ""


def _supported_file_count(directory: Path) -> int:
    try:
        return sum(
            1
            for path in Path(directory).iterdir()
            if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
        )
    except (OSError, PermissionError):
        return 0


def validate_primary_folders(incoming: Path, completed: Path) -> FolderIntegrityResult:
    """Validate the two user-facing folders and flag likely role reversal."""
    incoming = Path(incoming).expanduser().resolve()
    completed = Path(completed).expanduser().resolve()
    if incoming == completed:
        return FolderIntegrityResult(False, error="Incoming and Completed must use different folders.")
    for label, path in (("Incoming", incoming), ("Completed", completed)):
        if not path.is_dir():
            return FolderIntegrityResult(False, error=f"{label} folder does not exist: {path}")
        if not os.access(path, os.R_OK | os.X_OK):
            return FolderIntegrityResult(False, error=f"{label} folder is not readable: {path}")

    incoming_name = incoming.name.casefold().replace(" ", "")
    completed_name = completed.name.casefold().replace(" ", "")
    incoming_looks_final = incoming_name.endswith("final") and not incoming_name.endswith("prefinal")
    completed_looks_source = completed_name.endswith("prefinal") or completed_name.endswith("wip")
    warnings: list[str] = []
    if incoming_looks_final and completed_looks_source:
        warnings.append(
            "Folder names suggest Incoming and Completed may be reversed "
            f"(Incoming: {incoming.name}; Completed: {completed.name})."
        )
    incoming_count = _supported_file_count(incoming)
    completed_count = _supported_file_count(completed)
    if incoming_count == 0 and completed_count > 0:
        warnings.append(
            "Incoming contains no supported images while Completed contains "
            f"{completed_count}; verify the folder roles."
        )
    return FolderIntegrityResult(True, tuple(warnings))


def _atomic_update_preferences(preferences_path: Path, values: dict[str, str]) -> None:
    """Atomically update INI values without exposing a partial folder pair."""
    preferences_path = Path(preferences_path)
    parser = configparser.ConfigParser(interpolation=None)
    parser.optionxform = str
    if preferences_path.exists():
        parser.read(preferences_path, encoding="utf-8")
    for key, value in values.items():
        section, option = key.split("/", 1)
        if not parser.has_section(section):
            parser.add_section(section)
        parser.set(section, option, value)
    preferences_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_name: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=preferences_path.parent,
            prefix=f".{preferences_path.name}.", suffix=".tmp", delete=False,
        ) as temporary:
            temporary_name = temporary.name
            parser.write(temporary, space_around_delimiters=False)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, preferences_path)
        temporary_name = None
        try:
            directory_fd = os.open(preferences_path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError:
            pass
    finally:
        if temporary_name:
            Path(temporary_name).unlink(missing_ok=True)


def save_primary_folder_settings(
    preferences_path: Path, *, incoming: Path, completed: Path
) -> None:
    """Persist the validated primary-folder pair and its last-known-good copy."""
    result = validate_primary_folders(incoming, completed)
    if not result.valid:
        raise ValueError(result.error)
    values = {
        INCOMING_FOLDER_SETTING: str(Path(incoming)),
        COMPLETED_FOLDER_SETTING: str(Path(completed)),
        LAST_GOOD_INCOMING_SETTING: str(Path(incoming)),
        LAST_GOOD_COMPLETED_SETTING: str(Path(completed)),
    }
    _atomic_update_preferences(preferences_path, values)


def load_primary_folder_settings(
    settings: Any, default_incoming: Path, default_completed: Path
) -> tuple[Path, Path, FolderIntegrityResult]:
    incoming = normalize_path_setting(
        raw_ini_setting(settings, INCOMING_FOLDER_SETTING, str(default_incoming)),
        default_incoming,
        INCOMING_FOLDER_SETTING,
    )
    completed = normalize_path_setting(
        raw_ini_setting(settings, COMPLETED_FOLDER_SETTING, str(default_completed)),
        default_completed,
        COMPLETED_FOLDER_SETTING,
    )
    result = validate_primary_folders(incoming, completed)
    if result.valid and not result.warnings:
        return incoming, completed, result
    last_incoming = normalize_path_setting(
        raw_ini_setting(settings, LAST_GOOD_INCOMING_SETTING, str(default_incoming)),
        default_incoming,
        LAST_GOOD_INCOMING_SETTING,
    )
    last_completed = normalize_path_setting(
        raw_ini_setting(settings, LAST_GOOD_COMPLETED_SETTING, str(default_completed)),
        default_completed,
        LAST_GOOD_COMPLETED_SETTING,
    )
    last_result = validate_primary_folders(last_incoming, last_completed)
    if last_result.valid and not last_result.warnings:
        return last_incoming, last_completed, result
    return Path(default_incoming), Path(default_completed), result


def scanning_status_text(incoming: Path, count: int) -> str:
    noun = "image" if count == 1 else "images"
    return f"Scanning: {Path(incoming)}\n{count} supported {noun} found"


def scan_supported_images(input_dir: Path) -> list[Path]:
    """Read the current Incoming directory without using cached or historical state."""
    input_dir = Path(input_dir)
    input_dir.mkdir(parents=True, exist_ok=True)
    return sorted(
        path.resolve()
        for path in input_dir.iterdir()
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
    )


def scan_and_select_all(input_dir: Path) -> tuple[list[Path], set[Path]]:
    """Scan Incoming and return the deterministic default selection."""
    available_files = scan_supported_images(input_dir)
    return available_files, set(available_files)


def selected_batch_cost(
    selected_files: list[Path] | set[Path],
    quality: str,
    demo_mode: bool,
) -> float:
    if demo_mode:
        return 0.0
    if quality == "smart":
        return sum(
            estimated_cost_per_image(quality_for_image("smart", path)[0])
            for path in selected_files
        )
    return len(selected_files) * estimated_cost_per_image(quality)


def update_checked_selection(
    selected_files: set[Path], path: Path, checked: bool
) -> set[Path]:
    """Return an updated checked-file set for a single table-row change."""
    updated = set(selected_files)
    if checked:
        updated.add(Path(path))
    else:
        updated.discard(Path(path))
    return updated


def requires_paid_confirmation(demo_mode: bool) -> bool:
    return not demo_mode


def validate_folder_configuration(
    input_dir: Path,
    output_dir: Path,
    review_dir: Path,
    error_dir: Path,
) -> tuple[bool, str]:
    """Reject folder combinations that could overwrite or misroute source images."""
    named_paths = {
        "Incoming": Path(input_dir).expanduser().resolve(),
        "Completed": Path(output_dir).expanduser().resolve(),
        "NeedsReview": Path(review_dir).expanduser().resolve(),
        "Error": Path(error_dir).expanduser().resolve(),
    }
    invalid_pairs = (
        ("Incoming", "Completed"),
        ("Incoming", "NeedsReview"),
        ("Incoming", "Error"),
        ("Completed", "NeedsReview"),
        ("Completed", "Error"),
        ("NeedsReview", "Error"),
    )
    for first, second in invalid_pairs:
        if named_paths[first] == named_paths[second]:
            return False, f"{first} and {second} must use different folders."
    return True, "Folder configuration is valid."


def paid_confirmation_text(
    image_count: int,
    quality: str,
    estimated_cost: float,
) -> str:
    return (
        f"Images: {image_count}\n"
        f"Quality: {quality.title()}\n"
        f"Estimated cost: ${estimated_cost:.2f}\n"
        "Demo Mode: Off\n"
        f"Prompt: MLS Production {PROMPT_VERSION}"
    )


def retry_confirmation_text(filename: str, quality: str) -> str:
    return (
        f"Image: {filename}\n"
        f"Quality: {quality.title()}\n"
        f"Estimated additional cost: ${estimated_cost_per_image(quality):.2f}\n\n"
        "Queue this image for retry? Processing will not start automatically."
    )


def normalize_path_setting(value: Any, default: Path, setting_key: str) -> Path:
    """Safely recover one macOS path without ever splitting punctuation."""
    candidate = value
    if isinstance(candidate, (list, tuple)):
        if len(candidate) == 1:
            candidate = candidate[0]
            logging.warning("Recovered one-item legacy path setting: key=%s", setting_key)
        else:
            logging.warning("Rejected malformed path setting: key=%s type=%s", setting_key, type(value).__name__)
            return Path(default)
    if candidate is None:
        logging.warning("Missing path setting: key=%s; using fallback", setting_key)
        return Path(default)
    try:
        text = os.fspath(candidate).strip() if isinstance(candidate, os.PathLike) else str(candidate).strip()
        if not text or text.startswith("[") or text.startswith("("):
            raise ValueError("empty or malformed path value")
        return Path(text)
    except (TypeError, ValueError):
        logging.warning("Rejected malformed path setting: key=%s; using fallback", setting_key)
        return Path(default)


def raw_ini_setting(settings: Any, setting_key: str, default: Any) -> Any:
    """Read a raw INI scalar before Qt can coerce comma-containing paths to a list."""
    file_name_method = getattr(settings, "fileName", None)
    if callable(file_name_method):
        try:
            file_name = Path(file_name_method())
            if file_name.is_file():
                parser = configparser.ConfigParser(interpolation=None)
                parser.read(file_name, encoding="utf-8")
                section, option = setting_key.split("/", 1)
                if parser.has_option(section, option):
                    return parser.get(section, option, raw=True)
        except (OSError, TypeError, ValueError, configparser.Error):
            logging.warning("Could not read raw INI setting: key=%s", setting_key)
    return settings.value(setting_key, default)


def load_folder_settings(settings: Any, defaults: tuple[Path, ...]) -> tuple[Path, ...]:
    return tuple(
        normalize_path_setting(raw_ini_setting(settings, key, str(default)), default, key)
        for key, default in zip(FOLDER_SETTING_KEYS, defaults)
    )


def save_folder_setting(settings: Any, index: int, path: Path) -> None:
    if index < 2:
        raise ValueError(
            "Incoming and Completed must be saved through their explicit setters."
        )
    settings.setValue(FOLDER_SETTING_KEYS[index], str(Path(path)))


def load_boolean_setting(settings: Any, key: str, default: bool = False) -> bool:
    value = settings.value(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


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


def demo_runtime_paths() -> tuple[Path, Path, Path, Path, Path]:
    """Return isolated demo destinations rooted beside the application."""
    demo_root = (
        USER_DATA_DIR / "runtime" / "Demo"
        if IS_PACKAGED
        else APP_DIR / "runtime" / "Demo"
    )
    return (
        demo_root / "Completed",
        demo_root / "NeedsReview",
        demo_root / "Error",
        demo_root / "Logs",
        demo_root / "Data",
    )


def configure_demo_runtime_paths(input_dir: Path) -> None:
    """Use the real Incoming folder with demo-only output, log, and data folders."""
    global INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, ERROR_DIR, LOG_DIR, DATA_DIR, HISTORY_DB
    completed, review, error, logs, data = demo_runtime_paths()
    INPUT_DIR = Path(input_dir)
    OUTPUT_DIR = completed
    REVIEW_DIR = review
    ERROR_DIR = error
    LOG_DIR = logs
    DATA_DIR = data
    HISTORY_DB = DATA_DIR / "image_history.sqlite3"


def existing_output_destination(filename: str) -> str | None:
    """Return the current output folder containing this filename, without history."""
    for directory, destination in (
        (OUTPUT_DIR, "Completed"),
        (REVIEW_DIR, "NeedsReview"),
        (ERROR_DIR, "Error"),
    ):
        if (directory / filename).is_file():
            return destination
    return None


def selection_status(input_file: Path) -> str:
    """Build a fresh GUI status from current filesystem state only."""
    if not input_file.is_file():
        return "Missing"
    destination = existing_output_destination(input_file.name)
    if destination:
        return f"Already exists in {destination}"
    return "Selected — ready"


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
        if existing_output_destination(file.name) is None
    ]
    return pending, len(candidates) - len(pending)


def _output_file_evidence(path: Path, *, stage: str) -> OutputWriteEvidence:
    """Record observable output state without changing processing outcomes."""
    timestamp = datetime.now().isoformat(timespec="seconds")
    try:
        if not path.is_file():
            evidence = OutputWriteEvidence(path, False, 0, "", timestamp)
        else:
            digest = hashlib.sha256()
            with path.open("rb") as output_file:
                for chunk in iter(lambda: output_file.read(1024 * 1024), b""):
                    digest.update(chunk)
            evidence = OutputWriteEvidence(
                path, True, path.stat().st_size, digest.hexdigest(), timestamp
            )
    except OSError as error:
        evidence = OutputWriteEvidence(
            path, False, 0, "", timestamp, f"{type(error).__name__}: {error}"
        )

    logging.info(
        "Output %s verification: filename=%s path=%s exists=%s bytes=%d "
        "sha256=%s timestamp=%s error=%s",
        stage,
        path.name,
        path,
        str(evidence.exists).lower(),
        evidence.byte_size,
        evidence.sha256 or "unavailable",
        evidence.timestamp,
        evidence.error or "none",
    )
    return evidence


def _atomic_write(destination: Path, data: bytes) -> OutputWriteEvidence:
    """Prevent cancellation or crashes from leaving a partial JPEG."""
    temporary = destination.with_name(f".{destination.name}.tmp")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_bytes(data)
        os.replace(temporary, destination)
        return _output_file_evidence(destination, stage="write")
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
    rule_selection: RuleSelection | None = None,
    api_request_count: int = 0,
    window_pull_assessment: WindowPullAssessment | None = None,
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
        "window_pull_classification": (
            window_pull_assessment.classification if window_pull_assessment else "MANUAL_OVERRIDE"
        ),
        "window_pull_reason": (
            window_pull_assessment.reason if window_pull_assessment else "Manual quality selection."
        ),
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
        "image_preparation_seconds": f"{metrics.get('timings', {}).get('image_preparation_seconds', 0.0):.3f}",
        "api_latency_seconds": f"{metrics.get('timings', {}).get('api_latency_seconds', 0.0):.3f}",
        "response_decode_seconds": f"{metrics.get('timings', {}).get('response_decode_seconds', 0.0):.3f}",
        "premium_finish_seconds": f"{metrics.get('timings', {}).get('premium_finish_seconds', 0.0):.3f}",
        "filesystem_write_seconds": f"{metrics.get('timings', {}).get('filesystem_write_seconds', 0.0):.3f}",
        "learned_rule_ids": ",".join(rule_selection.applied_rule_ids) if rule_selection else "",
        "learned_rules_hash": rule_selection.database_hash if rule_selection else "",
        "learned_rules_schema_version": rule_selection.database_version if rule_selection else "",
        "api_request_count": api_request_count,
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
    if quality not in QUALITY_MODES:
        raise ValueError(f"Unsupported quality: {quality}")
    global QUALITY
    QUALITY = quality if quality in QUALITY_OPTIONS else "low"
    batch_started = time.perf_counter()
    run_internal_regression_tests()
    for directory in (INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, ERROR_DIR, LOG_DIR, DATA_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    initialize_history_db()
    reconcile_history_labels()
    base_prompt = load_prompt()
    files, skipped = pending_images(selected_files)
    summary = BatchSummary(
        total=len(files), selected=len(files) + skipped, skipped=skipped, quality=quality
    )
    if not files:
        summary.elapsed_seconds = time.perf_counter() - batch_started
        return summary
    log_path = create_log_file()
    summary.log_path = log_path
    run_id = log_path.stem
    local_agent = editing_agent()
    review_pdf_outputs: dict[Path, Path] = {}
    successful_output_evidence: dict[Path, OutputWriteEvidence] = {}

    for index, input_file in enumerate(files, 1):
        if cancel_requested():
            summary.cancelled = True
            event("cancelled", input_file.name)
            break
        event("started", {"index": index, "total": len(files), "filename": input_file.name})
        requested_size = ""
        metrics: dict[str, Any] = {}
        usage = ApiUsage()
        rule_selection: RuleSelection | None = None
        window_assessment: WindowPullAssessment | None = None
        image_quality = quality
        image_started = time.perf_counter()
        api_call_completed = False
        timings: dict[str, float] = {}
        try:
            preparation_started = time.perf_counter()
            metrics = analyze_input(input_file)
            image_quality, window_assessment = quality_for_image(quality, input_file)
            QUALITY = image_quality
            if window_assessment:
                logging.info(
                    "Smart costing: filename=%s classification=%s selected_quality=%s reason=%s api_calls=0",
                    input_file.name,
                    window_assessment.classification,
                    image_quality,
                    window_assessment.reason,
                )
            rule_selection = build_edit_instruction(base_prompt, input_file)
            prompt = (
                f"{rule_selection.instruction}\n\n"
                f"{build_adaptive_addendum(metrics)}"
            )
            timings["image_preparation_seconds"] = time.perf_counter() - preparation_started
            api_started = time.perf_counter()
            generated_bytes, requested_size, usage = call_image_editor(
                client, input_file, prompt, image_quality
            )
            timings["api_latency_seconds"] = time.perf_counter() - api_started
            api_call_completed = True
            summary.api_calls += 1
            summary.fallback_cost += estimated_cost_per_image(image_quality)
            decode_started = time.perf_counter()
            with Image.open(BytesIO(generated_bytes)) as generated:
                generated_image = generated.convert("RGB")
            timings["response_decode_seconds"] = time.perf_counter() - decode_started
            premium_started = time.perf_counter()
            premium_finish_error = ""
            try:
                generated_image = apply_premium_finish(generated_image)
            except Exception as premium_error:
                premium_finish_error = (
                    f"Premium Finish failed locally: {type(premium_error).__name__}: "
                    f"{premium_error}"
                )
                logging.exception("Premium Finish failed; preserving successful API image")
            timings["premium_finish_seconds"] = time.perf_counter() - premium_started
            generated_image, sharpened = maybe_apply_gentle_sharpening(
                input_file, generated_image
            )
            verification = compare_images(input_file, generated_image, sharpened)
            jpeg_bytes, jpeg_quality = encode_final_jpeg(
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
            if premium_finish_error:
                review_reasons.append(premium_finish_error)
            needs_review_reason = " | ".join(dict.fromkeys(review_reasons))
            routing_status = "REVIEW" if needs_review_reason else "PASS"
            destination = REVIEW_DIR if needs_review_reason else OUTPUT_DIR
            filesystem_started = time.perf_counter()
            output_path = destination / input_file.name
            successful_output_evidence[input_file.resolve()] = _atomic_write(
                output_path, jpeg_bytes
            )
            review_pdf_outputs[input_file.resolve()] = output_path
            timings["filesystem_write_seconds"] = time.perf_counter() - filesystem_started
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
            timings["total_processing_seconds"] = image_elapsed
            metrics["timings"] = timings
            logging.info(
                "Image timing: filename=%s quality=%s api_latency=%.3f preparation=%.3f "
                "decode=%.3f premium_finish=%.3f filesystem=%.3f total=%.3f",
                input_file.name, image_quality, timings.get("api_latency_seconds", 0.0),
                timings.get("image_preparation_seconds", 0.0),
                timings.get("response_decode_seconds", 0.0),
                timings.get("premium_finish_seconds", 0.0),
                timings.get("filesystem_write_seconds", 0.0), image_elapsed,
            )
            image_cost = estimated_cost_per_image(image_quality)
            smart_message = (
                f"Smart costing: {window_assessment.classification}; {window_assessment.reason}"
                if window_assessment else ""
            )
            message = " | ".join(part for part in (message, smart_message) if part)
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
                    rule_selection=rule_selection,
                    api_request_count=1,
                    window_pull_assessment=window_assessment,
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
                rule_selection=rule_selection,
                api_request_count=1,
            )
            local_agent.record_applied(
                rule_selection.applied_rule_ids if rule_selection else (),
                filename=input_file.name,
                batch_id=run_id,
                quality=image_quality,
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
                    "window_pull_classification": (
                        window_assessment.classification if window_assessment else "MANUAL_OVERRIDE"
                    ),
                    "quality": image_quality,
                    "window_pull_reason": window_assessment.reason if window_assessment else "Manual quality selection.",
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
                    api_cost=(estimated_cost_per_image(image_quality) if api_call_completed else 0.0),
                    rule_selection=rule_selection,
                    api_request_count=int(api_call_completed),
                    window_pull_assessment=window_assessment,
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
                rule_selection=rule_selection,
                api_request_count=int(api_call_completed),
            )
            event(
                "failed",
                {
                    "filename": input_file.name,
                    "error": str(error),
                    "processing_time_seconds": time.perf_counter() - image_started,
                    "api_cost": (
                        estimated_cost_per_image(image_quality) if api_call_completed else 0.0
                    ),
                    "destination": str(ERROR_DIR),
                    "needs_review_reason": "",
                    "window_pull_classification": (
                        window_assessment.classification if window_assessment else "MANUAL_OVERRIDE"
                    ),
                    "quality": image_quality,
                    "window_pull_reason": window_assessment.reason if window_assessment else "Manual quality selection.",
                },
            )
    summary.elapsed_seconds = time.perf_counter() - batch_started
    finalize_batch_review_pdfs(
        summary, files, review_pdf_outputs, run_id, successful_output_evidence
    )
    return summary


def append_demo_history(
    *,
    run_id: str,
    filename: str,
    quality: str,
    status: str,
    destination: Path,
    message: str,
) -> None:
    """Write an isolated learning record that is unmistakably a simulation."""
    implicit_label = {
        "PASS": "ACCEPTED",
        "REVIEW": "UNRESOLVED",
        "FAILED": "FAILED",
    }[status]
    with sqlite3.connect(HISTORY_DB) as connection:
        connection.execute(
            """
            INSERT INTO image_history (
                run_id, processed_at, filename, program_version, prompt_version,
                model, quality, system_decision, implicit_final_label, destination,
                fallback_estimated_cost, message
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                datetime.now().isoformat(timespec="seconds"),
                filename,
                f"{PROGRAM_VERSION}-DEMO",
                "DEMO",
                "DEMO",
                quality,
                status,
                implicit_label,
                str(destination),
                0.0,
                f"DEMO — {message}",
            ),
        )
        connection.commit()


def process_demo_batch(
    *,
    selected_files: list[Path] | None = None,
    quality: str = "low",
    result_mode: str = "All Pass",
    cancel_requested=lambda: False,
    event=lambda kind, payload: None,
    delay_seconds: float = 0.15,
) -> BatchSummary:
    """Simulate the complete batch workflow without constructing an API client."""
    if quality not in QUALITY_MODES:
        raise ValueError(f"Unsupported quality: {quality}")
    if result_mode not in {"All Pass", "Some Need Review", "Include Error"}:
        raise ValueError(f"Unsupported demo result mode: {result_mode}")

    global QUALITY
    QUALITY = quality if quality in QUALITY_OPTIONS else "low"
    batch_started = time.perf_counter()
    for directory in (INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, ERROR_DIR, LOG_DIR, DATA_DIR):
        directory.mkdir(parents=True, exist_ok=True)
    initialize_history_db()
    files, skipped = pending_images(selected_files)
    summary = BatchSummary(
        total=len(files), selected=len(files) + skipped, skipped=skipped, quality=quality
    )
    if not files:
        summary.elapsed_seconds = time.perf_counter() - batch_started
        return summary

    log_path = create_log_file()
    summary.log_path = log_path
    run_id = f"DEMO-{log_path.stem}"
    review_pdf_outputs: dict[Path, Path] = {}
    successful_output_evidence: dict[Path, OutputWriteEvidence] = {}

    for index, input_file in enumerate(files, 1):
        if cancel_requested():
            summary.cancelled = True
            event("cancelled", input_file.name)
            break
        event("started", {"index": index, "total": len(files), "filename": input_file.name})
        image_started = time.perf_counter()
        image_quality, window_assessment = quality_for_image(quality, input_file)
        QUALITY = image_quality
        if delay_seconds:
            time.sleep(delay_seconds)

        if result_mode == "All Pass":
            status = "PASS"
        elif result_mode == "Some Need Review":
            status = "REVIEW" if index % 2 else "PASS"
        elif index == len(files):
            status = "FAILED"
        elif index % 2 == 0:
            status = "REVIEW"
        else:
            status = "PASS"

        elapsed = time.perf_counter() - image_started
        smart_message = (
            f"Smart costing: {window_assessment.classification}; {window_assessment.reason}"
            if window_assessment else "Manual quality selection."
        )
        if status == "PASS":
            destination = OUTPUT_DIR
            message = "DEMO simulated pass; original copied as mock processed result."
            needs_review_reason = ""
            output_path = destination / input_file.name
            successful_output_evidence[input_file.resolve()] = _atomic_write(
                output_path, input_file.read_bytes()
            )
            review_pdf_outputs[input_file.resolve()] = output_path
            summary.completed += 1
        elif status == "REVIEW":
            destination = REVIEW_DIR
            needs_review_reason = "DEMO simulated NeedsReview result."
            message = needs_review_reason
            output_path = destination / input_file.name
            successful_output_evidence[input_file.resolve()] = _atomic_write(
                output_path, input_file.read_bytes()
            )
            review_pdf_outputs[input_file.resolve()] = output_path
            summary.review += 1
        else:
            destination = ERROR_DIR
            needs_review_reason = ""
            message = "DEMO simulated processing error."
            ERROR_DIR.mkdir(parents=True, exist_ok=True)
            (ERROR_DIR / f"{input_file.stem}_error.txt").write_text(
                f"DEMO — NO API CALLS\nFile: {input_file.name}\nReason: {message}\n",
                encoding="utf-8",
            )
            summary.failed += 1

        row = _log_row(
            filename=input_file.name,
            status=status,
            destination=destination,
            message=f"DEMO — {message} | {smart_message}",
            processing_time_seconds=elapsed,
            api_cost=0.0,
            needs_review_reason=needs_review_reason,
            window_pull_assessment=window_assessment,
        )
        row["model"] = "DEMO"
        row["prompt_version"] = "DEMO"
        row["api_cost"] = "0.000000"
        row["fallback_estimated_cost"] = "0.000000"
        append_log(log_path, row)
        append_demo_history(
            run_id=run_id,
            filename=input_file.name,
            quality=image_quality,
            status=status,
            destination=destination,
            message=f"{message} | {smart_message}",
        )

        payload = {
            "filename": input_file.name,
            "processing_time_seconds": elapsed,
            "api_cost": 0.0,
            "destination": str(destination),
            "needs_review_reason": needs_review_reason,
            "window_pull_classification": (
                window_assessment.classification if window_assessment else "MANUAL_OVERRIDE"
            ),
            "quality": image_quality,
            "window_pull_reason": window_assessment.reason if window_assessment else "Manual quality selection.",
        }
        if status == "FAILED":
            payload["error"] = message
            event("failed", payload)
        else:
            payload.update({"status": status, "messages": [message], "metrics": None})
            event("finished", payload)

    summary.fallback_cost = 0.0
    summary.elapsed_seconds = time.perf_counter() - batch_started
    finalize_batch_review_pdfs(
        summary, files, review_pdf_outputs, run_id, successful_output_evidence
    )
    return summary


def accept_review_output(filename: str) -> Path:
    """Move a reviewed output into the active Completed folder and update history."""
    source = REVIEW_DIR / filename
    destination = OUTPUT_DIR / filename
    if destination.exists():
        raise FileExistsError(f"Completed already contains {filename}.")
    source.replace(destination)
    set_review_label(filename, "ACCEPTED")
    return destination


def move_output_to_review(filename: str) -> Path:
    """Move an existing Completed output into the active NeedsReview folder."""
    source = OUTPUT_DIR / filename
    destination = REVIEW_DIR / filename
    if destination.exists():
        raise FileExistsError(f"NeedsReview already contains {filename}.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    source.replace(destination)
    set_latest_history_label(filename, "UNRESOLVED")
    return destination


def delete_active_output(filename: str) -> None:
    """Delete the active result only; the Incoming original and history remain."""
    for directory in (REVIEW_DIR, OUTPUT_DIR):
        candidate = directory / filename
        if candidate.is_file():
            candidate.unlink()
            return
    raise FileNotFoundError(f"No output exists for {filename}.")


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


def set_latest_history_label(filename: str, label: str) -> None:
    if not HISTORY_DB.exists():
        return
    with sqlite3.connect(HISTORY_DB) as connection:
        connection.execute(
            """
            UPDATE image_history SET implicit_final_label = ?
            WHERE id = (
                SELECT id FROM image_history
                WHERE filename = ?
                ORDER BY id DESC LIMIT 1
            )
            """,
            (label, filename),
        )
        connection.commit()


def launch_gui() -> int:
    try:
        from PySide6.QtCore import (
            QObject,
            QSettings,
            QSignalBlocker,
            QThread,
            QTimer,
            Qt,
            Signal,
            Slot,
            QUrl,
        )
        from PySide6.QtGui import QDesktopServices, QPixmap
        from PySide6.QtWidgets import (
            QApplication,
            QCheckBox,
            QComboBox,
            QDialog,
            QFileDialog,
            QFrame,
            QFormLayout,
            QGridLayout,
            QGroupBox,
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

    app_stylesheet = """
        QWidget {
            color: #172033;
            font-size: 13px;
        }
        QMainWindow, QWidget#appRoot, QScrollArea { background: #f4f6fa; border: none; }
        QLabel#appTitle { font-size: 28px; font-weight: 700; color: #111827; }
        QLabel#appSubtitle { font-size: 13px; color: #667085; }
        QLabel#versionBadge {
            color: #175cd3; background: #eff8ff; border: 1px solid #b2ddff;
            border-radius: 12px; padding: 5px 10px; font-weight: 600;
        }
        QLabel#demoBanner {
            color: #7a2e0e; background: #fef0c7; border: 1px solid #fec84b;
            border-radius: 9px; padding: 9px 14px; font-size: 14px; font-weight: 750;
        }
        QCheckBox { color: #344054; font-weight: 650; spacing: 8px; }
        QCheckBox::indicator { width: 18px; height: 18px; }
        QLabel#pathValue {
            color: #344054; background: #f8fafc; border: 1px solid #e4e7ec;
            border-radius: 7px; padding: 7px 9px;
        }
        QLabel#hintText { color: #667085; font-size: 12px; }
        QGroupBox {
            background: white; border: 1px solid #e4e7ec; border-radius: 12px;
            margin-top: 12px; padding: 16px 14px 14px 14px;
            font-size: 14px; font-weight: 650; color: #1d2939;
        }
        QGroupBox::title { subcontrol-origin: margin; left: 14px; padding: 0 6px; }
        QPushButton {
            background: white; border: 1px solid #d0d5dd; border-radius: 8px;
            padding: 7px 12px; color: #344054; font-weight: 600;
        }
        QPushButton:hover { background: #f9fafb; border-color: #98a2b3; }
        QPushButton:pressed { background: #f2f4f7; }
        QPushButton:disabled { color: #98a2b3; background: #f2f4f7; border-color: #eaecf0; }
        QPushButton#primaryButton {
            color: white; background: #2563eb; border-color: #2563eb;
            padding: 9px 18px;
        }
        QPushButton#primaryButton:hover { background: #1d4ed8; border-color: #1d4ed8; }
        QPushButton#dangerButton { color: #b42318; background: #fff; border-color: #fda29b; }
        QPushButton#dangerButton:hover { background: #fff5f4; }
        QPushButton#reviewButton { color: #7a2e0e; background: #fffaeb; border-color: #fedf89; }
        QComboBox {
            background: white; border: 1px solid #d0d5dd; border-radius: 8px;
            padding: 7px 28px 7px 10px; min-width: 105px;
        }
        QComboBox:focus { border: 2px solid #84adff; }
        QFrame#metricCard {
            background: white; border: 1px solid #e4e7ec; border-radius: 11px;
        }
        QLabel#metricCaption { color: #667085; font-size: 11px; font-weight: 650; }
        QLabel#metricValue { color: #101828; font-size: 14px; font-weight: 650; }
        QTableWidget {
            background: white; alternate-background-color: #f9fafb; border: 1px solid #e4e7ec;
            border-radius: 8px; gridline-color: #eaecf0; selection-background-color: #eff8ff;
            selection-color: #175cd3;
        }
        QHeaderView::section {
            background: #f9fafb; color: #475467; border: none; border-bottom: 1px solid #e4e7ec;
            padding: 8px; font-weight: 650;
        }
        QPlainTextEdit {
            background: #101828; color: #d0d5dd; border: 1px solid #344054;
            border-radius: 9px; padding: 10px; font-family: monospace; font-size: 11px;
        }
        QProgressBar {
            background: #eaecf0; border: none; border-radius: 5px; height: 10px; text-align: center;
        }
        QProgressBar::chunk { background: #2563eb; border-radius: 5px; }
        QLabel#imageCanvas {
            background: #101828; color: #98a2b3; border: 1px solid #344054;
            border-radius: 10px;
        }
        QLabel#reviewReason {
            color: #7a2e0e; background: #fffaeb; border: 1px solid #fedf89;
            border-radius: 8px; padding: 10px;
        }
    """

    class Worker(QObject):
        event_signal = Signal(str, object)
        complete = Signal(object)

        def __init__(
            self,
            client,
            selected_files,
            quality,
            demo_mode=False,
            demo_result_mode="All Pass",
        ):
            super().__init__()
            self.client = client
            self.selected_files = selected_files
            self.quality = quality
            self.demo_mode = demo_mode
            self.demo_result_mode = demo_result_mode
            self.cancellation = CancellationToken()

        @Slot()
        def run(self):
            if self.demo_mode:
                summary = process_demo_batch(
                    selected_files=self.selected_files,
                    quality=self.quality,
                    result_mode=self.demo_result_mode,
                    cancel_requested=self.cancellation.is_cancelled,
                    event=lambda kind, payload: self.event_signal.emit(kind, payload),
                )
            else:
                summary = process_batch(
                    self.client,
                    selected_files=self.selected_files,
                    quality=self.quality,
                    cancel_requested=self.cancellation.is_cancelled,
                    event=lambda kind, payload: self.event_signal.emit(kind, payload),
                )
            self.complete.emit(summary)

        @Slot()
        def cancel(self):
            self.cancellation.cancel()

    class ReviewWindow(QMainWindow):
        reprocess = Signal(str)

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle(f"Review — {DISPLAY_APPLICATION_NAME}")
            self.resize(1180, 760)
            self.files: list[Path] = []
            self.index = 0
            self.demo_mode = False
            root = QWidget()
            root.setObjectName("appRoot")
            layout = QVBoxLayout(root)
            layout.setContentsMargins(24, 22, 24, 22)
            layout.setSpacing(14)
            self.title = QLabel()
            self.title.setObjectName("appTitle")
            self.reason = QLabel()
            self.reason.setObjectName("reviewReason")
            self.reason.setWordWrap(True)
            self.metrics = QLabel()
            self.metrics.setObjectName("appSubtitle")
            images = QSplitter()
            self.original = QLabel("Original")
            self.processed = QLabel("AI Output")
            for caption, label in (
                ("Original", self.original),
                ("AI Output", self.processed),
            ):
                label.setObjectName("imageCanvas")
                label.setAlignment(Qt.AlignCenter)
                label.setMinimumSize(400, 400)
                panel = QFrame()
                panel_layout = QVBoxLayout(panel)
                panel_layout.setContentsMargins(0, 0, 0, 0)
                heading = QLabel(caption)
                heading.setObjectName("metricValue")
                panel_layout.addWidget(heading)
                panel_layout.addWidget(label, 1)
                images.addWidget(panel)
            buttons = QHBoxLayout()
            for text, handler in (
                ("Previous", self.previous),
                ("Next", self.next),
                ("Accept", self.accept_image),
                ("Move to Needs Review", self.move_to_review),
                ("Retry", self.retry_image),
                ("Delete Output", self.delete_output),
            ):
                button = QPushButton(text)
                if text == "Accept":
                    button.setObjectName("primaryButton")
                elif text == "Delete Output":
                    button.setObjectName("dangerButton")
                elif text in {"Move to Needs Review", "Retry"}:
                    button.setObjectName("reviewButton")
                if text == "Retry":
                    self.reprocess_button = button
                button.clicked.connect(handler)
                buttons.addWidget(button)
            layout.addWidget(self.title)
            layout.addWidget(images, 1)
            layout.addWidget(self.reason)
            layout.addWidget(self.metrics)
            layout.addLayout(buttons)
            self.setCentralWidget(root)

        def set_demo_mode(self, enabled):
            self.demo_mode = enabled
            self.reprocess_button.setText(
                "Retry (Simulated)" if enabled else "Retry"
            )

        def refresh_files(self):
            review_files = sorted(
                path
                for path in REVIEW_DIR.glob("*")
                if path.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            review_names = {path.name for path in review_files}
            completed_files = sorted(
                path
                for path in OUTPUT_DIR.glob("*")
                if path.suffix.lower() in SUPPORTED_EXTENSIONS
                and path.name not in review_names
            )
            self.files = review_files + completed_files
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
            metric_values = (
                tuple(value or 0 for value in record[1:]) if record else (0, 0, 0)
            )
            self.metrics.setText(
                "Sharpness: {:.3f}   Brightness shift: {:.3f}   Chromaticity: {:.4f}".format(
                    *metric_values
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
            if source.parent == REVIEW_DIR:
                try:
                    accept_review_output(source.name)
                except FileExistsError as error:
                    QMessageBox.warning(self, "Existing file", str(error))
                    return
            else:
                set_latest_history_label(source.name, "ACCEPTED")
            self.refresh_files()

        def move_to_review(self):
            if not self.files:
                return
            source = self.files[self.index]
            if source.parent == REVIEW_DIR:
                self.reason.setText("This output is already in NeedsReview.")
                return
            try:
                move_output_to_review(source.name)
            except FileExistsError as error:
                QMessageBox.warning(self, "Existing file", str(error))
                return
            self.refresh_files()

        def retry_image(self):
            if not self.files:
                return
            filename = self.files[self.index].name
            answer = QMessageBox.question(
                self,
                "Simulated reprocess" if self.demo_mode else "Paid reprocess",
                (
                    f"Queue {filename} for a simulated retry? No API call will be made."
                    if self.demo_mode
                    else retry_confirmation_text(filename, QUALITY)
                ),
            )
            if answer == QMessageBox.Yes:
                delete_active_output(filename)
                self.reprocess.emit(filename)
                self.refresh_files()

        def delete_output(self):
            if not self.files:
                return
            filename = self.files[self.index].name
            answer = QMessageBox.question(
                self,
                "Delete output",
                f"Delete the output for {filename}? The Incoming original will remain untouched.",
            )
            if answer == QMessageBox.Yes:
                delete_active_output(filename)
                self.refresh_files()

    class EditingMemoryDialog(QDialog):
        """Simple, explicit administration for persistent local editing rules."""

        def __init__(self, parent=None):
            super().__init__(parent)
            self.setWindowTitle("Editing Memory — MyEstatePics V4.0.3")
            self.resize(920, 480)
            layout = QVBoxLayout(self)
            explanation = QLabel(
                "Only APPROVED and enabled rules affect future images. "
                "The source-controlled production prompt always wins."
            )
            explanation.setWordWrap(True)
            layout.addWidget(explanation)
            self.table = QTableWidget(0, 6)
            self.table.setHorizontalHeaderLabels(
                ["Rule ID", "Categories", "Description", "Status", "Enabled", "Applied"]
            )
            self.table.setEditTriggers(QTableWidget.NoEditTriggers)
            self.table.setSelectionBehavior(QTableWidget.SelectRows)
            self.table.horizontalHeader().setStretchLastSection(True)
            layout.addWidget(self.table)
            actions = QHBoxLayout()
            for text, handler in (
                ("Approve", self.approve_selected),
                ("Disable", self.disable_selected),
                ("Re-enable", self.enable_selected),
                ("Delete Proposed", self.delete_selected),
                ("Refresh", self.refresh),
            ):
                button = QPushButton(text)
                button.clicked.connect(handler)
                actions.addWidget(button)
            actions.addStretch(1)
            layout.addLayout(actions)
            self.refresh()

        def selected_rule_id(self):
            row = self.table.currentRow()
            item = self.table.item(row, 0) if row >= 0 else None
            return item.text() if item else None

        def refresh(self):
            rules = editing_agent().list_rules()
            self.table.setRowCount(len(rules))
            for row, rule in enumerate(rules):
                values = (
                    rule.id,
                    ", ".join(rule.categories),
                    rule.description,
                    rule.status,
                    "Yes" if rule.enabled else "No",
                    str(rule.times_applied),
                )
                for column, value in enumerate(values):
                    self.table.setItem(row, column, QTableWidgetItem(value))

        def _apply(self, action):
            rule_id = self.selected_rule_id()
            if not rule_id:
                QMessageBox.information(self, "Editing Memory", "Select a rule first.")
                return
            try:
                action(rule_id)
            except Exception as error:
                QMessageBox.warning(self, "Editing Memory", str(error))
            self.refresh()

        def approve_selected(self):
            self._apply(editing_agent().approve)

        def disable_selected(self):
            self._apply(editing_agent().disable)

        def enable_selected(self):
            self._apply(editing_agent().enable)

        def delete_selected(self):
            self._apply(editing_agent().delete_proposed)

    class MainWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(DISPLAY_APPLICATION_NAME)
            self.resize(1180, 1080)
            self.setMinimumSize(980, 760)
            self.thread = None
            self.worker = None
            self.processing_active = False
            self.processing_control_states = {}
            self.api_key: str | None = None
            self.selected_files: set[Path] = set()
            self.available_files: list[Path] = []
            self.updating_table = False
            self.selection_events_enabled = False
            self.selection_generation = 0
            self.folder_configuration_valid = True
            self.preferences_path = USER_DATA_DIR / "preferences.ini"
            self.settings = QSettings(
                str(self.preferences_path), QSettings.IniFormat
            )
            self.review_window = ReviewWindow(self)
            self.review_window.reprocess.connect(self.queue_retry)
            self.memory_window = EditingMemoryDialog(self)
            root = QWidget()
            root.setObjectName("appRoot")
            root_layout = QVBoxLayout(root)
            root_layout.setContentsMargins(0, 0, 0, 0)
            content = QWidget()
            content.setObjectName("appRoot")
            layout = QVBoxLayout(content)
            layout.setContentsMargins(28, 24, 28, 24)
            layout.setSpacing(14)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.NoFrame)
            scroll.setWidget(content)
            root_layout.addWidget(scroll)

            header = QFrame()
            header_layout = QHBoxLayout(header)
            header_layout.setContentsMargins(0, 0, 0, 0)
            header_text = QVBoxLayout()
            title = QLabel(DISPLAY_APPLICATION_NAME)
            title.setObjectName("appTitle")
            subtitle = QLabel("Commercial MLS batch photo editing")
            subtitle.setObjectName("appSubtitle")
            header_text.addWidget(title)
            header_text.addWidget(subtitle)
            header_status = QGridLayout()
            version = QLabel(f"Production v{PROGRAM_VERSION} • {RELEASE_DATE}")
            version.setObjectName("versionBadge")
            prompt_version = QLabel(f"Prompt v{PROMPT_VERSION}")
            prompt_version.setObjectName("versionBadge")
            self.api_key_status = QLabel()
            self.api_key_status.setObjectName("appSubtitle")
            self.demo_status = QLabel("Demo Mode: Off")
            self.demo_status.setObjectName("appSubtitle")
            header_status.addWidget(version, 0, 0)
            header_status.addWidget(prompt_version, 0, 1)
            header_status.addWidget(self.api_key_status, 1, 0, 1, 2)
            header_status.addWidget(self.demo_status, 2, 0, 1, 2)
            header_layout.addLayout(header_text, 1)
            header_layout.addLayout(header_status)
            layout.addWidget(header)

            self.demo_banner = QLabel("DEMO — NO API CALLS")
            self.demo_banner.setObjectName("demoBanner")
            self.demo_banner.setAlignment(Qt.AlignCenter)
            self.demo_banner.setVisible(False)
            layout.addWidget(self.demo_banner)

            default_paths = (INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, ERROR_DIR, LOG_DIR)
            saved_paths = list(load_folder_settings(self.settings, default_paths))
            incoming, completed, self.startup_folder_integrity = load_primary_folder_settings(
                self.settings, default_paths[0], default_paths[1]
            )
            saved_paths[0], saved_paths[1] = incoming, completed
            self.folder_paths = list(saved_paths)
            logging.info(
                "Folder configuration loaded: source=startup restore incoming=%s "
                "completed=%s validation=%s",
                incoming,
                completed,
                (
                    self.startup_folder_integrity.error
                    or "; ".join(self.startup_folder_integrity.warnings)
                    or "valid"
                ),
            )
            self.path_labels = []

            def add_folder_row(parent_layout, row, index, title_text):
                path_label = QLabel(str(self.folder_paths[index]))
                path_label.setObjectName("pathValue")
                path_label.setMinimumHeight(30)
                path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                choose = QPushButton("Choose…")
                choose.clicked.connect(
                    lambda checked=False, i=index: self.choose_folder(i)
                )
                open_button = QPushButton("Open")
                open_button.clicked.connect(
                    lambda checked=False, i=index: self.open_folder(i)
                )
                parent_layout.addWidget(QLabel(title_text), row, 0)
                parent_layout.addWidget(path_label, row, 1)
                parent_layout.addWidget(choose, row, 2)
                parent_layout.addWidget(open_button, row, 3)
                return path_label

            job_group = QGroupBox("Job Setup")
            job_layout = QGridLayout(job_group)
            job_layout.setColumnStretch(1, 1)
            self.path_labels.append(
                add_folder_row(job_layout, 0, 0, "Incoming Folder")
            )
            self.path_labels.append(
                add_folder_row(job_layout, 1, 1, "Completed Folder")
            )

            self.quality = QComboBox()
            self.quality.addItems(["SMART", "LOW", "MEDIUM", "HIGH"])
            saved_quality = normalize_quality_mode(
                self.settings.value(QUALITY_SETTING, "smart"), "smart"
            )
            self.quality.setCurrentText(saved_quality.upper())
            self.demo_checkbox = QCheckBox("Demo Mode — No API Charges")
            self.demo_checkbox.toggled.connect(self.on_demo_toggled)
            self.architecture_lock = QCheckBox("Conservative Architecture Lock")
            self.architecture_lock.setChecked(True)
            self.architecture_lock.setEnabled(False)
            job_layout.addWidget(QLabel("Quality"), 2, 0)
            job_layout.addWidget(self.quality, 2, 1)
            job_layout.addWidget(self.demo_checkbox, 2, 2, 1, 2)
            job_layout.addWidget(self.architecture_lock, 3, 1, 1, 3)

            self.images_found = QLabel("0")
            self.images_selected = QLabel("0")
            self.cost = QLabel("$0.00")
            for value_label in (self.images_found, self.images_selected, self.cost):
                value_label.setObjectName("metricValue")
            job_layout.addWidget(QLabel("Images Found"), 4, 0)
            job_layout.addWidget(self.images_found, 4, 1)
            job_layout.addWidget(QLabel("Images Selected"), 4, 2)
            job_layout.addWidget(self.images_selected, 4, 3)
            job_layout.addWidget(QLabel("Estimated Cost"), 5, 0)
            job_layout.addWidget(self.cost, 5, 1)
            self.demo_result = QComboBox()
            self.demo_result.addItems(["All Pass", "Some Need Review", "Include Error"])
            self.demo_result.setEnabled(False)
            job_layout.addWidget(QLabel("Demo Results"), 5, 2)
            job_layout.addWidget(self.demo_result, 5, 3)
            self.scan_status = QLabel()
            self.scan_status.setWordWrap(True)
            job_layout.addWidget(self.scan_status, 6, 0, 1, 4)
            self.folder_validation = QLabel()
            self.folder_validation.setWordWrap(True)
            self.folder_validation.setStyleSheet("color: #b42318;")
            job_layout.addWidget(self.folder_validation, 7, 0, 1, 4)
            layout.addWidget(job_group)

            self.advanced_group = QGroupBox("Advanced Folders")
            self.advanced_group.setCheckable(True)
            advanced_layout = QVBoxLayout(self.advanced_group)
            self.advanced_content = QWidget()
            advanced_grid = QGridLayout(self.advanced_content)
            advanced_grid.setContentsMargins(0, 0, 0, 0)
            advanced_grid.setColumnStretch(1, 1)
            for row, (index, title_text) in enumerate(
                ((2, "NeedsReview"), (3, "Error"), (4, "Logs"))
            ):
                self.path_labels.append(
                    add_folder_row(advanced_grid, row, index, title_text)
                )
            self.model_label = QLabel(MODEL)
            self.open_env_button = QPushButton("Open .env")
            self.open_env_button.clicked.connect(self.open_env)
            self.reload_key_button = QPushButton("Reload API Key")
            self.reload_key_button.clicked.connect(self.reload_api_key)
            self.editing_memory_button = QPushButton("Editing Memory")
            self.editing_memory_button.clicked.connect(self.open_editing_memory)
            advanced_grid.addWidget(QLabel("Model"), 3, 0)
            advanced_grid.addWidget(self.model_label, 3, 1)
            advanced_grid.addWidget(self.open_env_button, 3, 2)
            advanced_grid.addWidget(self.reload_key_button, 3, 3)
            advanced_grid.addWidget(self.editing_memory_button, 4, 2, 1, 2)
            advanced_layout.addWidget(self.advanced_content)
            advanced_expanded = load_boolean_setting(
                self.settings, ADVANCED_FOLDERS_SETTING, False
            )
            self.advanced_group.setChecked(advanced_expanded)
            self.advanced_content.setVisible(advanced_expanded)
            self.advanced_group.toggled.connect(self.on_advanced_toggled)
            layout.addWidget(self.advanced_group)

            selection_group = QGroupBox("Images")
            selection_layout = QVBoxLayout(selection_group)
            selection_actions = QHBoxLayout()
            for text, handler in (
                ("Select All", self.select_all),
                ("Clear All", self.clear_selection),
                ("Rescan Folder", self.rescan_folder),
                ("Analyze", self.analyze),
            ):
                button = QPushButton(text)
                button.clicked.connect(handler)
                selection_actions.addWidget(button)
            selection_actions.addStretch(1)
            selection_layout.addLayout(selection_actions)
            self.selected_table = QTableWidget(0, 5)
            self.selected_table.setHorizontalHeaderLabels(
                ["", "Filename", "File Size", "Dimensions", "Status"]
            )
            self.selected_table.horizontalHeader().setStretchLastSection(True)
            self.selected_table.setAlternatingRowColors(True)
            self.selected_table.setShowGrid(False)
            self.selected_table.setMinimumHeight(320)
            self.selected_table.setColumnWidth(0, 44)
            self.selected_table.setColumnWidth(1, 310)
            self.selected_table.setColumnWidth(2, 110)
            self.selected_table.setColumnWidth(3, 130)
            self.selected_table.itemChanged.connect(self.on_table_item_changed)
            selection_layout.addWidget(self.selected_table)
            layout.addWidget(selection_group, 1)

            activity_group = QGroupBox("Activity")
            activity_layout = QVBoxLayout(activity_group)
            self.log = QPlainTextEdit()
            self.log.setReadOnly(True)
            self.log.setPlaceholderText("Batch activity will appear here.")
            self.log.setMaximumHeight(125)
            activity_layout.addWidget(self.log)
            layout.addWidget(activity_group)

            footer = QFrame()
            footer.setObjectName("metricCard")
            footer_layout = QVBoxLayout(footer)
            footer_layout.setContentsMargins(16, 12, 16, 12)
            self.progress = QProgressBar()
            self.progress.setTextVisible(False)
            footer_layout.addWidget(self.progress)
            footer_status = QHBoxLayout()
            self.current = QLabel("Current: —")
            self.counts = QLabel("Completed: 0   NeedsReview: 0   Error: 0")
            footer_status.addWidget(self.current, 1)
            footer_status.addWidget(self.counts)
            footer_layout.addLayout(footer_status)
            footer_actions = QHBoxLayout()
            self.start_button = QPushButton("Start Processing")
            self.start_button.setObjectName("primaryButton")
            self.start_button.clicked.connect(self.start)
            self.cancel_button = QPushButton("Cancel")
            self.cancel_button.setObjectName("dangerButton")
            self.cancel_button.clicked.connect(self.cancel)
            self.cancel_button.setEnabled(False)
            self.review_button = QPushButton("Review Results")
            self.review_button.setObjectName("reviewButton")
            self.review_button.clicked.connect(self.open_review)
            footer_actions.addWidget(self.start_button)
            footer_actions.addWidget(self.cancel_button)
            footer_actions.addWidget(self.review_button)
            footer_actions.addStretch(1)
            footer_layout.addLayout(footer_actions)
            root_layout.addWidget(footer)
            self.setCentralWidget(root)
            self.quality.currentTextChanged.connect(self.on_quality_changed)
            self.reload_api_key(show_error=False)
            self.apply_paths()
            self.rescan_folder()
            saved_size = self.settings.value("ui/window_size")
            if saved_size is not None:
                self.resize(saved_size)
            if load_boolean_setting(self.settings, "ui/demo_mode", False):
                self.demo_checkbox.setChecked(True)
            if (
                not self.startup_folder_integrity.valid
                or self.startup_folder_integrity.warnings
            ):
                QTimer.singleShot(0, self.show_startup_folder_warning)

        def closeEvent(self, event):
            _atomic_update_preferences(
                self.preferences_path,
                {
                    INCOMING_FOLDER_SETTING: str(self.folder_paths[0]),
                    COMPLETED_FOLDER_SETTING: str(self.folder_paths[1]),
                    LAST_GOOD_INCOMING_SETTING: str(self.folder_paths[0]),
                    LAST_GOOD_COMPLETED_SETTING: str(self.folder_paths[1]),
                    "ui/window_size": (
                        f"@Size({self.size().width()} {self.size().height()})"
                    ),
                    "ui/demo_mode": (
                        "true" if self.demo_checkbox.isChecked() else "false"
                    ),
                    QUALITY_SETTING: self.quality.currentText().lower(),
                },
            )
            logging.info(
                "Folder configuration saved: source=shutdown incoming=%s completed=%s "
                "validation=valid",
                self.folder_paths[0],
                self.folder_paths[1],
            )
            super().closeEvent(event)

        def apply_paths(self):
            if self.demo_checkbox.isChecked():
                configure_demo_runtime_paths(self.folder_paths[0])
            else:
                configure_runtime_paths(*self.folder_paths)

        def show_startup_folder_warning(self):
            result = self.startup_folder_integrity
            detail = result.error or "\n".join(result.warnings)
            logging.warning(
                "Folder configuration rejected during startup restore: %s", detail
            )
            QMessageBox.warning(
                self,
                "Folder configuration needs attention",
                f"Saved folders were not used because they may be invalid or reversed.\n\n{detail}\n\n"
                "The last known good or default folders are shown. Please verify them.",
            )

        def _confirm_folder_warnings(self, result):
            if not result.warnings:
                return True
            answer = QMessageBox.warning(
                self,
                "Verify folder roles",
                "\n\n".join(result.warnings)
                + "\n\nSave these folders anyway?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            return answer == QMessageBox.Yes

        def _set_primary_folder(self, role, path, source="user selection"):
            index = 0 if role == "incoming" else 1
            candidate = list(self.folder_paths)
            candidate[index] = Path(path)
            result = validate_primary_folders(candidate[0], candidate[1])
            if not result.valid:
                logging.warning(
                    "Folder update rejected: role=%s source=%s path=%s reason=%s",
                    role, source, path, result.error,
                )
                QMessageBox.warning(self, "Invalid folder configuration", result.error)
                return False
            if not self._confirm_folder_warnings(result):
                logging.warning(
                    "Folder update rejected by user: role=%s source=%s path=%s warnings=%s",
                    role, source, path, "; ".join(result.warnings),
                )
                return False
            save_primary_folder_settings(
                self.preferences_path, incoming=candidate[0], completed=candidate[1]
            )
            self.folder_paths[index] = Path(path)
            self.path_labels[index].setText(str(path))
            logging.info(
                "Folder update accepted: role=%s source=%s incoming=%s completed=%s validation=%s",
                role, source, self.folder_paths[0], self.folder_paths[1],
                "warning-confirmed" if result.warnings else "valid",
            )
            return True

        def set_incoming_folder(self, path, source="user selection"):
            return self._set_primary_folder("incoming", Path(path), source)

        def set_completed_folder(self, path, source="user selection"):
            return self._set_primary_folder("completed", Path(path), source)

        def on_demo_toggled(self, enabled):
            self.demo_banner.setVisible(enabled)
            self.demo_status.setText(f"Demo Mode: {'On — no API calls' if enabled else 'Off'}")
            self.demo_result.setEnabled(enabled)
            self.open_env_button.setEnabled(not enabled)
            self.reload_key_button.setEnabled(not enabled)
            self.model_label.setText("DEMO (no API)" if enabled else MODEL)
            self.review_window.set_demo_mode(enabled)
            if enabled:
                self.api_key_status.setText("Not required — Demo Mode makes no API calls.")
                self.api_key_status.setStyleSheet("color: #7a2e0e;")
                self.log.appendPlainText("DEMO — NO API CALLS enabled.")
            else:
                self.reload_api_key(show_error=False)
                self.log.appendPlainText("Demo Mode disabled; real processing restored.")
            self.apply_paths()
            self.analyze()

        def choose_folder(self, index):
            role_name = ("Incoming", "Completed", "NeedsReview", "Error", "Logs")[index]
            chosen = QFileDialog.getExistingDirectory(
                self, f"Choose {role_name} folder", str(self.folder_paths[index])
            )
            if chosen:
                logging.info(
                    "Folder selected: role=%s source=user selection selected_path=%s",
                    role_name, chosen,
                )
                if index == 0 and not self.set_incoming_folder(chosen):
                    return
                if index == 1 and not self.set_completed_folder(chosen):
                    return
                if index >= 2:
                    self.folder_paths[index] = Path(chosen)
                    self.path_labels[index].setText(chosen)
                    _atomic_update_preferences(
                        self.preferences_path,
                        {FOLDER_SETTING_KEYS[index]: str(Path(chosen))},
                    )
                if index == 0:
                    self.rescan_folder()
                else:
                    self.analyze()

        def on_advanced_toggled(self, expanded):
            self.advanced_content.setVisible(expanded)
            _atomic_update_preferences(
                self.preferences_path,
                {ADVANCED_FOLDERS_SETTING: "true" if expanded else "false"},
            )

        def rescan_folder(self, checked=False):
            del checked
            self.selection_events_enabled = False
            self.selection_generation += 1
            generation = self.selection_generation
            self.apply_paths()
            self.available_files, self.selected_files = scan_and_select_all(INPUT_DIR)
            self.refresh_selection_table()
            self.update_job_summary()
            logging.info(
                "Incoming scan: incoming=%s completed=%s resolved=%s exists=%s readable=%s found=%d selected=%d",
                self.folder_paths[0], self.folder_paths[1], INPUT_DIR.resolve(),
                INPUT_DIR.is_dir(), os.access(INPUT_DIR, os.R_OK | os.X_OK),
                len(self.available_files),
                len(self.selected_files),
            )
            QTimer.singleShot(
                250,
                lambda current_generation=generation: self.finish_default_selection(
                    current_generation
                ),
            )

        def finish_default_selection(self, generation):
            if generation != self.selection_generation:
                return
            # Assert the default after Qt has finished delivering table setup
            # events, then permit genuine user check/uncheck changes.
            self.selected_files = set(self.available_files)
            self.refresh_selection_table()
            self.update_job_summary()
            self.selection_events_enabled = True
            logging.info(
                "Incoming selection ready: found=%d selected=%d",
                len(self.available_files),
                len(self.selected_files),
            )

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
            self.selected_files = set(self.available_files)
            self.refresh_selection_table()
            self.update_job_summary()

        def clear_selection(self):
            self.selected_files.clear()
            self.refresh_selection_table()
            self.update_job_summary()

        def refresh_selection_table(self):
            self.updating_table = True
            signal_blocker = QSignalBlocker(self.selected_table)
            try:
                files = self.available_files
                self.selected_table.setRowCount(len(files))
                for row, path in enumerate(files):
                    size = f"{path.stat().st_size / 1_000_000:.2f} MB"
                    try:
                        with Image.open(path) as image:
                            dimensions = f"{image.width} × {image.height}"
                    except (OSError, ValueError):
                        dimensions = "Unreadable"
                    status = selection_status(path)
                    if path not in self.selected_files and not status.startswith("Already"):
                        status = "Not selected"
                    checkbox = QTableWidgetItem()
                    checkbox.setFlags(Qt.ItemIsEnabled | Qt.ItemIsUserCheckable)
                    checkbox.setData(Qt.UserRole, str(path))
                    checkbox.setCheckState(
                        Qt.Checked if path in self.selected_files else Qt.Unchecked
                    )
                    self.selected_table.setItem(row, 0, checkbox)
                    for column, value in enumerate(
                        (path.name, size, dimensions, status), start=1
                    ):
                        item = QTableWidgetItem(value)
                        item.setFlags(item.flags() & ~Qt.ItemIsEditable)
                        self.selected_table.setItem(row, column, item)
            finally:
                del signal_blocker
                self.updating_table = False

        def on_table_item_changed(self, item):
            if (
                self.updating_table
                or not self.selection_events_enabled
                or item.column() != 0
            ):
                return
            path_value = item.data(Qt.UserRole)
            if not path_value:
                return
            path = Path(path_value)
            self.selected_files = update_checked_selection(
                self.selected_files, path, item.checkState() == Qt.Checked
            )
            status_item = self.selected_table.item(item.row(), 4)
            if status_item is not None:
                destination_status = selection_status(path)
                status_item.setText(
                    destination_status
                    if item.checkState() == Qt.Checked
                    or destination_status.startswith("Already")
                    else "Not selected"
                )
            self.update_job_summary()

        def selected_or_all(self) -> list[Path]:
            return sorted(self.selected_files)

        def eligible_selected_files(self):
            return [
                path
                for path in sorted(self.selected_files)
                if path.is_file() and existing_output_destination(path.name) is None
            ]

        def update_job_summary(self):
            self.apply_paths()
            valid, message = validate_folder_configuration(
                *self.folder_paths[:4]
            )
            self.folder_configuration_valid = valid
            eligible = self.eligible_selected_files()
            if not valid:
                summary_message = message
            elif not self.selected_files:
                summary_message = "Select at least one image to enable processing."
            elif not eligible:
                summary_message = (
                    "All selected images already have outputs. Delete an output or "
                    "select another image to process."
                )
            else:
                summary_message = ""
            self.folder_validation.setText(summary_message)
            quality = self.quality.currentText().lower()
            self.images_found.setText(str(len(self.available_files)))
            self.scan_status.setText(
                scanning_status_text(self.folder_paths[0], len(self.available_files))
            )
            self.images_selected.setText(str(len(self.selected_files)))
            self.cost.setText(
                f"${selected_batch_cost(eligible, quality, self.demo_checkbox.isChecked()):.2f}"
            )
            self.progress.setRange(0, max(1, len(eligible)))
            self.update_start_enabled()

        def update_start_enabled(self):
            api_ready = api_key_allows_processing(
                self.api_key, self.demo_checkbox.isChecked()
            )
            enabled = (
                not self.processing_active
                and self.folder_configuration_valid
                and bool(self.eligible_selected_files())
                and api_ready
            )
            self.start_button.setEnabled(enabled)

        def on_quality_changed(self, quality):
            global QUALITY
            QUALITY = normalize_quality_mode(quality, "smart")
            _atomic_update_preferences(
                self.preferences_path, {QUALITY_SETTING: QUALITY}
            )
            self.log.appendPlainText(
                "Smart Costing selected: local window assessment chooses Low or Medium per image."
                if QUALITY == "smart" else f"Quality selected: {QUALITY.upper()}"
            )
            self.update_job_summary()

        def open_env(self):
            env_path = api_environment_path()
            if not env_path.exists():
                QMessageBox.warning(
                    self,
                    ".env not found",
                    f"Create .env in {env_path.parent}, then click Reload API Key.",
                )
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(env_path.parent)))
                return
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(env_path)))

        def reload_api_key(self, checked=False, show_error=True):
            del checked
            if hasattr(self, "demo_checkbox") and self.demo_checkbox.isChecked():
                self.api_key = None
                self.api_key_status.setText(
                    "Not required — Demo Mode makes no API calls."
                )
                self.update_start_enabled()
                return True
            self.api_key, message = load_project_api_key()
            valid = self.api_key is not None
            self.api_key_status.setText(message)
            self.api_key_status.setStyleSheet(
                "color: #187a33;" if valid else "color: #b42318;"
            )
            if hasattr(self, "start_button"):
                self.update_start_enabled()
            if valid:
                if hasattr(self, "log"):
                    self.log.appendPlainText("API key reloaded successfully from project .env.")
            elif show_error:
                QMessageBox.critical(self, "Invalid API key", message)
            return valid

        def open_folder(self, index):
            self.apply_paths()
            if self.demo_checkbox.isChecked():
                path = (INPUT_DIR, OUTPUT_DIR, REVIEW_DIR, ERROR_DIR, LOG_DIR)[index]
            else:
                path = self.folder_paths[index]
            path.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

        def analyze(self):
            self.apply_paths()
            previous = set(self.selected_files)
            self.available_files = scan_supported_images(INPUT_DIR)
            self.selected_files = {
                path for path in self.available_files if path in previous
            }
            self.refresh_selection_table()
            self.update_job_summary()
            self.progress.setValue(0)

        def confirm_paid_processing(self, files, quality):
            box = QMessageBox(self)
            box.setWindowTitle("Confirm paid processing")
            box.setIcon(QMessageBox.Warning)
            box.setText(
                paid_confirmation_text(
                    len(files),
                    quality,
                    selected_batch_cost(files, quality, False),
                )
            )
            start = box.addButton("Start Paid Processing", QMessageBox.AcceptRole)
            box.addButton("Cancel", QMessageBox.RejectRole)
            box.exec()
            return box.clickedButton() is start

        def start(self):
            self.analyze()
            selected = self.selected_or_all()
            files = self.eligible_selected_files()
            quality = self.quality.currentText().lower()
            demo_mode = self.demo_checkbox.isChecked()
            if not selected:
                QMessageBox.information(
                    self, "Select images", "Select at least one image to process."
                )
                return
            if not files:
                QMessageBox.information(self, "Nothing to process", "No pending supported images were found.")
                return
            client = None
            if requires_paid_confirmation(demo_mode):
                if not self.reload_api_key(show_error=True):
                    return
                if not self.confirm_paid_processing(files, quality):
                    return
                api_key = self.api_key
                if api_key is None:
                    return
                client = OpenAI(api_key=api_key)
            self.thread = QThread(self)
            self.log.appendPlainText(
                f"Starting {'DEMO ' if demo_mode else ''}batch with quality: {quality}"
            )
            self.worker = Worker(
                client,
                files,
                quality,
                demo_mode=demo_mode,
                demo_result_mode=self.demo_result.currentText(),
            )
            self.worker.moveToThread(self.thread)
            self.thread.started.connect(self.worker.run)
            self.worker.event_signal.connect(self.on_event)
            self.worker.complete.connect(self.on_complete)
            self.worker.complete.connect(self.thread.quit)
            self.thread.finished.connect(self.worker.deleteLater)
            self.thread.finished.connect(self.thread.deleteLater)
            self.set_processing_active(True)
            self.cancel_button.setEnabled(True)
            self.thread.start()

        def set_processing_active(self, active):
            interactive_types = (
                QPushButton,
                QCheckBox,
                QComboBox,
                QGroupBox,
                QTableWidget,
            )
            if active:
                controls = []
                for control_type in interactive_types:
                    controls.extend(self.findChildren(control_type))
                self.processing_control_states = {
                    control: control.isEnabled()
                    for control in dict.fromkeys(controls)
                    if control is not self.cancel_button
                }
                for control in self.processing_control_states:
                    control.setEnabled(False)
            else:
                for control, was_enabled in self.processing_control_states.items():
                    control.setEnabled(was_enabled)
                self.processing_control_states.clear()
            self.processing_active = active
            self.update_start_enabled()

        def cancel(self):
            if self.worker:
                self.worker.cancel()
                self.log.appendPlainText("Cancelled by user")
                self.current.setText(
                    "Current: Cancel requested — waiting for current image"
                )
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
                    f"Filename: {payload['filename']} | Window pull: {payload.get('window_pull_classification', 'MANUAL_OVERRIDE')} | "
                    f"Quality: {payload.get('quality', QUALITY)} | Reason: {payload.get('window_pull_reason', 'Manual quality selection.')} | "
                    f"Processing time: {payload['processing_time_seconds']:.2f}s | "
                    f"Estimated API cost: ${payload['api_cost']:.4f} | "
                    f"Destination: {payload['destination']} | "
                    f"NeedsReview reason: {review_reason}"
                )
                self.selected_files.discard((INPUT_DIR / payload["filename"]).resolve())
                self.refresh_selection_table()
            elif kind == "failed":
                self.progress.setValue(self.progress.value() + 1)
                self.log.appendPlainText(
                    f"Filename: {payload['filename']} | Window pull: {payload.get('window_pull_classification', 'MANUAL_OVERRIDE')} | "
                    f"Quality: {payload.get('quality', QUALITY)} | Reason: {payload.get('window_pull_reason', 'Manual quality selection.')} | "
                    f"Processing time: {payload['processing_time_seconds']:.2f}s | "
                    f"Estimated API cost: ${payload['api_cost']:.4f} | "
                    f"Destination: {payload['destination']} | NeedsReview reason: — | "
                    f"Error: {payload['error']}"
                )
            elif kind == "cancelled":
                self.log.appendPlainText("Cancelled by user")

        def on_complete(self, summary):
            self.set_processing_active(False)
            self.cancel_button.setEnabled(False)
            self.current.setText(
                "Current: Cancelled by user" if summary.cancelled else "Current: —"
            )
            self.counts.setText(
                f"Completed: {summary.completed}   NeedsReview: {summary.review}   Error: {summary.failed}"
            )
            usage = (
                f"API tokens: input {summary.input_tokens}, output {summary.output_tokens}, total {summary.total_tokens}"
                if summary.usage_responses
                else "API token usage was not returned"
            )
            review_pdfs = (
                f"Review PDFs:\n"
                f"BEFORE: {'created' if summary.before_pdf else 'failed'}"
                f"{f' ({summary.before_pdf})' if summary.before_pdf else f' ({summary.before_pdf_error or 'not created'})'}\n"
                f"AFTER: {'created' if summary.after_pdf else 'failed'}"
                f"{f' ({summary.after_pdf})' if summary.after_pdf else f' ({summary.after_pdf_error or 'not created'})'}"
            )
            QMessageBox.information(
                self,
                "Demo Batch Summary" if self.demo_checkbox.isChecked() else "Batch summary",
                f"Selected: {summary.selected}\n"
                f"Successful: {summary.successful}\n"
                f"Failed/Skipped: {summary.failed_or_skipped}\n"
                f"Images processed: {summary.images_processed}\n"
                f"Quality: {summary.quality.title()}\n"
                f"Estimated total API cost: ${summary.fallback_cost:.2f}\n"
                f"Estimated average cost per image: "
                f"${summary.average_cost_per_image:.4f}\n"
                f"Elapsed time: {summary.elapsed_seconds:.2f} seconds\n"
                f"{usage}\n"
                f"Completed: {summary.completed}\nNeedsReview: {summary.review}\n"
                f"Errors: {summary.failed}\n"
                f"{review_pdfs}",
            )
            if summary.review_pdf_error:
                self.log.appendPlainText(summary.review_pdf_error)
            elif summary.before_pdf and summary.after_pdf:
                self.log.appendPlainText(f"Review PDFs created: {summary.before_pdf} | {summary.after_pdf}")
            self.review_window.refresh_files()
            self.analyze()

        def queue_retry(self, filename):
            self.apply_paths()
            source = (INPUT_DIR / filename).resolve()
            self.available_files = scan_supported_images(INPUT_DIR)
            if source not in self.available_files:
                QMessageBox.warning(
                    self,
                    "Incoming image missing",
                    f"{filename} is not present in the Incoming folder.",
                )
                return
            self.selected_files = {source}
            self.refresh_selection_table()
            self.update_job_summary()
            self.log.appendPlainText(
                f"Retry queued for {filename}. Review the quality and cost, then click Start Processing."
            )
            self.show()
            self.raise_()

        def open_review(self):
            self.apply_paths()
            self.review_window.refresh_files()
            self.review_window.show()
            self.review_window.raise_()

        def open_editing_memory(self):
            self.memory_window.refresh()
            self.memory_window.show()
            self.memory_window.raise_()
            self.memory_window.activateWindow()

    app = QApplication.instance() or QApplication([])
    app.setApplicationName(DISPLAY_APPLICATION_NAME)
    app.setStyle("Fusion")
    app.setStyleSheet(app_stylesheet)
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    configure_startup_logging()
    raise SystemExit(launch_gui())
