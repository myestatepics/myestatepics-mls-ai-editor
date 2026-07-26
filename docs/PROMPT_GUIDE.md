# Production Prompt Guide

## Source of truth

The executable production prompt is:

```text
prompts/mls_production.txt
```

The application loads that file at runtime in source and packaged modes. This
guide explains the current prompt; it does not replace it. The prompt badge is
`Prompt v2.0 RC1`.

## Prompt structure

The prompt is layered so preservation rules override aesthetic improvement:

1. photo correction only;
2. material color lock;
3. neutral white balance;
4. balanced exposure and depth;
5. real window-detail recovery;
6. blue only for genuine open sky;
7. architectural fidelity;
8. highest-priority structural lock;
9. confirmed existing-window window pull;
10. existing-window-only sky replacement;
11. mirror and reflection protection; and
12. preservation of everything else.

The original photograph is always the source of truth. Ambiguous areas must
remain unchanged.

## Structural lock

The highest-priority rule prohibits creating, inserting, removing, enlarging,
shrinking, relocating, or modifying windows, doors, skylights, wall openings,
arches, or other structural openings.

Walls must remain walls. If the source has no window, the model must not invent
one. Only windows already visible in the original may receive a window pull or
sky correction.

## Existing-window-only rule

A region qualifies as an exterior window only when the source shows all three:

- a physical window frame;
- an exterior wall boundary; and
- a visible outside scene.

Mirrors, shower glass, reflections, cabinet glass, glossy tile, polished stone,
TV screens, door gaps, open interior doors, highlights, chrome, and appliances
must never be interpreted as windows.

## MLS window pull

For a confirmed existing window, the prompt asks for a strong, natural
MLS-quality pull and recovery of the real exterior whenever possible. It does
not authorize replacing the exterior view.

Real neighboring houses, roofs, trees, landscaping, decks, fences, roads,
driveways, vehicles, and every other outdoor object must remain present and
unchanged.

## Natural blue sky

When the sky inside an existing confirmed window is white, gray, blown out, or
unattractive, only that sky portion may become a clean, natural light-blue MLS
sky. Blue must not spread onto houses, trees, roofs, decks, fences,
landscaping, frames, curtains, blinds, or any other non-sky surface.

If the model cannot distinguish sky from another surface confidently, the
source region must remain unchanged.

## Mirrors and reflections

The prompt prohibits fake windows, outdoor scenery, and reflections. Mirrors
must continue to reflect the original room. Reflections, shower glass, cabinet
glass, glossy surfaces, screens, and appliances must not receive synthetic
outdoor content or blue-sky treatment.

## Material and architectural preservation

The prompt locks room geometry, camera position, perspective, material
identity, wall and floor colors, cabinets, countertops, trim, doors, windows,
fixtures, and furniture placement.

Neutral white-balance and exposure corrections may improve presentation, but
they must not redesign the property, flatten materials, recolor finishes, move
objects, or reconstruct architecture.

## Regression philosophy

Prompt refinements must be driven by verified production failures, one issue at
a time. A change should be narrow, explain why it addresses the observed
failure, preserve the successful exposure/color baseline, and be tested first
with mocks and then—only after explicit approval—with one paid validation
image.

Required semantic comparisons include:

- a room with no window;
- a real window with blown sky and retained exterior objects;
- mirrors and mirror reflections;
- shower and cabinet glass;
- blue-bleed boundaries around trees, roofs, houses, frames, and curtains; and
- material, geometry, text, fixtures, and furniture preservation.

## Known generative-model limitations

Prompt rules reduce risk but do not enforce pixel identity. A generative image
model can still redraw countertop veining, text, plants, fabrics, fixtures,
reflections, or other fine details. It may also misclassify ambiguous bright
regions despite explicit safeguards.

The deterministic verifier measures image statistics; it cannot prove semantic
property fidelity. Every production result therefore requires visual review
against its source, with particular attention to architecture, mirrors,
reflections, materials, text, and exterior objects.
