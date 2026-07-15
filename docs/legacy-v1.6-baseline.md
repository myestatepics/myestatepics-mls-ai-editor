# MyEstatePics v1.6 behavior baseline

This document records the preserved behavior in `legacy/` before modularization.
The legacy files are immutable source material and remain the reference for all
thresholds, prompt text, and image-processing decisions.

## Configuration

- Program and prompt version: `1.6`.
- Model: `gpt-image-2`; quality: `medium`.
- Inputs are `.jpg` and `.jpeg` files (case-insensitive).
- Native API sizes are `1536x1024` landscape, `1024x1536` portrait, and
  `1024x1024` square.
- JPEG target is at most 2,000,000 bytes. Encoding begins at quality 95,
  descends by 2, and stops at 79 (the range does not reach the configured
  minimum of 78). Output is optimized, non-progressive, 4:4:4, at 300 DPI.
- Fallback cost is based on the observed average `$0.28 / 6` images.
- API retries: three attempts, with transient HTTP/API failures retried after
  3 and 6 seconds.

The legacy configuration hardcodes its runtime root. The modular application
replaces that path with project-relative defaults and user-selectable folders;
this path-only change does not alter processing behavior.

## Folder workflow

The legacy script creates Incoming, Completed, NeedsReview, Error, Logs, and
Data. It sorts all input JPEGs, skips a filename already present in Completed
or NeedsReview, asks for spending confirmation, and processes remaining files
sequentially. Originals never move or change. A successful verifier PASS is
written to Completed; REVIEW and FAIL verifier results are written to
NeedsReview. Runtime exceptions produce an Error text report, while the source
JPEG remains in Incoming. Existing outputs are skipped rather than overwritten.

## Analysis and prompt construction

Input analysis downsizes only for measurement (maximum edge 1200), converts to
RGB, and calculates luminance mean, 10th-to-90th percentile contrast span,
shadow fraction below 0.18, highlight fraction above 0.92, normalized edge
sharpness, and conservative neutral-candidate white balance. Neutral candidates
must meet the configured saturation/value/fraction/consistency gates. The
adaptive prompt chooses one of three exposure messages, one of three contrast
messages, a confidence-gated white-balance message, and the fixed sky warning.
It is appended to the complete v1.6 production prompt without changing it.

## API call and response

Each attempt reopens the source and calls `client.images.edit` with the model,
prompt, orientation-specific native size, configured quality, and PNG output.
The first response image's base64 is decoded exactly once. Usage accepts dict or
object forms and captures input, output, total tokens, and a raw representation
when returned. The PNG is opened once and converted to an RGB Pillow image.

## Verification

Source and output are independently normalized to a maximum edge of 1024.
Sharpness is mean squared luminance-gradient energy; ratios below 0.45 FAIL and
below 0.60 REVIEW. Absolute mean-brightness change is checked against the
larger of 0.34 and the legacy adaptive limit (0.45, 0.38, 0.32, 0.27, or 0.22
by source mean). Output highlight clipping over 24% and shadow crushing over
28% trigger review. Global color drift uses median per-pixel RGB chromaticity,
excluding unstable dark/clipped pixels, and triggers review above 0.055. This
metric is intentionally independent of uniform brightness scaling.

Optional unsharp masking exists but is disabled. Therefore v1.6 performs no
post-generation upscale, denoise, or sharpening in its default configuration.

## JPEG and metadata

Generated RGB pixels are JPEG-encoded once per attempted quality; the first
result within the byte limit is used. If even the final attempted quality is
oversized, those bytes are retained and the image is routed to NeedsReview.
Readable source EXIF is preserved where Pillow can serialize it, Orientation is
reset to 1, and PixelX/YDimension are set to output dimensions. The source ICC
profile is retained where available. The exact source filename is used.

## Logging, history, and summary

Every processed or failed image appends a CSV row containing configuration,
analysis, verifier, output, usage, fallback-cost, and message fields. SQLite
stores the corresponding per-image history. Completed files infer ACCEPTED,
NeedsReview files UNRESOLVED, and errors FAILED. At startup, unresolved records
whose file has later moved to Completed become ACCEPTED. The final console
summary reports route counts, skips, fallback spend, any API-reported token
totals, and runtime locations.

## Baseline caveats preserved for compatibility

- Verifier status `FAIL` is routed to NeedsReview, not Error.
- The configured JPEG minimum is 78 but the descending range attempts 79 last.
- Error handling creates a report, not a copied original in Error.
- The legacy module imports its config by filename and embeds an absolute
  runtime root; neither legacy file is changed.
