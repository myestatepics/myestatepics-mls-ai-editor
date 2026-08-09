# Changelog

This file records the production architecture history relevant to the frozen
Direct release. The older engineering timeline remains available in
[`docs/CHANGELOG.md`](docs/CHANGELOG.md).

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
