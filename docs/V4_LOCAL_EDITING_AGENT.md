# V4.0 Local Editing Agent

V4.0 is built from the frozen V3.1.1 commit `8321ed302e74acfca4079c8c948cd43310f879b0`.
It retains the Direct Images Edit production engine and adds a local rule-memory
layer only.

## Boundary

`editing_agent.py` has no OpenAI client, no network dependency, no embeddings,
and no model invocation. For each source image it combines the authoritative
`prompts/mls_production.txt` with concise, relevant rules that are both
`APPROVED` and enabled. The master prompt is placed first and wins over a rule
that attempts to weaken factual, architectural, geometry, or cost safeguards.

The resulting instruction is submitted by the existing one-call
`client.images.edit(model="gpt-image-2", ...)` path. No second request,
analysis call, quality escalation, or retry beyond the existing genuine
transient-failure retry policy is introduced.

## Local data

Mutable data is intentionally outside the app bundle and Git repository:

```text
~/Library/Application Support/MyEstatePics AI Editor - Direct/
├── learned_rules.json
└── feedback_history.jsonl
```

The first rule-memory access seeds established approved contextual lessons.
`learned_rules.json` contains the schema version, rule ID, categories,
description, instruction, status, enabled state, timestamps, application count,
notes, and optional source reference. `feedback_history.jsonl` records seed,
approve, disable, re-enable, delete, and applied events.

## Selection and failure handling

Direct filename signals are used only when explicit (for example `window`,
`sheer`, `mirror`, or room-name terms). Uncertain images receive only global
rules. Missing, unreadable, corrupt, or individually invalid memory data is
logged and safely falls back to the master prompt alone; it cannot stop image
editing.

The Editing Memory dialog displays all rules and permits approving a proposed
rule, disabling/re-enabling an approved rule, and deleting a proposed rule.
It does not auto-promote lessons or modify source-controlled prompt content.
