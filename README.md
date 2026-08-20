# MyEstatePics AI Editor — Direct V4.0.3

MyEstatePics AI Editor is a macOS PySide6 desktop application for conservative,
batch-oriented enhancement of real-estate photographs. The frozen production
baseline is the **Direct** edition on branch
`restore/direct-image-edit-production`. It sends each image directly to
OpenAI's Images Edit endpoint using `gpt-image-2`; it does not use the Responses
API or GPT-5.6.

The source photograph remains untouched. A successful result is exported as a
JPEG with the original filename and routed to `Completed` or `NeedsReview`.
Errors are recorded separately. The production prompt prioritizes architectural
and material fidelity, existing-window-only edits, natural MLS window pulls,
and protection of mirrors and reflections.

Each processed batch also creates local Before/After contact-sheet PDFs under
`Completed/Batch Reviews/<run-id>/`. They are review documents only: they do
not alter JPEG outputs or make any API request.

V4.0.3 production validation is governed by the
[regression manifest](docs/V4_0_3_REGRESSION_MANIFEST.md). It distinguishes
offline checks from individually approved paid-image validation.

## Production status

- Production source baseline before this documentation freeze:
  `8321ed302e74acfca4079c8c948cd43310f879b0` (V3.1.1 frozen baseline)
- Application badge: `Production v4.0.3` / `Prompt V4.0.3` (release date: `2026-08-20`)
- Production model: `gpt-image-2`
- Production endpoint: `/v1/images/edits`
- API requests: one direct image-edit request per successful image; a genuine
  transient failure can retry the same request
- Current installer: internal Apple Silicon, ad-hoc-signed build; it is not
  notarized for public distribution

The application reports estimated costs. The OpenAI dashboard is the authority
for actual charges.

## Install the Direct application

1. Open `MyEstatePics AI Editor - Direct V4.0.3.dmg`.
2. Drag **MyEstatePics AI Editor - Direct V4.0.3** to **Applications**.
3. On first launch, Control-click the application, choose **Open**, and confirm
   **Open** if macOS warns that the developer cannot be verified.
4. Add the API key to:

   ```text
   ~/Library/Application Support/MyEstatePics AI Editor - Direct/.env
   ```

   using:

   ```text
   OPENAI_API_KEY=sk-your-key
   ```

The Direct edition uses a separate Application Support directory, so it can
remain installed beside the earlier application. On its first launch only, it
can copy an existing `.env` from
`~/Library/Application Support/MyEstatePics AI Editor/.env`; the original file
is never moved, edited, or deleted.

## Launch from source

Development requires macOS, Python 3.11 or newer, and the dependencies in
`requirements.txt`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python myestatepics_ai_editor.py
```

Source execution reads `.env` from the repository root. The API key is never
bundled, displayed, or written to logs. Demo Mode works without a key and makes
no API calls.

## Quality selection

The **Quality** selector supports:

- **Smart** — local-only assessment chooses Medium only for a confirmed
  substantial exterior-facing opening; clear no-window and unconfirmed cases
  use Low
- **Low** — manual lower-cost override
- **Medium** — sends `quality="medium"` to the same direct Images Edit request
- **High** — sends `quality="high"` to the same direct Images Edit request

Changing quality updates the estimate immediately. The selected value is
included in logs and the batch summary. Always confirm current OpenAI pricing
and validate actual cost in the OpenAI dashboard before a large production
batch.

## Editing Memory

V4.0 adds a local-only **Editing Memory** dialog. It persists approved editing
lessons in the existing Direct Application Support directory:

```text
~/Library/Application Support/MyEstatePics AI Editor - Direct/learned_rules.json
```

Only enabled `APPROVED` rules are appended to the source-controlled master
prompt; `PROPOSED` and `DISABLED` rules cannot affect processing. The agent
makes no API call, never rewrites the master prompt, and records its local
audit events in `feedback_history.jsonl` beside the rule database.

## Basic usage

1. Choose the Incoming and Completed folders.
2. Review the automatically selected supported `.jpg`, `.jpeg`, and `.png`
   images. Uncheck any image that should not run.
3. Select **Smart**, **Low**, **Medium**, or **High**. Smart never uses Auto or
   High automatically.
4. Use **Analyze** to refresh eligibility and estimated cost.
5. Click **Start Processing**, review the paid-processing confirmation, and
   approve it.
6. Use **Review Results** for files routed to `NeedsReview`.

An image is eligible again when no same-named output exists in the active
`Completed`, `NeedsReview`, or `Error` result folder. Historical CSV and SQLite
records do not block reprocessing.

**Cancel** is cooperative: it keeps the GUI responsive, lets the current HTTP
request finish naturally, preserves any completed output, and prevents the
next queued image from starting.

## Data and logs

The packaged Direct application stores its private configuration and default
runtime data under:

```text
~/Library/Application Support/MyEstatePics AI Editor - Direct/
├── .env
├── preferences.ini
├── Logs/application.log
└── runtime/
    ├── Incoming/
    ├── Completed/
    ├── NeedsReview/
    ├── Error/
    ├── Logs/
    └── Data/image_history.sqlite3
```

User-selected Incoming and Completed folders may be elsewhere. Source mode
uses the repository `runtime/` directory for processing data and Application
Support for startup diagnostics and preferences.

## Build and test

See [Build and Release](docs/BUILD_RELEASE.md) for the exact Direct-edition
build, signing, DMG, checksum, and clean-rebuild procedure.

Offline validation:

```bash
python3 -m py_compile myestatepics_ai_editor.py
pytest -q
git diff --check
```

Tests use mocks and Demo Mode and make no paid API calls.

## Production documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Prompt Guide](docs/PROMPT_GUIDE.md)
- [Build and Release](docs/BUILD_RELEASE.md)
- [Test Plan](docs/TEST_PLAN.md)
- [Changelog](CHANGELOG.md)

Additional engineering references:

- [Product Requirements](docs/PRODUCT_REQUIREMENTS.md)
- [Technical Design](docs/TECHNICAL_DESIGN.md)
- [Prompt Specification](docs/PROMPT_SPECIFICATION.md)
- [Quality Standards](docs/QUALITY_STANDARDS.md)
- [User Guide](docs/USER_GUIDE.md)
- [Build Guide](docs/BUILD_GUIDE.md)
- [Release Process](docs/RELEASE_PROCESS.md)
- [Roadmap](docs/ROADMAP.md)
- [Historical Changelog](docs/CHANGELOG.md)
- [Production Readiness Audit](production_audit.md)
- [Legacy v1.6 Baseline](docs/legacy-v1.6-baseline.md)
- [ADR Index](docs/adr/README.md)

## License

License terms have not been selected. Add an approved `LICENSE` before external
distribution.
