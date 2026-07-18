# Changelog

This changelog summarizes major engineering milestones. Early rapid-prototype
commits were not consistently version-tagged.

Future tagged releases must add an ISO date, tested commit, artifact checksum,
and download reference.

## Unreleased

- Documentation audit corrections only; application code, tests, and the
  production prompt are unchanged.

## Production v2.1 RC1

### Added

- production-oriented four-section GUI
- automatic Incoming scan and default Select All
- individual checkbox selection and immediate cost/count updates
- remembered folders, window size, Demo state, and Advanced Folders state
- paid-batch confirmation based on checked eligible images
- review actions for accept, move, retry queue, and output deletion
- portable PyInstaller `.app` and optional DMG workflow
- Application Support preferences, runtime storage, diagnostics, and packaged key
- startup logging and secret-exclusion build checks

### Fixed

- current-output-only reprocessing eligibility
- isolation between Demo and production skip states
- stale checkbox events clearing restored-folder selection
- source and packaged `.env` path handling
- normal successful results being routed too aggressively to NeedsReview

## Production v2.0 RC1

- externalized the production prompt
- appended architectural fidelity safeguards without rewriting the successful
  v1.6 correction baseline
- separated application and prompt version identifiers
- added prompt regressions for mirrors, invented windows, and openings

## Production v1.6 baseline

- established the conservative MLS interior prompt
- preserved filename, JPEG export, EXIF orientation, verifier, CSV, retry, and
  SQLite behavior
- introduced deterministic brightness-independent chromaticity checks

See [Legacy v1.6 Baseline](legacy-v1.6-baseline.md) for the preserved reference.
