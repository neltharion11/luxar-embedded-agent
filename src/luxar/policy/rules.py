"""Immutable high-level rules for the vNext runtime."""

RULES = [
    "Never fabricate tool results, hardware state, or validation evidence.",
    "Harness is the runtime behavior system; operational workflows belong in skills, not prompt gates.",
    "Draft artifacts may be auto-created or patched; validated promotion requires evidence.",
    "Repeated failures should prefer recovery skills and lesson updates over blind retries.",
    "Executable skills must pass a sandboxed dry-run before promotion.",
    "Lessons must adhere to a strict schema (topic, symptom, hypothesis, evidence, resolution, outcome) to be recorded or promoted.",
]
