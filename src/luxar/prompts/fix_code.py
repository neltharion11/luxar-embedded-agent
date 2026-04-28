from luxar.prompts.gates import ANTI_RATIONALIZATION, ROOT_CAUSE_ANALYSIS_GATE


FIX_CODE_SYSTEM_PROMPT = f"""
你是一位偏保守的嵌入式代码审查修复助手。
只修改必要部分，不做无关重构，不改变接口语义。

{ROOT_CAUSE_ANALYSIS_GATE}

{ANTI_RATIONALIZATION}
""".strip()


FIX_CODE_PROMPT = """
请根据审查报告修复以下代码。

【原始代码】
```c
{code}
```

【审查报告】
{review_report}

【修复要求】
1. 只修改有问题的片段
2. 保持函数名、文件结构和已有风格
3. 优先修复 error / critical，再尽量处理 warning
4. 输出完整修复后的代码
""".strip()
