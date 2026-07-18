# ADR-004: Prompt Externalization

**Status:** Accepted

## Problem

An embedded prompt is difficult to audit, compare with the legacy baseline,
test independently, and package as a controlled resource.

## Decision

Keep the production prompt in `prompts/mls_production.txt` and load it at
runtime through centralized resource discovery. Maintain `PROMPT_VERSION`
separately from `PROGRAM_VERSION`.

Runtime adaptive guidance may be appended for unusually dark inputs, but it
may not replace or weaken the external baseline.

## Alternatives

- Embed a Python string: rejected because it creates duplicate or obscured
  sources of truth.
- Make the prompt freely editable in the GUI: rejected because production
  behavior must remain controlled.
- Fetch a remote prompt: rejected because it weakens reproducibility and
  offline startup.

## Consequences

Packaging must include the prompt and tests must verify it. Prompt edits require
review, regression evidence, and an appropriate prompt-version change.
