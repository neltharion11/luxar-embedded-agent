import json

from luxar.adapters.deepseek.fake_client import FakeJsonCompletionClient
from luxar.adapters.deepseek.project_analyzer import DeepSeekProjectAnalyzer
from luxar.domain.repairs import ProjectFile


def test_project_analyzer_makes_post_source_retrieval_decision() -> None:
    client = FakeJsonCompletionClient(
        [
            {
                "project_exists": True,
                "has_source_code": True,
                "fingerprint": "model-placeholder",
                "summary": "代码把 GPIO34 配置为输出。",
                "entry_points": ["main/main.c"],
                "implemented_features": [],
                "architecture": [],
                "gaps": [],
                "risks": [],
                "evidence_paths": ["main/main.c"],
                "evidence_decision": {
                    "code_evidence_sufficient": False,
                    "confirmed_from_code": ["GPIO34 被配置为输出"],
                    "missing_evidence": ["GPIO34 的芯片级能力限制"],
                    "knowledge_retrieval": "retrieve",
                    "knowledge_query": "ESP32 GPIO34 input only output limitation",
                    "reason": "源码不能证明芯片引脚的电气能力",
                },
                "cache_hit": False,
            }
        ]
    )
    analyzer = DeepSeekProjectAnalyzer(client, "deepseek-reasoner")

    result = analyzer.analyze(
        project_name="blink",
        target_chip="esp32",
        fingerprint="source-fingerprint",
        files=[
            ProjectFile(
                path="main/main.c",
                content="gpio_set_direction(GPIO_NUM_34, GPIO_MODE_OUTPUT);",
            )
        ],
        inspection_request="检查 GPIO34 为什么不能输出",
    )

    system_prompt, user_prompt, model = client.calls[0]
    payload = json.loads(user_prompt)
    assert payload["inspection_request"] == "检查 GPIO34 为什么不能输出"
    assert "读取源码后必须填写 evidence_decision" in system_prompt
    assert result.fingerprint == "source-fingerprint"
    assert result.evidence_decision.knowledge_retrieval == "retrieve"
    assert result.evidence_decision.confirmed_from_code == [
        "GPIO34 被配置为输出"
    ]
    assert model == "deepseek-reasoner"

