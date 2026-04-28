from luxar.prompts.gates import ANTI_RATIONALIZATION, SELF_REVIEW_GATE


SKILL_EVOLUTION_SYSTEM_PROMPT = f"""
你是一位资深嵌入式平台工程师，负责把项目经验沉淀成可复用的协议级 skill。
输出必须务实、可复用、避免项目私货，优先总结接口模式、约束、调试经验和边界条件。

{SELF_REVIEW_GATE}

{ANTI_RATIONALIZATION}
""".strip()


SKILL_EVOLUTION_PROMPT = """
任务：根据以下验证通过的项目经验，生成一个协议通用 skill 文档。

【协议】
{protocol}

【器件】
{device_name}

【平台】
{platforms}

【运行时】
{runtimes}

【来源项目】
{source_project}

【摘要】
{summary}

【经验教训】
{lessons_learned}

【要求】
1. 只沉淀协议级和驱动级通用经验，不写项目路径、私有板卡细节或一次性调试噪声
2. 必须包含：适用范围、接口模式、常见错误、调试检查表、边界条件
3. 内容面向后续 ESP、Linux、FreeRTOS 等平台扩展，避免只写 STM32 私有表述
4. 输出为单个 Markdown 文档，不要加解释
""".strip()
