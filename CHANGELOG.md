# Changelog

This file records the production architecture history relevant to the frozen
Direct release. The older engineering timeline remains available in
[`docs/CHANGELOG.md`](docs/CHANGELOG.md).

## V4.0.4 window-pull recovery — 2026-08-20

- New versioned application and installer name: `MyEstatePics AI Editor - Direct V4.0.4`.
- Restores the historical direct-production requirement for a crisp, clear,
  immediately readable exterior through every genuine exterior-facing window.
  A white, washed-out, hazy, or barely visible window/sky is explicitly a
  failed MLS window pull.
- Keeps source-bound exterior objects and geometry protected while authorizing
  a light natural MLS-blue fallback only for genuinely clipped sky pixels.
- Makes Medium the normal production default. Low, Medium, and High remain
  manual choices; High is never automatic and OpenAI Auto remains unavailable.
- Retains the one-request Direct Images Edit workflow, local Premium Finish,
  material-color protections, mirror-equipment cleanup, and all V4.0.3
  engineering protections.

## V4.0.3 final production stabilization — 2026-08-20

- New versioned application and installer name: `MyEstatePics AI Editor - Direct V4.0.3`.
- Establishes the release priority order: real window pull first, then material
  fidelity, interior exposure, surface uniformity, natural depth, and finally
  Smart Cost optimization.
- Consolidates the authoritative window rule so real exterior information is
  recovered first and an irrecoverably clipped visible-sky portion may receive
  only a restrained light-natural-blue fallback. Exterior architecture and
  objects remain source-bound and must never be invented.
- Refines Smart Cost so only confirmed `WINDOW_PULL_REQUIRED` assessments use
  Medium. `UNCERTAIN` no longer raises cost by itself; Low, Medium, and High
  manual selections remain unchanged, and Auto remains unavailable.
- Retains the V4.0.2 one-request direct Images Edit architecture, local Premium
  Finish, JPEG settings, PDF reliability fix, folder protections, and output
  disappearance diagnostics.

## V4.0.2 production reliability and Smart Cost update

- New versioned application and installer name: `MyEstatePics AI Editor - Direct V4.0.2`.
- Carries forward the V4.0.1 partial-batch reliability fix: BEFORE and AFTER
  review PDFs finalize independently, failed or missing outputs retain their
  matched position, and batch summaries report their separate PDF states.
- Records write-time and finalization-time output existence, size, SHA-256, and
  timestamp evidence. A previously verified missing output is logged as
  `OUTPUT_MISSING_AFTER_SUCCESS`; it is never automatically reprocessed.
- Smart Cost now selects Low for clear no-window, closet, hallway, basement,
  detail, and tiny-incidental-opening cases. Medium remains reserved for
  substantial or genuinely ambiguous window/exterior-view openings; High stays
  manual-only and Auto remains unavailable.
- The authoritative prompt retains the factual exterior lock and strong window
  pull, with a bright natural MLS exposure target and light, soft,
  low-saturation source-supported sky guidance.

## V4.0.1 production hotfix

- New versioned application and installer name: `MyEstatePics AI Editor - Direct V4.0.1`.
- Smart Cost now selects Medium for meaningful or uncertain window/exterior-view
  openings; Low is reserved for a clear no-window-pull assessment. Smart remains
  local-only and never selects Auto or High.
- Consolidated all window, sky, and sheer-curtain guidance into one authoritative
  source-controlled Window Fidelity block. Learned rules in those categories are
  suppressed and logged; non-conflicting approved learned rules remain active.
- The one-call Direct Images Edit architecture, quality mappings, Premium Finish,
  JPEG export, folder workflow, and Before/After PDFs are unchanged.

## V4.0 local editing agent

- New versioned application name: `MyEstatePics AI Editor - Direct V4.0`.
- Adds a zero-API local Editing Memory with persisted approved rules and audit
  history under the existing Direct Application Support directory.
- The source-controlled V3.1.1 production prompt remains authoritative; only
  relevant enabled approved rules are appended for a single direct image edit.
- Keeps the V3.1.1 Direct Images Edit, size, quality, JPEG, Premium Finish,
  cancellation, and local Before/After PDF behavior unchanged.

## V3.1.1 production hotfix

- Application version: `3.1.1`; prompt version: `V3.1.1`.
- Strengthened source-supported factual fidelity for exterior information seen
  through windows, including sheer-curtain, screen, blind, and reflection
  limitations; unresolved exterior content must remain unresolved.
- Added local-only, deterministic Before/After batch review PDFs. PDF failures
  are reported separately and never affect processed JPEG outcomes or API use.

## V3.1 production patch

- Application version: `3.1.0`; prompt version: `V3.1`.
- Final JPEG export is a single maximum-quality `100`, 4:4:4 encode with no
  file-size limit, quality reduction, resize, or oversize review routing.
- The authoritative production prompt retains the strong existing-window pull
  while requiring a restrained, natural light-blue sky through confirmed
  interior windows only.

## Direct production freeze

### Production baseline

- Source baseline:
  `b5b6b231d626551198f5e440f0faa4be99d03020`
- Branch: `restore/direct-image-edit-production`
- Application badge: `Production v2.1 RC1`
- Prompt badge: `Prompt v2.0 RC1`
- Installer: `MyEstatePics AI Editor - Direct`
- Architecture: direct `/v1/images/edits` using `gpt-image-2`
- Quality modes: Low and Medium

This freeze adds documentation only. Production application code, tests,
prompt content, API behavior, retry handling, cancellation, sizing, export,
logging, and packaging behavior remain unchanged.

## Direct Images Edit restoration

Commit `fb8b6d8ac6648a86dce6fb7ddbad0cc8626633c4` restored the direct Images Edit
production path:

- one direct `client.images.edit(...)` operation per successful image;
- `gpt-image-2`;
- Low default with Medium support;
- PNG API output followed by controlled JPEG export;
- no GPT-5.6 orchestration;
- no Responses API production call;
- no separate paid image-analysis request;
- no automatic cross-API fallback;
- cooperative cancellation;
- direct-request path, dimensions, timing, and estimated-cost logging;
- separate Direct Application Support identity; and
- safe one-time `.env` migration.

The rollback was required because the Responses workflow was measured at
approximately $0.29 and 111 seconds per image, outside the commercial target of
approximately $0.02–$0.03 and less than 30 seconds per image. Those measurements
were specific production observations, not a guaranteed cost or latency for
all configurations.

## Prompt refinements

Commit `b5b6b231d626551198f5e440f0faa4be99d03020` refined only the production
prompt:

- highest-priority structural lock;
- never create a window where none exists;
- existing-window-only editing;
- strong MLS window pull;
- natural blue replacement limited to actual exterior sky;
- preservation of houses, roofs, trees, decks, fences, landscaping, roads,
  driveways, vehicles, and other exterior objects;
- mirror, reflection, shower-glass, glossy-surface, and fake-window protection;
  and
- material, geometry, perspective, and furniture preservation.

The prompt bundled in the Direct installer built from this commit matches the
source prompt byte-for-byte.

## Responses API migration and rollback

Commit `a50b84391de3785dfa8d86c8d599dfa2854ed4b3` implemented a Responses API image
generation workflow with GPT-5.6 orchestration and retained the cancellation
fix. Commit `40148058d360a1ceaa4095c8c43b0b7c5e51966f` finalized that development
baseline and packaging isolation.

Production testing rejected that architecture because measured cost and
latency exceeded the commercial targets. The commits remain in history; they
were not rewritten or deleted. The Direct restoration removed Responses and
GPT-5.6 from the current production processing path rather than modifying
historical commits.

## Earlier baseline

The v1.6 engine established filename preservation, JPEG export, EXIF handling,
verification, retry behavior, CSV logging, SQLite history, and the conservative
MLS correction prompt. Later releases added the GUI, selected-file processing,
Demo Mode, current-output-only reprocessing, Application Support packaging,
automatic selection, architectural safeguards, and cooperative cancellation.
- V3 production stabilization: safe path-setting recovery, explicit Low/Medium/High
  selection, V3 prompt controls, local Premium Finish, and stage timing logs.
