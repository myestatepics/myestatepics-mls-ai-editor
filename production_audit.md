# Production Readiness Audit

**Application:** MyEstatePics AI Editor, Production v1.0.0
**Audit scope:** entire repository at commit `deda4ac78934579b407239047c7bd079edd15d4f`  
**Audit date:** 2026-07-18  
**Decision:** **Not ready for unattended thousand-image production batches**

## Executive summary

The application has a sound conservative processing baseline, isolated Demo
Mode, atomic final-image replacement, useful per-image history, and 55 passing
automated tests. Those strengths do not yet provide production-grade recovery
or operational safety.

Two release-blocking issues can corrupt batch consistency or leave the GUI
permanently busy: runtime destinations are mutable process globals that remain
changeable during a batch, and exceptions outside the per-image `try` block can
terminate the worker without emitting its completion signal. High-priority
gaps also exist in HTTP timeout/retry policy, crash recovery, disk-capacity
handling, settings validation, large-folder responsiveness, log retention,
dependency reproducibility, and multi-instance protection.

No finding below recommends a new user-facing feature or GUI redesign. The
recommended work is defensive hardening of current behavior.

## Method

The audit reviewed:

- all 3,234 lines of `myestatepics_ai_editor.py`;
- both packaging scripts and dependency declarations;
- all 1,008 lines of current tests plus the legacy-baseline tests;
- the production prompt only as an integration resource, without proposing a
  prompt change;
- runtime paths, API calls, image decode/encode, SQLite/CSV persistence,
  cancellation, review operations, settings, logging, and packaging;
- `python3 -m py_compile` and `pytest -q` using a temporary bytecode cache.

Validation result: **55 tests passed in 1.57 seconds; no paid API call was
made.**

## Priority order

| Priority | Finding | Severity | Release position |
|---|---|---|---|
| 1 | PH-001 Mutable global runtime state during active work | Critical | Block release |
| 2 | PH-002 Worker can exit without completion or UI recovery | Critical | Block release |
| 3 | PH-003 No bounded API timeout or authoritative retry policy | High | Block large-batch release |
| 4 | PH-004 No durable batch journal or deterministic crash resume | High | Block unattended operation |
| 5 | PH-005 Output, log, and history commits are not transactional | High | Block unattended operation |
| 6 | PH-006 No preflight disk-space and writability validation | High | Block large-batch release |
| 7 | PH-007 Large-folder scanning blocks the GUI thread | High | Fix before 500+ image use |
| 8 | PH-008 Settings and folder containment are insufficiently validated | High | Fix before broad deployment |
| 9 | PH-009 Multiple application instances can corrupt shared state | High | Fix before broad deployment |
| 10 | PH-010 Error artifacts do not participate consistently in eligibility | High | Fix before reliable resume |

## Critical findings

### PH-001 — Mutable global runtime state during active work

- **Severity:** Critical
- **Description:** `INPUT_DIR`, `OUTPUT_DIR`, `REVIEW_DIR`, `ERROR_DIR`,
  `LOG_DIR`, `DATA_DIR`, `HISTORY_DB`, and `QUALITY` are mutable module globals.
  GUI controls are not frozen as a complete job configuration while the worker
  runs. Demo toggling, folder actions, review actions, or quality changes can
  reconfigure globals that the active worker reads between images.
- **Risk:** A single batch can write images, CSV rows, and SQLite records into
  different real/Demo or customer folders. Review actions may target the wrong
  job. This is a data-integrity and billing-audit failure.
- **Recommended fix:** Create an immutable per-batch context containing all
  resolved paths, quality, prompt/version, mode, and cost policy. Pass it to
  every processing and persistence function. Disable configuration and review
  mutations until the worker reaches a terminal state.
- **Estimated effort:** 3–5 developer days plus regression tests.

### PH-002 — Worker can exit without completion or UI recovery

- **Severity:** Critical
- **Description:** `Worker.run()` has no top-level `try/finally`. Failures in
  prompt loading, directory creation, database initialization, reconciliation,
  log creation, or other code outside the per-image handler prevent
  `complete.emit()`. Qt then receives no normal terminal signal.
- **Risk:** The GUI can remain in `processing_active` state indefinitely with
  Start disabled and cancellation ineffective. An uncaught worker exception
  may also terminate the application depending on Qt/Python behavior.
- **Recommended fix:** Wrap the whole worker lifecycle, emit a structured fatal
  result in `except`, and emit exactly one terminal signal in `finally`. Reset
  all GUI state from the terminal handler. Add tests for every pre-loop failure.
- **Estimated effort:** 1–2 developer days.

## High findings

### PH-003 — No bounded API timeout or authoritative retry policy

- **Severity:** High
- **Description:** The OpenAI client is constructed without an explicit
  timeout. Application retries are layered on top of SDK behavior, use fixed
  exponential delays without jitter, ignore `Retry-After`, and classify errors
  by status/name only.
- **Risk:** A request may occupy the sole worker for an excessive period,
  cancellation cannot interrupt it, and combined SDK/application retries can
  multiply delay or charges. Rate-limit storms across long batches are likely
  to recur in lockstep.
- **Recommended fix:** Define connect/read/total timeouts, select one retry
  owner, honor server delay guidance, add capped exponential backoff with
  jitter, and classify authentication, quota, moderation, malformed response,
  timeout, and transient server failures separately.
- **Estimated effort:** 2–3 developer days.

### PH-004 — No durable batch journal or deterministic crash resume

- **Severity:** High
- **Description:** Queue state exists only in memory. Current-output existence
  provides limited de-duplication but records no planned order, attempt state,
  source fingerprint, cancellation checkpoint, or terminal reason.
- **Risk:** After a crash or power loss, operators cannot distinguish never
  attempted, request submitted, response received, output committed, or history
  failed. Re-running may repeat paid requests or silently use a changed source.
- **Recommended fix:** Add a durable job/attempt journal in SQLite with batch
  ID, source path/fingerprint, state transitions, timestamps, request attempt,
  destination, and terminal reason. Reconcile incomplete states at startup.
- **Estimated effort:** 5–8 developer days.

### PH-005 — Output, CSV, and SQLite commits are not transactional

- **Severity:** High
- **Description:** The image is committed first, followed by CSV and SQLite.
  If logging/history fails, the broad per-image handler counts the item as
  failed and writes an Error report even though a valid Completed/NeedsReview
  image already exists. CSV appends and error reports are not atomic or synced.
- **Risk:** Summary, output folders, CSV, and history can disagree. Retry is
  then blocked by an output that the run reported as failed.
- **Recommended fix:** Define an explicit commit protocol. Persist attempt
  state, atomically commit output, then finalize history; treat secondary
  telemetry failure separately from image failure. Reconcile interrupted
  commits on startup and test injected failure after every step.
- **Estimated effort:** 3–5 developer days.

### PH-006 — No disk-space and writability preflight

- **Severity:** High
- **Description:** Folder validation checks equality but not free space,
  permissions, read-only volumes, path containment, SQLite creation, or a
  representative atomic replace. Temporary JPEGs are written beside the final
  file without an `fsync`.
- **Risk:** A 500-image batch may spend money before discovering that output,
  log, or data storage is full or unwritable. Power loss can leave metadata and
  directory entries unflushed.
- **Recommended fix:** Before paid confirmation, verify source readability,
  destination containment, writable probe/atomic rename, SQLite transaction,
  and conservative free-space headroom. Add file and directory syncing where
  durability is required and provide a distinct disk-full terminal reason.
- **Estimated effort:** 2–4 developer days.

### PH-007 — Large-folder scanning blocks the GUI thread

- **Severity:** High
- **Description:** Scan/table refresh runs synchronously on the GUI thread.
  Every row performs `stat()` and opens the image to read dimensions. The table
  is rebuilt repeatedly during selection, Analyze, and each successful image.
- **Risk:** With 500+ images or slow/network storage, the application appears
  frozen for long periods and performs quadratic-feeling repeated I/O. Finder
  may label it unresponsive.
- **Recommended fix:** Move metadata scanning to a cancellable background task,
  cache metadata by path/size/mtime for the current scan, batch UI updates, and
  avoid rebuilding every row after each completion.
- **Estimated effort:** 4–6 developer days.

### PH-008 — Settings and folder containment are insufficiently validated

- **Severity:** High
- **Description:** QSettings values are converted directly to `Path`. There is
  no schema/version, corruption quarantine, type/length validation, or recovery
  record. Folder validation rejects only equal paths; it does not reject nested
  result/source layouts despite documented policy.
- **Risk:** Corrupt or malicious settings can cause startup exceptions, create
  unexpected directories, target unavailable volumes, or mix source and result
  trees. Recovery requires manual file deletion.
- **Recommended fix:** Centralize a versioned settings schema; normalize and
  validate every value; reject ancestor/descendant conflicts; fall back to safe
  defaults while preserving a renamed corrupt settings file for diagnosis.
- **Estimated effort:** 2–3 developer days.

### PH-009 — Multiple application instances can corrupt shared state

- **Severity:** High
- **Description:** There is no single-instance lock or per-job ownership.
  Two instances can use the same folders, fixed temporary filename, run CSV
  timestamp, and SQLite database simultaneously.
- **Risk:** Duplicate paid requests, overwritten temporary/output files,
  SQLite lock failures, mixed CSV rows, and contradictory review labels.
- **Recommended fix:** Add an application/job lock with stale-lock recovery.
  Also use unique temporary files and collision-resistant run IDs so defensive
  correctness does not depend solely on the UI lock.
- **Estimated effort:** 2–4 developer days.

### PH-010 — Error artifacts do not participate consistently in eligibility

- **Severity:** High
- **Description:** Eligibility checks `Error/<original filename>`, while real
  and Demo failures create `<stem>_error.txt`. A failed source is therefore
  immediately eligible even though the GUI/history reports an Error result.
  Files sharing a stem across `.jpg`, `.jpeg`, and `.png` also share the same
  error-report name.
- **Risk:** Failed images can be retried unintentionally and incur repeated
  charges; error reports can overwrite each other; displayed skip semantics do
  not match stored artifacts.
- **Recommended fix:** Define one collision-safe error-manifest convention
  keyed by exact source filename (and preferably source fingerprint), and make
  eligibility, review, deletion, and tests use it consistently.
- **Estimated effort:** 1–2 developer days.

### PH-011 — Source identity is filename-only

- **Severity:** High
- **Description:** Output eligibility and most history/review operations use
  only basename. The system records no source hash, size/mtime identity, or
  canonical job identifier. Case variants can collide on the default macOS
  case-insensitive filesystem.
- **Risk:** Replacing an Incoming image while retaining its name is treated as
  the old job. Two customer jobs using the same output folders can block or
  relabel each other.
- **Recommended fix:** Store a source fingerprint and resolved job root in the
  journal/history. Detect case-folded and normalized-Unicode collisions during
  preflight and reject ambiguous batches.
- **Estimated effort:** 2–4 developer days.

### PH-012 — Application shutdown is unsafe during processing

- **Severity:** High
- **Description:** `closeEvent` saves settings and closes without coordinating
  with the running QThread. It does not request cancellation, wait for a safe
  boundary, or prevent review/path teardown while work is active.
- **Risk:** Closing the window can produce “QThread destroyed while running,”
  process termination during writes, or ambiguous paid-request state.
- **Recommended fix:** Implement a controlled shutdown state: refuse immediate
  close during an in-flight request, request cancellation, wait for terminal
  worker cleanup, and record interrupted state durably.
- **Estimated effort:** 1–2 developer days.

## Medium findings

### PH-013 — API response validation is minimal

- **Severity:** Medium
- **Description:** Code assumes `response.data[0].b64_json` exists and decodes
  it without strict Base64 validation, response-count checks, payload-size
  limits, or an explicit content contract.
- **Risk:** API schema changes or malformed/truncated responses produce generic
  exceptions and weak diagnostics; oversized payloads can consume excessive
  memory.
- **Recommended fix:** Validate response shape, non-empty payload, strict Base64,
  maximum encoded/decoded size, image decode, dimensions, and format before
  downstream processing. Preserve sanitized request IDs/error categories.
- **Estimated effort:** 1–2 developer days.

### PH-014 — Network/authentication/quota errors are not actionable

- **Severity:** Medium
- **Description:** All per-image exceptions converge on raw exception text and
  processing continues. Authentication or exhausted quota can therefore fail
  every remaining image one by one.
- **Risk:** Hundreds of redundant failures, noisy reports, slow completion, and
  poor operator guidance.
- **Recommended fix:** Classify batch-fatal errors (authentication, permission,
  exhausted quota, incompatible endpoint) versus item/transient errors. Stop
  the batch on fatal categories and present a sanitized corrective message.
- **Estimated effort:** 1–2 developer days.

### PH-015 — Cancellation latency is unbounded

- **Severity:** Medium
- **Description:** Cancellation is checked only between images. It is not
  checked during retry backoff, image analysis, encoding, SQLite waits, or an
  API request.
- **Risk:** Cancel may appear broken for minutes and shutdown remains unsafe.
- **Recommended fix:** Use interruptible backoff, bounded API timeouts, checks
  between expensive local phases, and an explicit “cancelling/current request
  finishing” state.
- **Estimated effort:** 2–3 developer days.

### PH-016 — Fixed temporary names are collision-prone

- **Severity:** Medium
- **Description:** `_atomic_write` always uses `.<filename>.tmp`.
- **Risk:** Multiple instances, stale files, or two operations targeting the
  same destination can overwrite or delete another writer's temporary file.
- **Recommended fix:** Use `tempfile.NamedTemporaryFile` in the destination
  directory with exclusive creation, then flush, sync, replace, and clean up
  only the owned file.
- **Estimated effort:** Less than 1 day.

### PH-017 — Startup performs unguarded filesystem mutation at import time

- **Severity:** Medium
- **Description:** `application_data_dir()` creates the full directory tree
  while the module imports, before startup logging and GUI error handling.
- **Risk:** A permission problem, damaged home path, or unavailable volume can
  terminate Finder launch with no visible explanation or diagnostic log.
- **Recommended fix:** Separate pure path calculation from directory creation.
  Bootstrap storage inside a guarded startup phase with a minimal fallback log
  and user-safe fatal dialog.
- **Estimated effort:** 1–2 developer days.

### PH-018 — Prompt/resource integrity is checked too late

- **Severity:** Medium
- **Description:** Missing/empty prompt is discovered after a batch starts, and
  the error message still references a legacy prompt filename. There is no
  packaged resource manifest or checksum.
- **Risk:** Paid workflow preparation succeeds but processing fails; packaging
  omissions are detected only in operation; support guidance is misleading.
- **Recommended fix:** Validate required resources and expected prompt checksum
  or release manifest during startup/preflight, before enabling Production.
  Correct the resource-specific error without changing prompt content.
- **Estimated effort:** 1 day.

### PH-019 — Logs have no rotation, retention, or collision-safe run ID

- **Severity:** Medium
- **Description:** `application.log` grows indefinitely. Per-run CSV names have
  second resolution, no batch UUID, no rotation/retention, and no guaranteed
  header/schema version.
- **Risk:** Disk growth, overwritten/mixed same-second runs, difficult support
  correlation, and future parser incompatibility.
- **Recommended fix:** Use a rotating startup log, UUID-based run IDs, explicit
  CSV schema version, retention policy, and batch ID in every diagnostic,
  history record, and error report.
- **Estimated effort:** 2–3 developer days.

### PH-020 — Diagnostic records may expose customer information

- **Severity:** Medium
- **Description:** Logs/history store full folder paths, filenames, error text,
  and raw API usage serialization. Startup logs include user-specific paths.
  No redaction, retention, export policy, or support-bundle scrubber exists.
- **Risk:** Property/customer names and local usernames can leak through copied
  logs or support tickets. Unexpected SDK text could contain sensitive request
  context.
- **Recommended fix:** Define a data classification and retention policy,
  sanitize exception/usage fields, use relative/job-scoped identifiers where
  possible, and provide a documented scrub procedure. Continue never logging
  key material.
- **Estimated effort:** 2–4 developer days.

### PH-021 — API key is plaintext without permission hardening

- **Severity:** Medium
- **Description:** The packaged key is stored in an Application Support `.env`.
  The application does not verify or restrict file permissions and source mode
  can inherit a shell key instead of the repository value.
- **Risk:** Other local users/processes may read an overly permissive file, and
  operators may unknowingly use the wrong account/key.
- **Recommended fix:** Prefer macOS Keychain in a future security change, or at
  minimum require owner-only permissions, warn on unsafe mode, report the
  selected credential source without revealing content, and test precedence
  explicitly.
- **Estimated effort:** 1–2 days for permission hardening; 3–5 for Keychain.

### PH-022 — Image resource limits are absent

- **Severity:** Medium
- **Description:** Supported extension is trusted before decode. There is no
  input byte/pixel limit, decompression-bomb policy, or early dimension sanity
  check. Processing creates several full or normalized image/array copies.
- **Risk:** Corrupt or adversarial files can exhaust memory or CPU, especially
  during large batches. Pillow warnings may not stop dangerous allocations.
- **Recommended fix:** Define maximum source bytes/pixels/dimensions, treat
  decompression-bomb warnings as controlled item failures, validate before API
  submission, and explicitly release large intermediates per iteration.
- **Estimated effort:** 1–2 developer days.

### PH-023 — SQLite durability and migration policy is incomplete

- **Severity:** Medium
- **Description:** Schema creation has no version table or migration framework.
  Connections use defaults without explicit busy timeout, integrity check,
  backup, WAL policy, or corruption recovery.
- **Risk:** A future schema change or interrupted filesystem can make history
  unusable and may block processing because initialization is on the critical
  path.
- **Recommended fix:** Add schema versioned migrations, bounded busy handling,
  startup integrity/recovery, periodic backup, and decouple nonessential
  learning-history failure from successful image delivery.
- **Estimated effort:** 3–5 developer days.

### PH-024 — Dependency and build inputs are not reproducible

- **Severity:** Medium
- **Description:** Requirements use broad lower bounds; OpenAI, PySide6,
  Pillow, NumPy, and PyInstaller can change without a source diff. The build
  uses whichever Python/architecture is present and ad-hoc signing only.
- **Risk:** API behavior, packaging contents, image output, or GUI behavior can
  change between builds. A previously passing release may no longer reproduce.
- **Recommended fix:** Maintain a reviewed lock/constraints file with hashes,
  record Python/macOS/architecture/tool versions, build in a clean controlled
  environment, and generate an artifact manifest/SBOM.
- **Estimated effort:** 2–3 developer days.

### PH-025 — Test coverage is unit-heavy and misses operational faults

- **Severity:** Medium
- **Description:** Current tests are strong for functional helpers and mocked
  happy/error paths but do not exercise an actual Qt event loop, worker fatal
  exceptions, shutdown, settings corruption, disk full, permissions, SQLite
  lock/corruption, API timeout/rate-limit policy, 500-file scans, or packaged
  artifact launch.
- **Risk:** The highest production risks can regress while all 55 tests pass.
- **Recommended fix:** Add fault-injection tests, Qt integration tests, a
  synthetic 500/1,000-file benchmark, crash/restart tests, and packaged smoke
  tests. Keep all API behavior mocked.
- **Estimated effort:** 5–8 developer days initially, then ongoing.

### PH-026 — Review/history updates are basename-wide

- **Severity:** Medium
- **Description:** Some label updates affect every unresolved record matching a
  filename; others update only the latest matching filename. They do not scope
  by batch, source root, mode, or fingerprint.
- **Risk:** Accepting one `kitchen.jpg` can relabel unrelated historical jobs,
  weakening learning data and audit accuracy.
- **Recommended fix:** Give each attempt a stable ID and update the exact
  reviewed attempt/output. Preserve explicit relationships across retry.
- **Estimated effort:** 2–3 developer days.

## Low findings

### PH-027 — Module header and some messages describe obsolete behavior

- **Severity:** Low
- **Description:** At the time of this audit, the source docstring said an
  obsolete prerelease version and described a
  Medium-only/JPEG-only workflow, and older skip folders. The missing-prompt
  message names a legacy file.
- **Risk:** Maintainers and support staff diagnose the wrong behavior.
- **Recommended fix:** Update comments/messages to current facts without
  changing runtime behavior or prompt content; add a version-consistency test.
- **Estimated effort:** Less than 1 day.

### PH-028 — Internal regression test runs for every production batch

- **Severity:** Low
- **Description:** A random-array verifier self-test executes synchronously at
  every `process_batch` start and prints to stdout, which is not visible in a
  windowed packaged app.
- **Risk:** Minor repeated work and confusing diagnostic placement; a failure
  is subject to PH-002.
- **Recommended fix:** Run self-checks once at startup or in build/test
  validation, report through structured logging, and fail preflight cleanly.
- **Estimated effort:** Less than 1 day.

### PH-029 — Cost reporting is explicitly approximate but looks authoritative

- **Severity:** Low
- **Description:** “Total API cost” and per-image cost are fallback estimates,
  not reconciled billing. Failed calls completed before a later local failure
  are approximated; retry attempts are not represented independently.
- **Risk:** Operators may treat estimates as financial records.
- **Recommended fix:** Label estimates consistently in UI/log schema, record
  each request attempt, and document that provider billing is authoritative.
- **Estimated effort:** 1 day.

### PH-030 — Build scripts contain duplicated version metadata

- **Severity:** Low
- **Description:** Application version is repeated in Python and shell plist
  assignments.
- **Risk:** UI, bundle, DMG, and release notes can disagree.
- **Recommended fix:** Read build metadata from one machine-readable source and
  fail the build on mismatch.
- **Estimated effort:** 1 day.

## Recommended hardening sequence

### Gate 1 — Data integrity and terminal-state safety

Resolve PH-001, PH-002, PH-005, PH-006, PH-009, PH-010, and PH-012. Add
fault-injection tests before any additional production-scale use.

### Gate 2 — API and resumability

Resolve PH-003, PH-004, PH-013, PH-014, and PH-015. Prove deterministic restart
at every state transition using mocked responses only.

### Gate 3 — Scale and operations

Resolve PH-007, PH-008, PH-019, PH-022, PH-023, PH-024, and PH-025. Establish
measured acceptance targets for 500- and 1,000-image scans/batches.

### Gate 4 — Security and maintainability

Resolve PH-011, PH-020, PH-021, PH-026 through PH-030. Re-run packaged
installation and clean-first-launch validation.

## Production-readiness exit criteria

The application should not be approved for thousands-image operation until:

1. every batch uses an immutable job context;
2. every worker path emits exactly one terminal result;
3. crash/restart state is durable and tested at every processing phase;
4. paid calls have bounded timeout, classified failure, and one retry policy;
5. disk, permissions, settings, resource, and database preflight failures occur
   before paid confirmation;
6. output, journal, CSV, and history reconcile after injected failures;
7. cancellation and window close have deterministic semantics;
8. 1,000-file scan and long-running mocked batch benchmarks meet defined UI and
   memory limits;
9. multiple instances cannot process the same job;
10. the packaged artifact passes clean-install, missing/corrupt-resource,
    settings-corruption, and no-key Demo tests with no paid API calls.
