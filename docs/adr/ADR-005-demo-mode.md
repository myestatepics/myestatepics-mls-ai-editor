# ADR-005: Demo Mode

**Status:** Accepted

## Problem

GUI, routing, review, and training workflows need repeatable validation without
API credentials, network access, or charges.

## Decision

Provide a visible Demo Mode that never creates an OpenAI client. It copies the
source as mock output, simulates pass/review/error outcomes, records DEMO
history/log rows, reports zero cost, and uses the active runtime's isolated
`Demo` folders. Simulated errors are reports rather than processed images.

## Alternatives

- Mock only in tests: rejected because operators need to exercise the real GUI.
- Use a low-cost live request: rejected because it still requires credentials,
  network access, and spend.
- Mix demo and production output: rejected because it corrupts eligibility and
  operational records.

## Consequences

Demo validates workflow, not edit quality or API compatibility. Demo labels and
storage must remain visibly isolated, and production behavior must not branch
through simulated results.
