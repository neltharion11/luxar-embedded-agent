"""Compatibility shims for the legacy prompt stack.

The v0.2.0 runtime keeps harness as a behavior-governing system and moves
operational guidance into skills. These constants remain for compatibility,
but they should stay thin and must not be used as the primary control plane
for new work.
"""

ANTI_RATIONALIZATION = """
[v0.2.0 compatibility note]
- Do not fabricate tool results or hardware state.
- Prefer skill / lesson updates over adding new prompt-side heuristics.
- Narrow changes beat broad speculative rewrites.
""".strip()

VERIFICATION_GATE_APP = """
[App verification]
- Keep application logic narrow and compilable.
- Preserve the declared public interface.
- Do not invent hardware bindings that are not evidenced by plan, skill, or runtime context.
""".strip()

VERIFICATION_GATE_DRIVER = """
[Driver verification]
- Keep the driver transport-injected and MCU-agnostic.
- Preserve header/source parity and deterministic return contracts.
- Do not introduce platform-global handles or unverifiable assumptions.
""".strip()

ROOT_CAUSE_ANALYSIS_GATE = """
[Root-cause guidance]
- Prefer the narrowest fix that resolves evidenced failures.
- If repeated failures suggest architectural mismatch, capture a lesson or recovery skill candidate.
""".strip()

SELF_REVIEW_GATE = """
[Self-review]
- Ensure the output is internally consistent.
- Surface unknowns instead of inventing data.
- Keep the result aligned with the selected skill path and runtime constraints.
""".strip()

UART_DIAGNOSTIC_REQUIREMENT = """
[Diagnostic output]
- Prefer explicit runtime diagnostics when the task or harness requires observability.
- Route diagnostics through project abstractions instead of hardcoded peripheral globals.
""".strip()
