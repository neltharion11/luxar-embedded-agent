"""真实 DeepSeek Smoke Test：仅在双重环境开关启用时发送一次最小请求。"""

from __future__ import annotations

import os

import pytest

from luxar.adapters.deepseek.client import DeepSeekJsonClient
from luxar.adapters.deepseek.requirement_parser import (
    DeepSeekRequirementParser,
)
from luxar.adapters.deepseek.settings import DeepSeekSettings


RUN_SMOKE = os.getenv("LUXAR_RUN_DEEPSEEK_SMOKE") == "1"
HAS_KEY = bool(os.getenv("DEEPSEEK_API_KEY"))


pytestmark = pytest.mark.skipif(
    not (RUN_SMOKE and HAS_KEY),
    reason=(
        "requires DEEPSEEK_API_KEY and "
        "LUXAR_RUN_DEEPSEEK_SMOKE=1"
    ),
)


def test_real_deepseek_parses_one_minimal_requirement() -> None:
    settings = DeepSeekSettings()
    client = DeepSeekJsonClient(settings)
    parser = DeepSeekRequirementParser(
        client=client,
        model=settings.fast_model,
    )

    requirement = parser.parse(
        "使用 ESP32 的 GPIO2 创建一个 LED 闪烁固件"
    )

    # 只检查经过 Pydantic 验证的 Domain 对象，不打印原始响应或密钥。
    assert requirement.target == "esp32"
    gpio = next(item for item in requirement.peripherals if item.kind == "gpio")
    assert gpio.parameters["pin"] == 2
    assert requirement.is_complete is True
