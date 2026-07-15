"""MyEstatePics MLS batch editor settings — Production v1.1."""

from pathlib import Path

BASE_DIR = Path("/Users/subratmohapatra/Documents/MyestatePics/MLS_AI_Automation")

INPUT_DIR = BASE_DIR / "Incoming"
OUTPUT_DIR = BASE_DIR / "Completed"
REVIEW_DIR = BASE_DIR / "NeedsReview"
ERROR_DIR = BASE_DIR / "Error"
LOG_DIR = BASE_DIR / "Logs"
DATA_DIR = BASE_DIR / "Data"
HISTORY_DB = DATA_DIR / "image_history.sqlite3"
PROMPT_FILE = BASE_DIR / "myestatepics_mls_interior_prompt_v1_6.txt"

PROGRAM_VERSION = "1.6"
PROMPT_VERSION = "1.6"
MODEL = "gpt-image-2"
QUALITY = "medium"

SUPPORTED_EXTENSIONS = {".jpg", ".jpeg"}

LANDSCAPE_SIZE = "1536x1024"
PORTRAIT_SIZE = "1024x1536"
SQUARE_SIZE = "1024x1024"

MAX_FILE_SIZE_BYTES = 2_000_000
JPEG_START_QUALITY = 95
JPEG_MIN_QUALITY = 78
JPEG_QUALITY_STEP = 2
DPI = (300, 300)

# Based on the user's observed spend: $0.28 / 6 images.
OBSERVED_ESTIMATED_COST_PER_IMAGE = 0.28 / 6.0

MAX_RETRIES = 3
RETRY_BASE_DELAY_SECONDS = 3

# Output verification thresholds.
NORMALIZED_LONG_EDGE = 1024
SHARPNESS_REVIEW_RATIO = 0.60
SHARPNESS_FAIL_RATIO = 0.45
SHARPNESS_AUTO_FIX_MIN_RATIO = 0.55
SHARPNESS_AUTO_FIX_MAX_RATIO = 0.85

MAX_GLOBAL_BRIGHTNESS_SHIFT = 0.34
MAX_HIGHLIGHT_CLIP_FRACTION = 0.24
MAX_SHADOW_CRUSH_FRACTION = 0.28
MAX_GLOBAL_CHROMATICITY_SHIFT = 0.055

# Gentle sharpening is available but disabled by default until validated on a larger sample.
ENABLE_AUTO_SHARPEN = False

# Gentle sharpening only when normalized output sharpness materially drops.
UNSHARP_RADIUS = 0.8
UNSHARP_PERCENT = 45
UNSHARP_THRESHOLD = 3

# Confidence-gated neutral-surface white-balance analysis.
WB_MIN_NEUTRAL_FRACTION = 0.08
WB_MAX_NEUTRAL_SATURATION = 0.12
WB_MIN_VALUE = 0.25
WB_MAX_VALUE = 0.90
WB_CAST_THRESHOLD = 0.025
WB_MAX_CHANNEL_STD = 0.16
