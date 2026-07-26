# Direct Production Test Plan

## Test policy

Offline tests run first and must not create an OpenAI client request. Paid
validation requires explicit approval, one stated objective, and a cost
estimate before the call. A failed semantic case stops further paid testing.

Record the branch, commit, Python version, dependency versions, test inputs,
output paths, timing, OpenAI dashboard charge, and reviewer for every release
candidate.

## Automated offline tests

Run:

```bash
python3 -m py_compile myestatepics_ai_editor.py
pytest -q
git diff --check
```

The suite must cover at least:

- mocked direct `client.images.edit` calls;
- absence of Responses API use;
- Low and Medium quality mapping;
- source and packaged API-key paths;
- Demo Mode with no client/API call;
- selected-file and empty-selection behavior;
- output-existence skip and reprocessing rules;
- Demo/production isolation;
- PNG decode, JPEG output, filename, EXIF, and size behavior;
- retry classification;
- cooperative cancellation;
- CSV and SQLite records; and
- production prompt safeguards.

## Startup smoke test

Pass criteria:

- packaged app opens by double-click;
- main window, version, and prompt badges render;
- bundled prompt loads;
- Direct Application Support path is used;
- no API call occurs at startup;
- missing-key state still allows Demo Mode;
- existing key is recognized without being displayed;
- Incoming and Completed folders can be selected; and
- supported files are automatically checked.

## Low mode

Use a mocked API for routine regression. Confirm:

- GUI shows Low;
- estimate updates;
- `client.images.edit` receives `quality="low"`;
- model is `gpt-image-2`;
- endpoint log says `/v1/images/edits`;
- exactly one call occurs after a successful response; and
- summary and per-image log show Low.

## Medium mode

Use a mocked API for routine regression. Confirm:

- GUI selection changes to Medium;
- estimate changes immediately;
- `client.images.edit` receives `quality="medium"`;
- exactly one call occurs after a successful response; and
- summary and per-image log show Medium.

## Semantic production cases

These tests require source/output visual comparison. Automated statistical
verification alone is insufficient.

### No-window interior

Input: an interior containing a solid wall, bright wall area, mirrors, or
doorways but no exterior window.

Pass:

- no fake window, skylight, opening, exterior view, or blue sky;
- wall geometry and material remain unchanged; and
- mirrors and doorways retain their original meaning.

### Existing window and blue sky

Input: a real framed exterior window or sliding glass door with a blown, white,
gray, or unattractive sky.

Pass:

- strong, natural MLS window pull;
- only the genuine sky becomes natural light blue;
- real exterior objects remain; and
- frames, curtains, blinds, and glass boundaries remain aligned.

### Mirror and reflection

Input: large mirrors, reflected windows, polished surfaces, or shower glass.

Pass:

- mirrors retain the original room reflection;
- no fake window or outdoor scene appears;
- no sky is added to reflected, glossy, or transparent interior surfaces; and
- reflected object placement remains credible and consistent with the source.

### Exterior preservation

Input: windows showing houses, roofs, trees, decks, fences, landscaping,
roads, driveways, or vehicles.

Pass:

- each visible exterior object remains present, in place, and recognizable;
- no object is substituted, removed, or invented; and
- the complete exterior view is not replaced.

### Fake-window detection

Review every solid wall, mirror boundary, shower enclosure, cabinet glass,
screen, appliance, bright highlight, chrome surface, and open interior door.

Fail if any becomes a window, opening, outdoor view, or architectural feature.

### Blue-bleed detection

Inspect sky boundaries at high zoom.

Fail if blue appears on houses, trees, roofs, decks, fences, landscaping,
window frames, curtains, blinds, reflections, glass as a whole, or interior
materials.

## Actual dashboard cost validation

After explicit approval, process exactly one named production image with one
stated objective and estimated maximum cost. Record:

- quality;
- request start/end time;
- application estimate;
- number of HTTP attempts;
- OpenAI dashboard charge after billing settles; and
- whether any retry occurred.

Pass only when the dashboard confirms the approved commercial target. Never
infer exact cost solely from token fields or the application estimate.

## Timing validation

For the same approved single image, measure wall-clock processing time from
request start through saved output. Distinguish API time from local decode,
verification, and export time where logs permit.

The current commercial target is less than 30 seconds per image on average.
One image is an initial gate, not proof of sustained batch performance.

## Batch stability validation

Run only after single-image cost, timing, and visual quality are approved.
Progress in controlled stages before 500+ images.

Validate:

- sequential one-request-per-image behavior;
- no unexplained duplicate billing;
- 500+ file scanning and selection responsiveness;
- cancellation during an active request;
- preservation of completed outputs after cancellation;
- remaining queue stops;
- transient retry logging;
- network interruption recovery;
- corrupt/unsupported input handling;
- disk-space and permission failures;
- exact filenames and output routing;
- CSV/SQLite completeness;
- memory growth and GUI responsiveness; and
- restart/reprocessing based on current outputs, not history.

## Release gate

A release passes only when:

- all offline tests pass;
- startup smoke test passes without API activity;
- Low and Medium mapping is verified;
- representative semantic cases pass human review;
- actual dashboard cost and measured timing meet the approved target;
- batch stability is acceptable;
- prompt in the app matches source exactly; and
- artifact metadata, signature, checksum, and secret exclusion are verified.
