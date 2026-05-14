"""Shared verification gates, checklists, and anti-rationalization tables
injected into all LLM prompts to enforce engineering discipline."""

ANTI_RATIONALIZATION = """
[DO NOT RATIONALIZE — these thoughts are traps]
| If you think...                          | Reality...                                            |
|------------------------------------------|-------------------------------------------------------|
| "This is simple, skip the checklist"     | Simple code has bugs too. Verify before claiming.     |
| "I'll fix all issues at once"            | Batch fixes hide regressions. Fix one at a time.      |
| "Just one more fix" (3+ attempts)        | 3+ fix failures = the architecture may be wrong. STOP.|
| "I'm confident it compiles"              | Confidence ≠ evidence. Run the compiler check.        |
| "The LLM already reviewed this"          | LLM review is fallible. Re-verify after changes.      |
| "I'll add a small improvement while here"| NO. Fix only the reported issue. No scope creep.      |
""".strip()

VERIFICATION_GATE_APP = """
[VERIFICATION GATE — BEFORE returning code, verify ALL of the following:]
1. `app_main_init(void)` declared in header AND implemented in source
2. `app_main_loop(void)` declared in header AND implemented in source
3. No hardcoded HAL handles (hspi1, hi2c1, huart1, huart2, htim3, etc.) — use the luxar_hardware layer (luxar_uart_write, luxar_delay_ms, etc.) or mark with TODO
4. No `malloc`, `free`, or unsolicited `printf`
5. All exported functions have `/** ... */` Doxygen comments
6. When a pin/port is unknown, mark it with `/* TODO(luxar): configure XXX pin */`
7. Code is syntactically valid C11 — check braces, semicolons, includes
8. Header has `#ifndef`/`#define`/`#endif` include guards
If any check fails, fix the code BEFORE returning it.
""".strip()

VERIFICATION_GATE_DRIVER = """
[VERIFICATION GATE — BEFORE returning code, verify ALL of the following:]
1. Header declares the driver's init/read/write/deinit functions with Doxygen
2. Source implements ALL functions declared in the header
3. No direct HAL handle references (hspi\\d+, hi2c\\d+, huart\\d+, htim\\d+)
4. All HAL operations go through injected function pointers or interface struct
5. No `malloc` / `free` / `printf`
6. All pointer parameters have NULL checks at function entry
7. Return type is `int` (0=success, negative=error code) for all public functions
8. Header has `#ifndef`/`#define`/`#endif` include guards
If any check fails, fix the code BEFORE returning it.
""".strip()

ROOT_CAUSE_ANALYSIS_GATE = """
[ROOT CAUSE ANALYSIS — BEFORE proposing any fix:]
1. Read every issue in the review report completely — do not skip warnings
2. For each issue, classify: is it a SYMPTOM (surface pattern) or ROOT CAUSE (why it exists)?
3. Trace backward: where does the problematic value/pattern originate in the call chain?
4. Form a SINGLE hypothesis per issue: "I think X is wrong because Y"
5. Fix the root cause, not just the symptom
6. Do NOT add new features, refactor unrelated code, or "improve" beyond the fix
7. Keep function names, file structure, and existing code style
[If 3+ separate fix attempts fail — STOP. The architecture may be wrong.]
""".strip()

SELF_REVIEW_GATE = """
[SELF-REVIEW — BEFORE returning output, verify your own work:]
1. Placeholder scan: Are there any "TODO" or "TBD" sections that need resolution?
2. Consistency: Do the declarations match the implementations exactly?
3. Scope check: Is this output complete for the stated task?
4. Ambiguity: Could any statement be reasonably misinterpreted?
5. Constraint check: Are all explicit constraints satisfied?
""".strip()

UART_DIAGNOSTIC_REQUIREMENT = """
[DIAGNOSTIC OUTPUT — every program MUST include UART debug output:]
1. Print a boot banner with project name and MCU info on startup
2. Print each initialization step with [OK] or [FAIL] status
   Example: "SYSCLK: 72000000 Hz [OK]"
   Example: "GPIOB: PB0 output [OK]"
   Example: "SysTick: 1ms tick [OK]"
3. In the main loop, print periodic status at meaningful intervals
4. Use the LUXAR hardware layer for UART output:
   - Call luxar_uart_write("your debug text") — this is the project's hardware abstraction
   - luxar_uart_write() already handles UART init, baud rate (115200 8N1), and TX via USART2 (PA2/PA3)
   - Do NOT reference huart2, HAL_UART_Transmit, or any HAL handles directly in app code
5. If a hardware check fails (e.g., clock not ready, SysTick not ticking),
   print "[FAIL] <reason>" and either halt or retry
6. This requirement applies to app_main.c. Driver code may omit UART.
""".strip()
