# MyEstatePics AI Editor

MyEstatePics AI Editor is a PySide6 desktop application for conservative,
batch-oriented enhancement of interior real-estate photographs. Production
v2.1 RC1 uses the externally stored MLS production prompt v2.0 RC1 and the
OpenAI image editing API. The original images remain in place, output filenames
are preserved, and deterministic checks route results to Completed,
NeedsReview, or Error.

## Features

- macOS desktop interface with source and standalone `.app` launch modes
- automatic Incoming-folder scan and default selection of supported images
- individual image selection, Low and Medium quality, and cost estimation
- paid-production confirmation and isolated no-cost Demo Mode
- conservative prompt with architectural and material fidelity safeguards
- deterministic output verification and explicit NeedsReview reasons
- JPEG export with preserved safe EXIF, normalized orientation, and a 2 MB goal
- CSV run logs and SQLite learning history
- side-by-side review with Accept, Move to Needs Review, Retry, and Delete
- portable PyInstaller app and optional DMG build

The current DMG is an internal, architecture-specific, ad-hoc-signed release
candidate. It is not a universal, Developer ID-signed, or notarized public
installer. Supported input formats are `.jpg`, `.jpeg`, and `.png`.

## Screenshots

Screenshots are intentionally not committed yet. Future release documentation
should add:

- main batch window
- Demo Mode banner and result controls
- side-by-side Review Results window

## Quick start

Development requires macOS, Python 3.11 or newer, and the dependencies in
`requirements.txt`.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python myestatepics_ai_editor.py
```

Source execution reads `.env` from the repository root. The packaged app reads
`.env` from:

```text
~/Library/Application Support/MyEstatePics AI Editor/.env
```

The required format is `OPENAI_API_KEY=sk-...`. The key is never bundled,
displayed, or written to logs.

## Architecture

The application is intentionally implemented in
`myestatepics_ai_editor.py`. It contains configuration, analysis, API
integration, verification, persistence, Demo Mode, and the PySide6 GUI. The
read-only production prompt is loaded from `prompts/mls_production.txt`.
Writable packaged data is stored in macOS Application Support.

Folder scans are event-driven, not continuous: the application scans when a
folder is selected or restored, when Rescan/Analyze is used, and after a batch.

See [Technical Design](docs/TECHNICAL_DESIGN.md) for component and data-flow
details.

## Build

```bash
./build_macos.sh
./build_dmg.sh
```

Outputs are created under `dist/`. See [Build Guide](docs/BUILD_GUIDE.md).

## Documentation

- [Product Requirements](docs/PRODUCT_REQUIREMENTS.md)
- [Technical Design](docs/TECHNICAL_DESIGN.md)
- [Prompt Specification](docs/PROMPT_SPECIFICATION.md)
- [Quality Standards](docs/QUALITY_STANDARDS.md)
- [User Guide](docs/USER_GUIDE.md)
- [Build Guide](docs/BUILD_GUIDE.md)
- [Release Process](docs/RELEASE_PROCESS.md)
- [Roadmap](docs/ROADMAP.md)
- [Changelog](docs/CHANGELOG.md)
- [Production Readiness Audit](production_audit.md)
- [Legacy v1.6 Baseline](docs/legacy-v1.6-baseline.md)
- [ADR Index](docs/adr/README.md)
- [ADR-001: Application Support Storage](docs/adr/ADR-001-application-support-storage.md)
- [ADR-002: Portable macOS Packaging](docs/adr/ADR-002-portable-macos-packaging.md)
- [ADR-003: Architectural Geometry Lock](docs/adr/ADR-003-architectural-geometry-lock.md)
- [ADR-004: Prompt Externalization](docs/adr/ADR-004-prompt-externalization.md)
- [ADR-005: Demo Mode](docs/adr/ADR-005-demo-mode.md)
- [Detailed macOS Build Notes](BUILD_MAC.md)

## Tests

Tests use mocks and Demo Mode; they do not make paid API calls.

```bash
python3 -m py_compile myestatepics_ai_editor.py
pytest -q
```

## License

License terms have not yet been selected. Add an approved `LICENSE` file before
external distribution.

## Roadmap

Planned engineering work is maintained in [ROADMAP.md](docs/ROADMAP.md).
