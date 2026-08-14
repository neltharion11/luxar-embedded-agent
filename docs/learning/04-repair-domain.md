# Repair Domain Boundary

`ProjectFile` is a validated pre-repair snapshot, `FileReplacement` is a complete-file proposal, and `RepairPlan` groups a nonempty diagnosis with unique replacement targets. The model can only propose these Domain values; it receives no filesystem handle.

Paths are normalized to forward-slash project-relative form and reject empty, absolute, drive-qualified, current-directory-only, and parent-traversal values. This is the first security layer. A real Workspace Adapter must still resolve every destination beneath the configured project root immediately before writing.

Focused repair-domain suite: 13 passed.
