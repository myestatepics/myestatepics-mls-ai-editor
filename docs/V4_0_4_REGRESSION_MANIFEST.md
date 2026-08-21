# V4.0.4 production regression manifest

This manifest keeps V4.0.4 validation targeted. It records offline checks and
the small, separately approved real-image acceptance set; it does not submit
images or store customer photographs in the repository.

## Mandatory production controls

| Category | Representative source set | Acceptance condition |
| --- | --- | --- |
| Kitchen window recovery gate | Chaterhouse Canton: 21 | Window/exterior is immediately readable. A white, washed-out, hazy, faint, or partial result fails. |
| Window-dominant interiors | Chaterhouse Canton: 1, 6, 8, 10, 12, 17 | Real exterior remains clear; visible sky is natural light blue only where applicable; window edges are clean. |
| Positive window control | Chaterhouse Canton: 6 | Keep the accepted combination of clear trees, bright interior, and natural photographic appearance. |
| Reflection cleanup | Chaterhouse Canton: 10, 13 | Photographer, camera, tripod, or phone reflection is removed without changing mirror or room geometry. |
| Material-color controls | Chaterhouse Canton: 4, 5, 21 | Hardwood, cabinetry, and wood surfaces retain their photographed color without orange/yellow warming. |
| Surface uniformity | Chaterhouse Canton: 1, 9, 21, 25, 26, 27, 28 | Artificial blobs and bands are controlled without flattening legitimate directional light or room depth. |

## Required offline checks

- Run the complete mocked unit suite and compile check.
- Verify Medium is the normal production default; Low, Medium, and High remain
  available, High is manual-only, and OpenAI Auto is unavailable.
- Verify mocked normal processing constructs exactly one direct `gpt-image-2`
  Images Edit request with no analysis or secondary image request.
- Compare the source prompt SHA-256 with the bundled application prompt.

## Minimum paid validation, only after explicit approval

Run three individually approved images, never a batch:

1. Chaterhouse 21, the mandatory kitchen-window recovery gate;
2. one dark interior with a large genuinely exterior-facing window; and
3. one bathroom/mirror control with photography-equipment reflection.

For each, inspect window pull, sky, edge quality, bright interior exposure,
material fidelity, reflection cleanup, and surface uniformity. A white, hazy,
or barely readable exterior is a release failure.
