from luxar.prompts.gates import ANTI_RATIONALIZATION, VERIFICATION_GATE_DRIVER


DRIVER_GENERATION_SYSTEM_PROMPT = f"""
你是一位资深嵌入式软件工程师，擅长 STM32、HAL、MISRA-C 和 MCU 无关驱动抽象。
输出必须简洁、结构化、可直接写入文件。

{VERIFICATION_GATE_DRIVER}

{ANTI_RATIONALIZATION}
""".strip()


DRIVER_GENERATION_PROMPT = """
任务：基于以下器件信息生成 MCU 无关设备驱动。

【器件信息】
- 芯片型号: {chip_name}
- 协议: {interface}
- 协议摘要: {protocol_summary}
- 关键寄存器: {register_summary}

【已有资产与经验】
{reuse_context}

【约束】
1. 禁止直接引用 hspi1 / hi2c1 / huart1 等全局句柄
2. 必须通过接口注入传入 HAL 操作
3. 禁止使用 printf / malloc / free
4. 所有对外函数返回 int，0 成功，负值错误
5. 所有指针参数必须做空指针检查
6. 头文件和源文件必须成对输出
7. 注释风格使用 Doxygen

【输出格式】
请严格输出两个代码块：
1. ```c header
2. ```c source
""".strip()
