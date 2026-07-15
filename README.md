# MyEstatePics AI Editor

MyEstatePics AI Editor is a macOS-friendly PySide6 desktop application for
batch-enhancing interior real-estate JPEGs with the preserved MyEstatePics v1.6
OpenAI image-editing workflow. The application is intentionally contained in
one program: `myestatepics_ai_editor.py`. The three files under `legacy/` remain
unchanged as the source-of-truth snapshot.

## Requirements

- macOS (the interface is portable, but Finder buttons are designed for macOS)
- Python 3.11 or newer
- An OpenAI API key with image API access
- Internet access while processing

## Install

Install Python from [python.org](https://www.python.org/downloads/) or Homebrew,
then from this repository run:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

## API key

The application reads the key only from `OPENAI_API_KEY` or a local `.env`
file. It never displays or logs the key.

```bash
cp .env.example .env
```

Edit `.env` and replace the placeholder. `.env` is ignored by Git. You may
instead export `OPENAI_API_KEY` in the shell before launching.

## Launch

```bash
python myestatepics_ai_editor.py
```

## Folder workflow

The default folders are generated under `runtime/`. Use the selectors to choose
other locations. Put `.jpg` or `.jpeg` originals in Incoming and click Analyze.
Start Processing shows a paid-API warning and cost estimate. Originals remain
untouched. Passing outputs go to Completed; flagged outputs go to NeedsReview;
failures produce reports in Error. CSV logs go to Logs and learning history is
stored in a SQLite database in the sibling Data folder. Exact filenames are
preserved and existing Completed/NeedsReview outputs are skipped, not replaced.

The v1.6 model is `gpt-image-2`, with Medium quality. API PNG output is decoded
once, converted to JPEG without upscale/denoise/default sharpening, constrained
to 2 MB where possible, and written atomically. EXIF Orientation is reset while
safe metadata is retained where Pillow supports it.

## Cost warning

Every processed or reprocessed image makes a paid OpenAI API call. The pre-run
estimate uses the preserved observed average of $0.28 per six images and is not
a quote. API-reported token totals are displayed when supplied; the OpenAI usage
dashboard is the billing source of truth.

## Review workflow

Open Review Images to compare the original and processed image side by side.
Accept moves the result to Completed and marks it accepted in learning history.
Reject leaves it in NeedsReview and records rejection. Reprocess asks for
confirmation because it costs money, removes the prior review output, and sends
the image back through the normal paid-run confirmation.

## Cancellation and safety

Processing runs in a worker thread so the interface remains responsive. Cancel
stops between images; an in-flight API request completes first. JPEGs are written
through a temporary file and atomically renamed, so cancellation does not leave
partial output. Failed originals remain in Incoming.

## Tests

Tests use mocked image responses and never make paid API calls:

```bash
python -m pytest -q
```

## Troubleshooting

- **PySide6 missing:** activate the virtual environment and reinstall requirements.
- **API key missing:** confirm `.env` is beside the application and contains
  `OPENAI_API_KEY=...`, or export the variable in the launch shell.
- **No pending images:** confirm JPEGs are in the selected input folder and no
  same-named output already exists.
- **NeedsReview result:** inspect verifier reasons and metrics in Review Images.
- **API errors:** check the CSV log and Error report; transient failures retry
  three times with the preserved backoff.

## Future macOS packaging

PyInstaller can later bundle the single-file program. A packaging spec should
include Qt plugins and the legacy prompt file, set a signed app icon/bundle ID,
and be tested on a clean Mac. Packaging is intentionally not part of this build.

See `docs/legacy-v1.6-baseline.md` for the exact preserved behavior and known
compatibility caveats.
