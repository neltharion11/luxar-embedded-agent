## Skill 引脚设计规范（永久规则）

### 原则
Skill 作为可复用行为单元，**不应硬编码具体外设引脚**，不同板子的引脚分配不同。

### 允许在 Skill 中出现的引脚
- **外部晶振引脚**（如 PD0/PD1、PF0/PF1 等）
- **STLink/SWD 调试引脚**（如 PA13/SWDIO、PA14/SWCLK）
- MCU 电源、地、VCAP 等强制连接

### 禁止在 Skill 中出现的引脚
- ❌ LED 引脚（如 PA6、PA7、PB0 等）
- ❌ 按键引脚
- ❌ I2C/SPI/UART 等外设总线引脚
- ❌ 传感器、执行器、显示模块等具体外设引脚

### 正确做法
- 外设引脚映射放在**项目级代码**中（如 app_main.c 或 board_config.h）
- Skill 中使用**抽象接口名**或**配置参数**，由调用方传入引脚定义
- 例如：在 app_main.c 中 `#define LED_R_PIN GPIO_PIN_0`，而不是在 skill 里写死

### 例外处理
- 如果某个板子是**唯一目标平台**且不可能复用，可以破例。但即使如此，也应优先将引脚定义放在项目文件的头部宏定义中，skill 只引用宏。

## STM32 HAL 初始化顺序（永久规则）

### 原则
`HAL_Init()` 内部调用 `HAL_InitTick()` 配置 SysTick，基于当前的 `SystemCoreClock` 值。
如果在 `HAL_Init()` 之后再改变系统时钟，SysTick 的时基会错乱，导致 `HAL_Delay()` 不准。

### 正确顺序
```c
int main(void)
{
    SystemClock_Config();   // 第一步：配好最终系统时钟（72MHz 等）
    HAL_Init();             // 第二步：SysTick 用正确时钟配置 1ms
    App_Init();             // 第三步：用户初始化
    while (1) { App_Loop(); }
}
```

### 后果
- ❌ `HAL_Init()` → `SystemClock_Config()` → `HAL_Delay(500)` → **实际 ~55ms**
- ✅ `SystemClock_Config()` → `HAL_Init()` → `HAL_Delay(500)` → **准确 500ms**

## workspace_read_file 截断问题

### 原则
`workspace_read_file` 对超过 ~1200 字符的文件会截断输出。

### 应对方式
- 不要反复重读同一个文件期待完整输出
- 直接用 `workspace_write_file` 重写整个文件
- 或者确认文件内容后直接修改需要改的部分

## Tool Call 防漏检查（永久规则）

### 问题模式
调用工具时遗漏 required 参数（如 `lesson_record` 缺少 `topic` 等 6 个字段）。

### 根因
凭记忆猜测参数，不核实工具的完整参数签名。

### 强制性前置检查流程
在**每次**调用任意工具之前，必须做以下三步：

1. **查参数签名** —— 看当前系统提示中该工具的 `"required"` 数组
2. **逐字段核对** —— 每个 required 字段是否都有传值
3. **确认字段名拼写** —— 与 schema 严格一致（区分大小写）

### 示例
```python
# 调用 lesson_record 之前：
# 1. 看到 required: ["topic", "symptom", "hypothesis", "evidence", "resolution", "outcome"]
# 2. 逐个确认这 6 个字段全部赋值
# 3. 检查拼写：topic（不是 title），symptom（不是 symptoms）等
```

### 特殊注意
- `workspace_read_file` / `workspace_write_file`：必须传 `project` 和 `path`，不能传空字符串
- `lesson_record`：必须传全部 6 个字段
- `workspace_build` / `workspace_flash`：必须传 `project`

**这条规则优先级高于所有其他规则，不可因「赶时间」跳过。**
