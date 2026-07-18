# Prompt Specification

## Source of truth

The production prompt is `prompts/mls_production.txt`, identified in the UI as
Prompt v2.0 RC1. This document explains its intent; it does not replace or
duplicate the prompt as an executable source.

## Design philosophy

The prompt treats the original photograph as authoritative. The desired result
is a restrained photographic correction comparable to careful Lightroom work,
not a redesign, reconstruction, staging operation, or synthetic rendering.

## Priority model

The prompt orders preservation before correction:

1. material identity and geometry
2. neutral white balance
3. naturally bright exposure with depth
4. recovery of real exterior window detail
5. subtle blue only where open sky genuinely exists

This ordering prevents aesthetic improvement from overriding property truth.

## Architectural Geometry Lock

The implementation names the safeguard **Architectural Fidelity** in the
external prompt and presents a checked, non-editable **Conservative
Architecture Lock** in the GUI. Together they express a geometry lock:
windows, doors, walls, openings, railings, stairs, cabinets, fireplaces, trim,
mirrors, and built-ins may not be created, removed, resized, moved, or
reinterpreted.

It exists because generative correction can otherwise infer plausible but
false structure from clipped highlights, blank walls, or ambiguous edges.

## Material and color lock

Photographed hues and relative colors must remain stable. Neutral correction
must remove a global cast without whitening beige, cooling warm wood, enriching
furnishings, or replacing one cast with another. This protects listing
accuracy and avoids a generic rendered appearance.

## Window handling

The prompt permits recovery only of exterior detail already present. Window
frames and edges must remain crisp without halos. Blue is allowed only on
genuine visible open-sky pixels; glass, reflections, structures, vegetation,
and interior surfaces must not receive a blue wash.

This section exists because windows combine clipped highlights, mixed color
temperature, reflections, and ambiguous exterior content.

## Object removal and decluttering

Current production behavior prohibits adding, removing, replacing, moving, or
reshaping anything. It does not authorize automatic decluttering, even for
temporary objects. This strict position is intentional: the current release
prioritizes factual preservation over cleanup convenience.

Any future object-removal feature would require a separate prompt, controls,
review criteria, and regression set. It must not be inferred from this prompt.

## Mirror and reflection handling

Mirrors and reflections must remain physically accurate. A reflected bright
region cannot be converted into a window, and reflected architecture cannot be
reconstructed. This safeguard addresses a recurring generative failure mode in
which reflection boundaries are interpreted as openings.

## Failure prevention

The prompt explicitly prevents:

- invented architecture, scenery, objects, text, logos, and watermarks
- altered furniture, fixtures, geometry, reflections, or exterior structures
- clipped, glowing, flat, hazy, haloed, painterly, plastic, or HDR-heavy output
- color contamination of materials or non-sky areas

Ambiguity resolves to preservation.

## Regression philosophy

Prompt changes require evidence from a representative regression library,
especially mirrors, bright blank walls, window reflections, mixed lighting,
dark cabinetry, warm materials, and difficult exterior views. A safeguard
should be appended or refined narrowly; successful exposure, white balance,
color, and MLS appearance should not be rewritten casually.
