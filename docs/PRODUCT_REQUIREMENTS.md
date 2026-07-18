# Product Requirements

## Purpose

MyEstatePics AI Editor prepares interior real-estate photographs for MLS use
through conservative image correction. It is designed for repeatable batch
operation while preserving the photographed property as the source of truth.

## Business goals

- reduce repetitive manual correction work for MLS photo batches
- maintain consistent, reviewable output quality
- prevent architectural or material misrepresentation
- expose cost before paid processing starts
- provide a portable macOS application suitable for operational testing

## Target users

The current target user is a real-estate photography operator on macOS who
understands folder-based batch workflows and is responsible for reviewing
outputs before delivery. Multi-user administration and customer self-service
are not implemented.

## Supported workflows

1. Choose or restore an Incoming folder and output folders.
2. Scan `.jpg`, `.jpeg`, and `.png` files.
3. Select all by default or adjust individual checkboxes.
4. Analyze selection, quality, eligibility, and estimated cost.
5. Run a paid production batch or an isolated Demo batch.
6. Review outputs and accept, move, queue for retry, or delete them.

## Functional requirements

Stable requirement IDs:

- **FR-01 Selection:** every eligible supported file is selected after folder
  selection, settings restoration, or rescan; individual toggles remain usable.
- **FR-02 Eligibility:** only a same-named current output in the active mode's
  Completed, NeedsReview, or Error location may block processing. Historical
  records never block it.
- **FR-03 Cost control:** selected count, quality, and estimate update together,
  and paid work requires confirmation before the first request.
- **FR-04 Credential safety:** Production requires a normalized `sk-` key and
  never displays it; Demo requires no key and creates no client.
- **FR-05 Output:** successful output keeps the exact filename, is JPEG data,
  attempts safe metadata preservation, and does not exceed 4.5 MB.
- **FR-06 Disposition:** verifier FAIL or a moderation, decode, dimension,
  corruption, or hard-size problem routes to NeedsReview with a reason; an
  exception writes an Error report.
- **FR-07 Isolation:** Demo and Production outputs, history, and eligibility
  never affect each other.
- **FR-08 Audit:** CSV and SQLite capture mode, quality, disposition, reason,
  prompt version, cost/usage where available, and final review label.

- Preserve the original file and exact filename.
- Support Low and Medium quality, with Low as the default.
- Process only checked and eligible files.
- Skip a filename only while a same-named output exists in the active
  Completed, NeedsReview, or Error folder.
- Confirm a production batch before creating API requests.
- Never create an OpenAI client in Demo Mode.
- Route passing output to Completed and problematic output to NeedsReview.
- Record explicit NeedsReview reasons and errors.
- Export JPEG data with orientation normalized and safe metadata preserved.
- Maintain CSV run logs and SQLite history without using history to block retry.
- Permit cancellation between images.

## Non-functional requirements

- The GUI must remain responsive while a worker thread processes a batch.
- Packaged execution must not depend on the repository or current directory.
- Secrets must not be bundled or logged.
- Demo and production data must remain isolated.
- Output writes must be atomic.
- Tests must not make paid API calls.

## Scope

Current scope is single-machine macOS batch processing of interior property
photos through one fixed OpenAI image model and one production prompt.

## Out of scope

- exterior-specific editing workflows
- automatic upload to MLS or delivery platforms
- cloud accounts, teams, permissions, or synchronization
- automatic billing reconciliation
- arbitrary prompt editing
- object removal, virtual staging, or architectural redesign
- Windows installers, notarization, or App Store distribution

## Folder workflow

Incoming remains user-controlled. Real outputs use Completed, NeedsReview,
Error, Logs, and a sibling Data directory. Folder collisions are rejected.
Scanning occurs on startup, folder selection, Rescan, and Analyze; there is no
continuous filesystem watcher. Saved history does not determine eligibility.

## Review workflow

Review Results compares the original and output. Accept moves a NeedsReview
file to Completed. Move to Needs Review performs the reverse routing. Retry
removes the active output and queues the source; it does not call the API until
the user starts a confirmed batch. Delete Output leaves the original intact.

## Demo Mode

Demo Mode requires no key and makes no OpenAI client or request. It copies the
source as a simulated output, records DEMO history/log data, supports simulated
pass/review/error outcomes, and uses only the active runtime's `Demo`
destinations. Simulated errors create reports rather than processed images.

## Production Mode

Production Mode requires a valid `sk-` key, displays estimated cost, and
requires confirmation. It calls `gpt-image-2`, records returned usage when
available, and uses fallback cost estimates for presentation and records.

## Packaging

PyInstaller produces a windowed macOS `.app`; an optional script creates a DMG.
Packaged resources are read from the bundle. Preferences, key configuration,
startup logs, runtime data, and cache live under Application Support.
Artifacts are architecture-specific and ad-hoc signed until a notarized
distribution workflow is approved.

## Release acceptance

Acceptance requires all FR items, a green no-paid-call suite, a Demo smoke test
from the built DMG, verified metadata and secret exclusion, working
documentation links, and the objective checks in
[QUALITY_STANDARDS.md](QUALITY_STANDARDS.md). A verifier REVIEW advisory is
logged but does not currently force NeedsReview.

## Future commercial direction

Commercial readiness requires broader regression coverage, signed and
notarized releases, support procedures, usage and cost telemetry with an
approved privacy design, operational performance work, and validated delivery
integration. These are roadmap items, not current features.
