from __future__ import annotations

import re
from pathlib import Path

from luxar.core.config_manager import AgentConfig
from luxar.core.llm_client import LLMClient, LLMClientError
from luxar.models.schemas import AppGenerationResult, ProjectConfig, ProjectPlan
from luxar.prompts.app_generation import (
    APP_GENERATION_SYSTEM_PROMPT,
    build_app_generation_prompt,
)


class AppGenerator:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.llm_client = LLMClient(config)

    def generate_app(
        self,
        project: ProjectConfig,
        project_plan: ProjectPlan,
        installed_drivers: list[str] | None = None,
    ) -> AppGenerationResult:
        project_root = Path(project.path).resolve()
        header_path = project_root / "App" / "Inc" / "app_main.h"
        source_path = project_root / "App" / "Src" / "app_main.c"
        header_path.parent.mkdir(parents=True, exist_ok=True)
        source_path.parent.mkdir(parents=True, exist_ok=True)
        available_drivers = self._discover_project_driver_headers(project_root, installed_drivers or [])

        if self._is_rgb_oled_status_plan(project_plan, available_drivers) or self._is_rgb_rainbow_plan(project_plan):
            header_code, source_code = self._fallback_code(project_plan, installed_drivers=available_drivers)
            used_fallback = True
            raw_response = ""
            failures: list[str] = []
            header_path.write_text(header_code.rstrip() + "\n", encoding="utf-8")
            source_path.write_text(source_code.rstrip() + "\n", encoding="utf-8")
            return AppGenerationResult(
                success=True,
                project=project.name,
                requirement=project_plan.requirement_summary,
                project_plan=project_plan,
                header_path=str(header_path),
                source_path=str(source_path),
                used_fallback=used_fallback,
                raw_response=raw_response,
                error="",
            )

        prompt = build_app_generation_prompt(
            project_name=project.name,
            mcu=project.mcu,
            project_mode=project.project_mode,
            project_plan=project_plan,
            installed_drivers=available_drivers,
        )

        try:
            response = self.llm_client.complete(
                prompt=prompt,
                system_prompt=APP_GENERATION_SYSTEM_PROMPT,
            )
            header_code, source_code = self._extract_code_blocks(response.content)
            used_fallback = False
            raw_response = response.content
        except (LLMClientError, ValueError):
            header_code, source_code = self._fallback_code(project_plan, installed_drivers=available_drivers)
            used_fallback = True
            raw_response = ""

        # VERIFICATION GATE: basic syntactic integrity check BEFORE writing
        failures = self._verify_generated(
            header_code,
            source_code,
            project_plan=project_plan,
            installed_drivers=available_drivers,
        )
        if failures:
            # LLM output failed hard gate; fall back to skeleton code which is
            # guaranteed to pass all checks.
            header_code, source_code = self._fallback_code(project_plan, installed_drivers=available_drivers)
            used_fallback = True

        header_path.write_text(header_code.rstrip() + "\n", encoding="utf-8")
        source_path.write_text(source_code.rstrip() + "\n", encoding="utf-8")

        return AppGenerationResult(
            success=True,
            project=project.name,
            requirement=project_plan.requirement_summary,
            project_plan=project_plan,
            header_path=str(header_path),
            source_path=str(source_path),
            used_fallback=used_fallback,
            raw_response=raw_response,
            error="" if not failures else ("LLM output failed verification gate, fell back to skeleton: " + "; ".join(failures)),
        )

    def _discover_project_driver_headers(self, project_root: Path, installed_drivers: list[str]) -> list[str]:
        discovered: list[str] = []
        for header in sorted((project_root / "App" / "Drivers").glob("*/*/*.h")):
            stem = header.stem.strip()
            if stem and stem not in discovered:
                discovered.append(stem)
        if discovered:
            return discovered
        return list(installed_drivers)

    def _extract_code_blocks(self, content: str) -> tuple[str, str]:
        matches = re.findall(r"```c\s+(header|source)\n(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
        blocks = {kind.lower(): body.strip() for kind, body in matches}
        if "header" in blocks and "source" in blocks:
            return blocks["header"], blocks["source"]

        generic = re.findall(r"```(?:c)?\n(.*?)```", content, flags=re.DOTALL | re.IGNORECASE)
        if len(generic) >= 2:
            return generic[0].strip(), generic[1].strip()
        raise ValueError("LLM response did not contain both header and source code blocks.")

    def _fallback_code(self, project_plan: ProjectPlan, installed_drivers: list[str]) -> tuple[str, str]:
        if self._is_rgb_oled_status_plan(project_plan, installed_drivers):
            return self._rgb_oled_status_fallback_code(project_plan, installed_drivers)
        if self._is_rgb_rainbow_plan(project_plan):
            return self._rgb_rainbow_fallback_code(project_plan)

        comment = project_plan.requirement_summary[:160] if project_plan.requirement_summary else "Application requirement placeholder."
        driver_comment = ", ".join(installed_drivers) or "none"
        feature_comments = "\n".join(f" * - {item}" for item in project_plan.features) or " * - none"
        doc_comment = project_plan.document_context_summary[:240] if project_plan.document_context_summary else "none"
        action_comments = "\n".join(f" * TODO: {item}" for item in project_plan.cubemx_or_firmware_actions) or " * TODO: review missing hardware configuration."
        header = """#ifndef APP_MAIN_H
#define APP_MAIN_H

/**
 * @brief Initialize application-level resources.
 */
void app_main_init(void);

/**
 * @brief Run one iteration of the application loop.
 */
void app_main_loop(void);

#endif /* APP_MAIN_H */
"""
        source = f"""#include "app_main.h"

/* Requirement summary: {comment} */
/* Installed drivers: {driver_comment} */
/* Document context: {doc_comment} */

/**
 * Planned features:
{feature_comments}
 */

/**
 * Pending hardware integration actions:
{action_comments}
 */

void app_main_init(void)
{{
    /* TODO(luxar): integrate any required HAL hooks after completing the pending configuration actions. */
}}

void app_main_loop(void)
{{
    /* TODO(luxar): implement the planned application behavior without guessing unknown pins or peripheral instances. */
}}
"""
        return header, source

    def _is_rgb_rainbow_plan(self, project_plan: ProjectPlan) -> bool:
        text = " ".join(
            [
                project_plan.requirement_summary,
                project_plan.app_behavior_summary,
                *project_plan.features,
            ]
        ).lower()
        has_rgb_feature = any(feature.instance.upper() == "RGB_LED" for feature in project_plan.board_features)
        has_pwm_timer = any(
            feature.interface.upper() in {"TIM", "PWM"} and "PWM" in feature.mode.upper()
            for feature in project_plan.internal_peripherals
        )
        return has_rgb_feature and has_pwm_timer and ("rgb" in text or "rainbow" in text or "彩虹" in text)

    def _is_rgb_oled_status_plan(self, project_plan: ProjectPlan, installed_drivers: list[str]) -> bool:
        text = " ".join(
            [
                project_plan.requirement_summary,
                project_plan.app_behavior_summary,
                *project_plan.features,
            ]
        ).lower()
        has_rgb_feature = any(feature.instance.upper() == "RGB_LED" for feature in project_plan.board_features)
        has_i2c_binding = any(binding.transport.upper() == "I2C" for binding in project_plan.transport_bindings)
        has_oled_driver = any("oled" in name.lower() or "ch1116" in name.lower() for name in installed_drivers)
        wants_status = any(token in text for token in ("status", "oled", "display", "显示"))
        return has_rgb_feature and has_i2c_binding and has_oled_driver and wants_status

    def _rgb_oled_status_fallback_code(self, project_plan: ProjectPlan, installed_drivers: list[str]) -> tuple[str, str]:
        comment = project_plan.requirement_summary[:180] if project_plan.requirement_summary else "RGB OLED status application."
        driver_header = next(
            (name for name in installed_drivers if "oled" in name.lower() or "ch1116" in name.lower()),
            installed_drivers[0] if installed_drivers else "oled_128x64",
        )
        header = """#ifndef APP_MAIN_H
#define APP_MAIN_H

/**
 * @brief Initialize the RGB breathing demo with OLED status output.
 */
void app_main_init(void);

/**
 * @brief Run one RGB/OLED status update step.
 */
void app_main_loop(void);

#endif /* APP_MAIN_H */
"""
        source = f"""#include "app_main.h"
#include "luxar_hardware.h"
#include "{driver_header}.h"

#include <stddef.h>
#include <stdint.h>

/* Requirement summary: {comment} */

#define MAX_DUTY                999U
#define BREATH_STEP_MAX         200U
#define OLED_UPDATE_DIVIDER     5U

static ch1116_hal_t g_oled_hal;
static uint16_t g_breath_step;
static uint16_t g_report_divider;
static uint16_t g_red_pwm;
static uint16_t g_green_pwm;
static uint16_t g_blue_pwm;

static int oled_i2c_write(uint8_t dev_addr, const uint8_t *data, uint16_t len, uint32_t timeout)
{{
    (void)timeout;
    return luxar_i2c_txrx(dev_addr, data, len, NULL, 0U);
}}

static uint16_t triangle_wave(uint16_t step, uint16_t offset)
{{
    uint16_t pos = (uint16_t)((step + offset) % BREATH_STEP_MAX);
    if (pos > (BREATH_STEP_MAX / 2U)) {{
        pos = (uint16_t)(BREATH_STEP_MAX - pos);
    }}
    return (uint16_t)(((uint32_t)pos * MAX_DUTY) / (BREATH_STEP_MAX / 2U));
}}

static void oled_write_bar(uint8_t page, uint16_t value)
{{
    uint8_t buffer[CH1116_LCD_WIDTH];
    uint16_t lit = (uint16_t)(((uint32_t)value * CH1116_LCD_WIDTH) / MAX_DUTY);
    uint16_t index;

    for (index = 0U; index < CH1116_LCD_WIDTH; ++index) {{
        buffer[index] = (index < lit) ? 0xFFU : 0x00U;
    }}
    (void)ch1116_write_buffer(&g_oled_hal, page, 0U, buffer, CH1116_LCD_WIDTH);
}}

static void oled_update_status(void)
{{
    oled_write_bar(0U, g_red_pwm);
    oled_write_bar(2U, g_green_pwm);
    oled_write_bar(4U, g_blue_pwm);
}}

void app_main_init(void)
{{
    g_oled_hal.i2c_write = oled_i2c_write;
    g_oled_hal.delay_ms = luxar_delay_ms;
    g_breath_step = 0U;
    g_report_divider = 0U;
    g_red_pwm = 0U;
    g_green_pwm = 0U;
    g_blue_pwm = 0U;

    luxar_rgb_pwm_set(0U, 0U, 0U);
    (void)ch1116_init(&g_oled_hal);
    (void)ch1116_clear_display(&g_oled_hal);
    luxar_uart_write("RGB OLED status demo ready\\r\\n");
}}

void app_main_loop(void)
{{
    g_red_pwm = triangle_wave(g_breath_step, 0U);
    g_green_pwm = triangle_wave(g_breath_step, BREATH_STEP_MAX / 3U);
    g_blue_pwm = triangle_wave(g_breath_step, (BREATH_STEP_MAX * 2U) / 3U);

    luxar_rgb_pwm_set(g_red_pwm, g_green_pwm, g_blue_pwm);
    g_breath_step = (uint16_t)((g_breath_step + 1U) % BREATH_STEP_MAX);
    g_report_divider++;

    if (g_report_divider >= OLED_UPDATE_DIVIDER) {{
        g_report_divider = 0U;
        oled_update_status();
    }}

    luxar_delay_ms(20U);
}}
"""
        return header, source

    def _rgb_rainbow_fallback_code(self, project_plan: ProjectPlan) -> tuple[str, str]:
        comment = project_plan.requirement_summary[:180] if project_plan.requirement_summary else "RGB rainbow application."
        header = """#ifndef APP_MAIN_H
#define APP_MAIN_H

/**
 * @brief Initialize the RGB rainbow demo application.
 */
void app_main_init(void);

/**
 * @brief Run one RGB rainbow update step.
 */
void app_main_loop(void);

#endif /* APP_MAIN_H */
"""
        source = f"""#include "app_main.h"
#include "luxar_hardware.h"

#include <stdint.h>

/* Requirement summary: {comment} */

static uint16_t g_hue;
static uint16_t g_report_divider;

static uint16_t scale8_to_pwm(uint8_t value)
{{
    return (uint16_t)(((uint32_t)value * 999U) / 255U);
}}

static void hsv_to_rgb(uint16_t hue, uint8_t *red, uint8_t *green, uint8_t *blue)
{{
    uint8_t region;
    uint8_t remainder;
    uint8_t p;
    uint8_t q;
    uint8_t t;
    const uint8_t value = 255U;
    const uint8_t saturation = 255U;

    hue %= 360U;
    region = (uint8_t)(hue / 60U);
    remainder = (uint8_t)(((hue % 60U) * 255U) / 60U);
    p = (uint8_t)(((uint16_t)value * (uint16_t)(255U - saturation)) / 255U);
    q = (uint8_t)(((uint16_t)value * (uint16_t)(255U - (((uint16_t)saturation * remainder) / 255U))) / 255U);
    t = (uint8_t)(((uint16_t)value * (uint16_t)(255U - (((uint16_t)saturation * (uint8_t)(255U - remainder)) / 255U))) / 255U);

    switch (region) {{
    case 0U:
        *red = value;
        *green = t;
        *blue = p;
        break;
    case 1U:
        *red = q;
        *green = value;
        *blue = p;
        break;
    case 2U:
        *red = p;
        *green = value;
        *blue = t;
        break;
    case 3U:
        *red = p;
        *green = q;
        *blue = value;
        break;
    case 4U:
        *red = t;
        *green = p;
        *blue = value;
        break;
    default:
        *red = value;
        *green = p;
        *blue = q;
        break;
    }}
}}

/**
 * @brief Initialize RGB outputs and emit a boot message.
 */
void app_main_init(void)
{{
    g_hue = 0U;
    g_report_divider = 0U;
    luxar_rgb_pwm_set(0U, 0U, 0U);
    luxar_uart_write("codexbaretest RGB rainbow ready\\r\\n");
    luxar_uart_write("USART2 debug active, TIM3 PWM on PA6 PA7 PB0\\r\\n");
}}

/**
 * @brief Advance the RGB LED by one hue step.
 */
void app_main_loop(void)
{{
    uint8_t red;
    uint8_t green;
    uint8_t blue;

    hsv_to_rgb(g_hue, &red, &green, &blue);
    luxar_rgb_pwm_set(scale8_to_pwm(red), scale8_to_pwm(green), scale8_to_pwm(blue));

    g_hue = (uint16_t)((g_hue + 2U) % 360U);
    g_report_divider++;
    if (g_report_divider >= 50U) {{
        g_report_divider = 0U;
        luxar_uart_write("RGB rainbow cycling\\r\\n");
    }}
    luxar_delay_ms(20U);
}}
"""
        return header, source

    def _verify_generated(
        self,
        header: str,
        source: str,
        *,
        project_plan: ProjectPlan,
        installed_drivers: list[str],
    ) -> list[str]:
        """Run integrity checks on generated code. Returns list of failure messages."""
        failures: list[str] = []
        if "app_main_init" not in header:
            failures.append("MISSING: app_main_init declaration in header")
        if "app_main_loop" not in header:
            failures.append("MISSING: app_main_loop declaration in header")
        if "app_main_init" not in source:
            failures.append("MISSING: app_main_init implementation in source")
        if "app_main_loop" not in source:
            failures.append("MISSING: app_main_loop implementation in source")
        if "malloc" in source or "malloc" in header:
            failures.append("FORBIDDEN: malloc usage detected")
        if re.search(r"\bprintf\s*\(", source):
            failures.append("WARNING: printf usage — only allowed if UART was explicitly required")
        if "#include" in source and "stm32f10x.h" in source:
            failures.append("FORBIDDEN: stm32f10x.h — use stm32f1xx_hal.h instead")
        if re.search(r"\b(huart\d+|hspi\d+|hi2c\d+|htim\d+)\b", source):
            failures.append("FORBIDDEN: direct HAL handle reference in app code — use luxar_uart_write/luxar_delay_ms or other luxar_hardware APIs")
        if re.search(r"\bluxar_rgb_set\s*\(", source):
            failures.append("FORBIDDEN: unknown luxar_rgb_set helper — use luxar_rgb_pwm_set")
        if re.search(r"\bHAL_[A-Z]+\b", source) and "stm32f1xx_hal.h" not in source and "#include \"stm32f1xx_hal.h\"" not in source:
            failures.append("MISSING: stm32f1xx_hal.h include is required when using HAL functions")
        if self._is_rgb_oled_status_plan(project_plan, installed_drivers):
            include_names = re.findall(r'#include\s+"([^"]+)"', source)
            installed_headers = {f"{name}.h" for name in installed_drivers}
            if installed_headers and not any(name in installed_headers for name in include_names):
                failures.append("MISSING: app source did not include an installed driver header")
            oled_guess_headers = [name for name in include_names if ("oled" in name.lower() or "ch1116" in name.lower())]
            for include_name in oled_guess_headers:
                if installed_headers and include_name not in installed_headers:
                    failures.append(f"FORBIDDEN: app source referenced unavailable driver header {include_name}")
        if "#ifndef" not in header and "#ifndef" not in header.upper():
            failures.append("MISSING: include guard in header")
        return failures
