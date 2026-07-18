# ADR-003: Architectural Geometry Lock

**Status:** Accepted

## Problem

Generative image correction can invent or reinterpret windows, openings,
mirrors, reflections, fixtures, and other architectural features, creating an
inaccurate property representation.

## Decision

Architectural fidelity has priority over exposure, window enhancement, and
aesthetics. The prompt prohibits creating, removing, resizing, moving, or
reinterpreting architectural features and requires ambiguity to resolve to the
source. The GUI displays a checked Conservative Architecture Lock.

## Alternatives

- Rely on a general realism instruction: rejected as insufficiently explicit.
- Permit reconstruction with human review: rejected for the current production
  risk tolerance.
- Use post-generation computer vision alone: rejected because the current
  verifier cannot prove geometric identity.

## Consequences

Some difficult images may receive less aggressive correction. Human review and
regression coverage remain necessary because prompt safeguards are not a formal
guarantee.
