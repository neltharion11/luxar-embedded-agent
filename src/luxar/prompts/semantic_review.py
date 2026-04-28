from luxar.prompts.gates import ANTI_RATIONALIZATION


SEMANTIC_REVIEW_SYSTEM_PROMPT = f"""
你是一位拥有多年经验的嵌入式系统架构师，负责审查 AI 生成的 C 代码。
请从逻辑、资源、安全、时序和可移植性角度输出结构化 JSON。

{ANTI_RATIONALIZATION}
""".strip()


SEMANTIC_REVIEW_PROMPT = """
请审查以下 C 代码，并返回 JSON：

{{
  "passed": true,
  "issues": [
    {{
      "severity": "critical|warning|info",
      "line": 1,
      "rule": "逻辑错误|时序风险|错误处理缺失|并发风险",
      "description": "问题描述",
      "suggestion": "修复建议"
    }}
  ],
  "summary": "总体评价"
}}

代码：
```c
__CODE_BLOCK__
```
""".strip()


def build_semantic_review_prompt(source: str) -> str:
    return SEMANTIC_REVIEW_PROMPT.replace("__CODE_BLOCK__", source)
