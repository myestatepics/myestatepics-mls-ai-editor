# V4.0.3 production regression manifest

This manifest records categories for controlled offline inspection and the
minimum paid validation set. It does not submit images or store customer
photographs in the repository.

## Historical production controls

| Category | Representative source set | Release condition |
| --- | --- | --- |
| Window-dominant interior | Chaterhouse Canton: 1, 2, 3, 6, 9 | Exterior is clearly readable; visible sky is light natural blue where applicable; edges are clean. PARTIAL is failure. |
| Dark room with bright window | Chaterhouse Canton: 1, 9 | Interior remains bright with depth while the real exterior is clear. |
| Large neutral surfaces | Chaterhouse Canton: 1, 9, 21, 25, 26, 27, 28 | No artificial bright/dark blobs, bands, or recoloring. |
| Dark cabinetry | Chaterhouse Canton: 15 | Cabinetry remains dark and detailed; windows remain recovered. |
| Bathroom/mirror/glass | Historical bathroom/mirror control | Smart Cost must not confuse mirrors, shower glass, fixtures, or reflections with exterior windows. |
| Hardwood | Historical sunlit-hardwood control | Color, grain, highlights, and reflections remain faithful. |
| Known-good control | Representative V4.0.2 image with accepted output | The V4.0.3 correction must not worsen an already accepted image. |

## Required local checks

- Run the complete mocked unit suite.
- Verify Smart Cost route selection against synthetic no-window, significant
  window, tiny incidental opening, closet, hallway, and basement fixtures.
- Verify a request constructed with mocks still contains one direct
  `gpt-image-2` Images Edit request and no Auto quality.
- Compare the source prompt SHA-256 with the bundled prompt SHA-256.

## Minimum paid validation, only after approval

Run **three** individually approved images, not a batch:

1. one dark interior with a large genuinely exterior-facing window;
2. one Chaterhouse neutral-surface failure control; and
3. one bathroom/mirror or small/incidental-window control.

For each, inspect window pull, sky, edge quality, interior exposure, material
fidelity, and surface uniformity. A partial window result is a release failure.
