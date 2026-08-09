# Quality Standards

## Definition: MyEstatePics MLS Ready

An image is MyEstatePics MLS Ready when it is a truthful, natural-looking
correction of the source, technically suitable for delivery, and free of
material generative defects. Automated checks support, but do not replace,
operator review.

## Mandatory visual criteria

- No invented, removed, moved, resized, or reinterpreted architecture.
- No hallucinated windows, doors, openings, rooms, rails, stairs, cabinets, or
  fixtures.
- Mirror content and reflections remain physically consistent with the source.
- No duplicated, removed, replaced, or reshaped objects.
- No automatic decluttering; temporary and permanent objects remain as
  photographed in the current release.
- Materials retain their photographed identity and relative color.
- White balance is neutral without making genuine warm light or materials cold.
- Exposure is MLS-bright while retaining black depth and dimensionality.
- Window recovery contains only detail supported by the source.
- Blue appears only in genuine visible open sky.
- No halos, cyan haze, clipped ceilings, glowing walls, flat HDR tonality,
  painterly texture, plastic surfaces, watermarks, borders, or AI artifacts.

## Measurable automated checks

Current verifier thresholds are implementation safeguards:

| Measure | Review/failure behavior |
|---|---|
| normalized sharpness ratio | FAIL below 0.45; REVIEW below 0.60 |
| global brightness shift | REVIEW above the adaptive limit |
| adaptive brightness limit | effective limit 0.45 below source mean 0.25; 0.38 from 0.25 to below 0.35; 0.34 at source mean 0.35 or above |
| clipped-highlight fraction | REVIEW above 0.24 |
| crushed-shadow fraction | REVIEW above 0.28 |
| global chromaticity shift | REVIEW above 0.055 |
| JPEG export | quality 100, 4:4:4, no file-size ceiling or size-based review routing |

Chromaticity is a review signal, not proof of a particular material error.

## File and metadata criteria

- exact source filename is preserved
- original remains untouched
- output is JPEG data
- pixel orientation is upright and EXIF Orientation equals 1
- safe EXIF and ICC data are retained when supported
- dimensions remain valid and nonzero
- writes complete atomically

## Routing criteria

Completed is the normal destination for a successful edit. NeedsReview is for
an actual verifier, moderation, decode, dimension, corruption, or export
concern and must include a reason. Exceptions route to Error with a report.

The current implementation routes verifier `FAIL` to NeedsReview. A verifier
`REVIEW` advisory is logged but, by itself, does not redirect an otherwise
valid result from Completed. Invalid dimensions and the hard size limit also
route to NeedsReview. Runtime exceptions create an Error text report rather
than a processed image. A Completed destination therefore does not eliminate
the need for human architectural and visual review.

## Human acceptance

Before delivery, compare original and output at fit-to-screen and 100%:

1. inspect walls, openings, mirrors, windows, fixtures, and built-ins
2. compare material colors and local warm light
3. inspect window edges and exterior content
4. inspect fine detail, noise, halos, and compression
5. confirm filename and destination

Every item is binary: pass only when the output can be matched to the source
without uncertainty. Record a specific failure reason for any false or
uncertain item and route it to NeedsReview rather than accepting the image.
