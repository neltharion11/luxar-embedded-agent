/* ========================================================================== */
/* 文件: app_main.c                                                           */
/* 项目: keysking_project — 基于 STM32F103C8T6(Blue Pill) 的代码学习项目      */
/* 主频: 72MHz (HSE 8MHz 外部晶振, PLL 9倍频)                                */
/* 架构: 超级循环 (Super Loop) 架构                                           */
/*                                                                            */
/* 项目概述:                                                                  */
/*   本项目是一个面向嵌入式初学者的综合学习平台,通过一个主循环集成多种        */
/*   外设模块,涵盖了 STM32F1 系列的大部分常用功能:                            */
/*     - GPIO 输入输出 (按键、LED、循迹传感器)                                */
/*     - EXTI 外部中断 (按键触发、超声波回波捕获)                             */
/*     - 定时器 PWM 输出 (电机控制、WS2812 灯带、舵机)                        */
/*     - 定时器编码器模式 (电机转速测量)                                      */
/*     - ADC 单次采样 (NTC 热敏电阻温度测量)                                  */
/*     - I2C 通信 (OLED 显示、AHT20 温湿度传感器)                             */
/*     - UART 串口通信 (调试串口、蓝牙模块)                                   */
/*     - DMA + 空闲中断接收 (USART2/USART3)                                   */
/*     - WS2812 智能灯带 (TIM3 PWM + DMA 驱动)                                */
/*                                                                            */
/* 集成的 BSP 模块:                                                           */
/*   - CH1116:   OLED 显示屏驱动 (128x64, I2C 地址 0x3C)                     */
/*   - AHT20:    温湿度传感器 (I2C 地址 0x38)                                */
/*   - DX_BT24:  蓝牙串口透传模块 (USART3, 9600 8N1)                         */
/*   - DRV8833:  直流电机驱动芯片 (TIM2 CH1/CH2 双路 PWM)                    */
/*   - HCSR04:   超声波测距模块 (GPIO 触发 + EXTI 回波)                      */
/*   - NTC:      热敏电阻温度传感器 (ADC1_CH4, PA4)                          */
/*   - WS2812:   智能 LED 灯带 (TIM3 CH1 PWM + DMA, PB4)                     */
/*                                                                            */
/* 架构说明:                                                                  */
/*   本文件实现超级循环架构的应用层:                                          */
/*   1. App_Init()  — 上电时调用一次,完成所有外设和应用模块的初始化           */
/*   2. App_Loop()  — 在 main() 的 while(1) 中反复调用,处理所有周期性任务    */
/*   3. 回调函数集  — EXTI 中断回调、UART 空闲中断回调,在中断上下文中执行     */
/*                                                                            */
/* 引脚分配:                                                                  */
/*   PA0  — TIM2_CH1  (DRV8833 电机 PWM A)                                   */
/*   PA1  — TIM2_CH2  (DRV8833 电机 PWM B)                                   */
/*   PA4  — ADC1_IN4  (NTC 热敏电阻)                                         */
/*   PA6  — RGB LED G (GPIO 输出)                                            */
/*   PA7  — RGB LED B (GPIO 输出)                                            */
/*   PA8  — TIM1_CH1  (编码器输入 A)                                         */
/*   PA9  — TIM1_CH2  (编码器输入 B)                                         */
/*   PA10 — HC-SR04 Echo (EXTI 输入)                                         */
/*   PA11 — HC-SR04 Trig (GPIO 输出)                                         */
/*   PB0  — RGB LED R (GPIO 输出, 原 TIM4_CH3 PWM 已重配置)                  */
/*   PB4  — TIM3_CH1  (WS2812 灯带数据线)                                    */
/*   PB6  — I2C1_SCL  (OLED + AHT20)                                         */
/*   PB7  — I2C1_SDA  (OLED + AHT20)                                         */
/*   PB10 — USART3_TX (DX-BT24 蓝牙)                                         */
/*   PB11 — USART3_RX (DX-BT24 蓝牙)                                         */
/*   PB12 — KEY1 (WS2812 模式切换, EXTI)                                     */
/*   PB13 — KEY2 (OLED 页面切换, EXTI)                                       */
/*   PB14 — 循迹传感器 (GPIO 输入)                                           */
/*   PB15 — 编码器按键 (风扇紧急停止, EXTI)                                  */
/*   PD5  — USART2_TX (调试串口)                                             */
/*   PD6  — USART2_RX (调试串口)                                             */
/* ========================================================================== */

#include "app_main.h"   /* 本文件头声明,包含 App_Init/Loop 及回调函数原型 */

/* STM32CubeMX 生成的外设句柄头文件 */
#include "main.h"       /* 系统级定义: Error_Handler() 等 */
#include "adc.h"        /* ADC1 句柄: hadc1 */
#include "dma.h"        /* DMA 句柄: DMA 通道定义 */
#include "i2c.h"        /* I2C1 句柄: hi2c1 */
#include "tim.h"        /* TIM1/2/3/4 句柄: htim1/htim2/htim3/htim4 */
#include "usart.h"      /* USART2/3 句柄: huart2/huart3 */
#include "gpio.h"       /* GPIO 初始化函数 */

/* 项目自定义 BSP 驱动头文件 */
#include "ch1116.h"     /* OLED 显示驱动 */
#include "aht20.h"      /* AHT20 温湿度传感器驱动 */
#include "dx_bt24.h"    /* DX-BT24 蓝牙模块驱动 */
#include "drv8833.h"    /* DRV8833 电机驱动 */
#include "hcsr04.h"     /* HC-SR04 超声波测距驱动 */
#include "ntc.h"        /* NTC 热敏电阻温度转换驱动 */
#include "ws2812.h"     /* WS2812 智能灯带驱动 */

/* C 标准库 */
#include <stdarg.h>     /* va_list / va_start / va_end — 可变参数宏 */
#include <stdlib.h>     /* strtol() — 字符串转长整型,用于命令解析 */
#include <stdio.h>      /* vsnprintf() — 格式化输出到缓冲区 */
#include <string.h>     /* memcpy() / strlen() / strcmp() / strncmp() */

/* ========================================================================== */
/* OLED 页面枚举                                                              */
/* 系统支持 3 个 OLED 显示页面,通过按键 KEY2 或蓝牙命令循环切换               */
/* ========================================================================== */
typedef enum
{
  OLED_PAGE_STATUS = 0, /* 状态页: 显示所有传感器/执行器概览 */
  OLED_PAGE_SENSOR = 1, /* 传感器页: 显示 NTC/AHT20/电机/按键/BT 明细 */
  OLED_PAGE_BT24   = 2  /* 蓝牙页: 显示 BT24 角色/链接/收发统计/最后数据 */
} OledPage_t;

/* ========================================================================== */
/* 配置常量宏定义                                                             */
/* 所有与硬件 / 时序相关的常量集中定义在此,便于移植和调参                      */
/* ========================================================================== */

/* --- UART 接收缓冲区大小 --- */
#define UART2_RX_BUFFER_SIZE      256U  /* USART2 (调试串口) DMA 循环缓冲区大小(字节) */
#define UART3_RX_BUFFER_SIZE      256U  /* USART3 (蓝牙模块) DMA 循环缓冲区大小(字节) */

/* --- BT24 OLED 显示缓冲区大小 --- */
#define BT24_OLED_TEXT_SIZE       20U   /* 蓝牙最后接收的可打印文本缓冲区 */
#define BT24_HEX_TEXT_SIZE        20U   /* 蓝牙最后接收的十六进制显示缓冲区 */

/* --- 定时任务周期(毫秒) --- */
#define STATUS_PERIOD_MS          500U  /* 状态报告/OLED 刷新周期,500ms */
#define I2C_SCAN_PERIOD_MS        3000U /* I2C 总线扫描周期,3秒一次 */
#define AHT20_PERIOD_MS           2000U /* AHT20 温湿度读取周期,2秒一次 */

/* --- 按键和电机控制参数 --- */
#define BUTTON_DEBOUNCE_MS        150U  /* 按键软件消抖时间,150ms */
#define MOTOR_SPEED_STEP_PERCENT  5     /* 编码器每转动一步对应的风扇速度变化百分比 */
#define FAN_START_BOOST_PERCENT   100   /* 风扇启动时短暂全速运转(启动力矩)百分比 */
#define FAN_START_BOOST_MS        300U  /* 启动力矩持续时间(毫秒) */

/* --- WS2812 智能灯带参数 --- */
#define WS2812_PIXEL_COUNT        10U   /* 灯带上的 LED 像素数量 */
#define WS2812_UPDATE_MS          25U   /* 灯带特效刷新周期,25ms (~40fps) */

/* --- RGB LED 引脚定义 --- */
#define LED_R_PORT                 GPIOB  /* 红色 LED 端口 */
#define LED_R_PIN                  GPIO_PIN_0  /* PB0 — RGB R */
#define LED_G_PORT                 GPIOA  /* 绿色 LED 端口 */
#define LED_G_PIN                  GPIO_PIN_6  /* PA6 — RGB G */
#define LED_B_PORT                 GPIOA  /* 蓝色 LED 端口 */
#define LED_B_PIN                  GPIO_PIN_7  /* PA7 — RGB B */
#define LED_RUNNING_STEP_MS        500U   /* 流水灯每步停留时间(毫秒) */

/* ========================================================================== */
/* 静态全局变量                                                               */
/* 这些变量仅在本文件内使用,通过 static 限制作用域                             */
/* ========================================================================== */

/* ---- USART2 (调试串口) 接收双缓冲 ---- */
/* rx_buffer:   DMA 持续写入的循环缓冲区,被中断和主循环共享 */
/* rx_frame:    空闲中断触发时,从 rx_buffer 复制到此帧缓冲区供主循环消费 */
/*              双缓冲机制确保主循环处理期间不会被新数据覆盖 */
static uint8_t uart2_rx_buffer[UART2_RX_BUFFER_SIZE];  /* DMA 接收缓冲区 */
static uint8_t uart2_rx_frame[UART2_RX_BUFFER_SIZE];   /* 帧复制缓冲区 */
static volatile uint16_t uart2_rx_frame_size;           /* 当前帧有效数据长度 */
static volatile uint8_t  uart2_rx_frame_ready;          /* 帧就绪标志: 1=有数据待处理 */
static volatile uint32_t uart2_rx_overrun_count;        /* 溢出计数: 主循环未处理完时新帧到来 */

/* ---- USART3 (BT24 蓝牙模块) 接收双缓冲 ---- */
static uint8_t uart3_rx_buffer[UART3_RX_BUFFER_SIZE];  /* DMA 接收缓冲区 */
static uint8_t uart3_rx_frame[UART3_RX_BUFFER_SIZE];   /* 帧复制缓冲区 */
static volatile uint16_t uart3_rx_frame_size;           /* 当前帧有效数据长度 */
static volatile uint8_t  uart3_rx_frame_ready;          /* 帧就绪标志 */
static volatile uint32_t uart3_rx_overrun_count;        /* 溢出计数 */

/* ---- I2C 设备扫描结果 ---- */
static uint8_t i2c_devices[16];    /* 检测到的 I2C 设备地址列表 */
static uint8_t i2c_device_count;   /* 检测到的 I2C 设备数量 */

/* ---- 按键消抖时间戳 ---- */
static uint32_t key1_last_tick;    /* KEY1 (PB12) 上次触发时的时间戳 */
static uint32_t key2_last_tick;    /* KEY2 (PB13) 上次触发时的时间戳 */
static volatile uint8_t button_event_mask;  /* 按键事件位掩码: bit0=KEY1, bit1=KEY2, bit2=ENC_KEY */

/* ---- OLED 相关 ---- */
static OledPage_t oled_page = OLED_PAGE_STATUS;  /* 当前 OLED 显示页面 */
static CH1116_HandleTypeDef oled;                 /* OLED 驱动句柄 */
static uint8_t oled_ready;                        /* OLED 初始化成功标志 */

/* ---- AHT20 温湿度传感器 ---- */
static AHT20_HandleTypeDef aht20;     /* AHT20 驱动句柄 */
static AHT20_Data_t aht20_data;       /* AHT20 温湿度数据(温度°C, 相对湿度%RH) */
static uint8_t aht20_ready;           /* AHT20 初始化成功标志 */

/* ---- DX-BT24 蓝牙模块 ---- */
static DX_BT24_HandleTypeDef bt24;  /* BT24 驱动句柄 */

/* ---- DRV8833 电机控制 ---- */
static DRV8833_HandleTypeDef motor; /* 电机驱动句柄 */

/* ---- HC-SR04 超声波测距 ---- */
static HCSR04_HandleTypeDef hcsr04; /* 超声波驱动句柄 */

/* ---- NTC 热敏电阻温度传感器 ---- */
static NTC_HandleTypeDef ntc;        /* NTC 驱动句柄 */
static NTC_Data_t ntc_data;          /* NTC 转换结果(温度°C, 电阻Ω) */
static uint8_t ntc_ready;            /* NTC 初始化成功标志 */
static uint16_t ntc_raw_last;        /* 上一次 ADC 原始采样值(12位, 0~4095) */

/* ---- WS2812 灯带 ---- */
static WS2812_HandleTypeDef ws2812;  /* WS2812 驱动句柄 */

/* ---- 电机编码器与速度控制 ---- */
static int16_t encoder_last_count;            /* 上一次读取的编码器计数值,用于计算差值 */
static int16_t motor_target_speed_percent;    /* 电机目标转速百分比(0~100) */
static uint32_t motor_boost_until_tick;       /* 启动力矩截止时间戳 */

/* ---- BT24 通信统计 ---- */
static uint32_t bt24_rx_total_bytes;  /* BT24 累计接收总字节数 */
static uint32_t bt24_tx_total_bytes;  /* BT24 累计发送总字节数 */
static char bt24_last_text[BT24_OLED_TEXT_SIZE];  /* 最后接收数据的可打印文本 */
static char bt24_last_hex[BT24_HEX_TEXT_SIZE];    /* 最后接收数据的十六进制表示 */

/* ---- 流水灯状态 ---- */
static uint32_t led_last_tick;  /* 上次流水灯步进的时间戳 */
static uint8_t  led_step;       /* 当前流水灯步进索引(0=R, 1=G, 2=B) */

/* ========================================================================== */
/* 静态函数前向声明                                                           */
/* ========================================================================== */
static void StartPeripherals(void);
static void StartDebugUartReception(void);
static void DebugPrint(const char *text);
static void DebugPrintf(const char *fmt, ...);
static void ConfigureNtcAdcSingleChannel(void);
static uint16_t ReadAdcChannel(uint32_t channel);
static void SampleAdc(uint16_t *ntc_raw);
static void UpdateOutputs(void);
static void UpdateMotorControl(void);
static void UpdateUltrasonic(void);
static void UpdateWs2812(void);
static void ScanI2cBus(void);
static void UpdateAht20(void);
static void UpdateNtc(uint16_t ntc_raw);
static void ReportStatus(void);
static void ProcessDebugUartFrame(void);
static void ProcessBt24Frame(void);
static void UpdateOled(void);
static HAL_StatusTypeDef Bt24SendString(const char *text);
static void Bt24Reply(const char *fmt, ...);
static void CycleOledPage(void);
static void SetOledPage(OledPage_t page);
static void SetFanSpeedPercent(int16_t speed_percent);
static uint8_t TryParseInt32(const char *text, int32_t *value);
static void NormalizeBt24Command(const uint8_t *data, uint16_t size, char *buffer, size_t buffer_size);
static uint8_t CondenseRepeatedBt24Command(const char *command, char *buffer, size_t buffer_size);
static int FindBt24CommandBoundary(const char *text, size_t start_index);
static uint8_t ExecuteBt24CommandStream(const char *command_stream);
static uint8_t HandleBt24Command(const char *command);
static uint32_t TriangleWave(uint32_t tick, uint32_t period_ms, uint32_t amplitude);
static uint32_t SmoothLevel(uint32_t linear_level, uint32_t amplitude);
static void ColorWheel(uint32_t hue, uint8_t *red, uint8_t *green, uint8_t *blue);
static const char *GetOledPageName(void);
static const char *GetBt24RoleName(const DX_BT24_HandleTypeDef *bt24_handle);
static void UpdateBt24LastText(const uint8_t *data, uint16_t size);
static void UpdateBt24LastHex(const uint8_t *data, uint16_t size);
static const char *GetBt24LinkName(void);
static const char *GetBt24WorkName(void);
static void LedRunningLight(void);

/* ========================================================================== */
/* 函数: App_Init()                                                           */
/* 描述: 应用层初始化入口,上电后调用一次                                      */
/*                                                                            */
/* 初始化流程总览:                                                            */
/*   1. ADC 校准 — 提高采样精度                                               */
/*   2. 配置 ADC 为单通道模式 (NTC 专用, IN4, PA4)                           */
/*   3. 启动外设 — TIM 编码器、PWM 输出                                      */
/*   4. I2C 总线扫描 — 发现挂载设备                                          */
/*   5. 各 BSP 驱动初始化:                                                    */
/*      - DX_BT24: 从机模式, 9600 8N1                                        */
/*      - DRV8833: 双路 PWM, TIM2 CH1/CH2                                    */
/*      - 舵机: TIM4_CH3, 中位 1500us (1.5ms 脉宽 = 90°)                    */
/*      - HCSR04: TRIG=PA11, ECHO=PA10                                       */
/*      - WS2812: 10 像素, 亮度 96/255                                       */
/*      - NTC: 默认配置 (查表温度转换)                                        */
/*      - AHT20: I2C 地址 0x38                                               */
/*      - CH1116 OLED: I2C 地址 0x3C                                         */
/*   6. GPIO 重配置: PB0/PA6/PA7 从 PWM/未配置状态重新初始化为 GPIO 输出      */
/*   7. 启动 UART 空闲中断 + DMA 接收                                          */
/* ========================================================================== */
void App_Init(void)
{
  /* NTC 默认配置: 使用内置 NTC 查表(10K B=3435) */
  const NTC_Config_t ntc_config = NTC_DEFAULT_CONFIG;

  /* ---- 第 1 步: ADC 校准 ---- */
  /*
   * HAL_ADCEx_Calibration_Start() — 启动 ADC 自校准
   * 原理: ADC 内部有电容阵列,因工艺偏差导致增益误差。
   *       校准过程自动测量并补偿该误差,提高转换精度。
   *       建议每次上电后调用一次。
   */
  HAL_ADCEx_Calibration_Start(&hadc1);

  /* ---- 第 2 步: 配置 NTC ADC 单通道 ---- */
  /*
   * 将 ADC1 配置为单通道单次转换模式:
   * - ScanConvMode = DISABLE  (非扫描模式,仅转换1个通道)
   * - ContinuousConvMode = DISABLE  (单次转换,每次需软件触发)
   * - 通道: ADC_CHANNEL_4 (对应 PA4 引脚)
   * - 采样时间: 239.5 个 ADC 时钟周期(约 5.35us @14MHz ADC时钟)
   *   较长的采样时间确保 NTC 分压电路的电压完全建立
   */
  ConfigureNtcAdcSingleChannel();

  /* ---- 第 3 步: 启动外设 ---- */
  /*
   * 启动 TIM1 编码器模式、TIM2 CH1/CH2 PWM(电机)、TIM4 CH3 PWM(舵机)
   * 并将各 PWM 初始占空比设为 0(电机) / 1500(舵机中位)
   */
  StartPeripherals();

  /* ---- 第 4 步: I2C 总线扫描 ---- */
  /*
   * 轮询 I2C1 总线上 1~127 地址的设备响应,
   * 将找到的设备地址保存到 i2c_devices[] 数组中
   */
  ScanI2cBus();

  /* ---- 第 5 步: 各 BSP 驱动初始化 ---- */

  /*
   * DX_BT24_Init(): 初始化蓝牙模块驱动
   * - 参数2: &huart3 — 使用 USART3 通信(9600波特率)
   * - 参数3: DX_BT24_ROLE_SLAVE — 从机模式(被手机连接)
   */
  DX_BT24_Init(&bt24, &huart3, DX_BT24_ROLE_SLAVE);

  /*
   * DRV8833_Init(): 初始化电机驱动
   * - 参数2: &htim2 — 使用 TIM2 产生 PWM
   * - 参数3/4: TIM_CHANNEL_1/2 — 双路 PWM 控制一个直流电机
   *   DRV8833 通过两路 PWM 控制电机的正反转和速度:
   *   CH1 > CH2 => 正转, CH1 < CH2 => 反转
   */
  DRV8833_Init(&motor, &htim2, TIM_CHANNEL_1, TIM_CHANNEL_2);

  /*
   * __HAL_TIM_SET_COMPARE(): 设置 TIM4_CH3 的捕获比较值
   * 舵机控制: 50Hz PWM (周期20ms)
   * 1500us => 脉宽 1.5ms => 舵机中位(90°)
   * 范围: 500us(0°) ~ 2500us(180°)
   */
  __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_3, 1500U);

  /*
   * HCSR04_Init(): 初始化 HC-SR04 超声波模块驱动
   * - 参数2/3: TRIG 引脚 — GPIOA, PIN_11 (控制触发信号)
   * - 参数4/5: ECHO 引脚 — GPIOA, PIN_10 (接收回波信号, EXTI 中断)
   */
  HCSR04_Init(&hcsr04, GPIOA, GPIO_PIN_11, GPIOA, GPIO_PIN_10);

  /*
   * WS2812_Init(): 初始化 WS2812 灯带驱动
   * - 参数2: &htim3 — 使用 TIM3 产生精确 PWM 时序
   * - 参数3: TIM_CHANNEL_1 — PWM 通道
   * - 参数4: 像素数量(10)
   *
   * WS2812 驱动原理: 通过 PWM+DMA 模拟 WS2812 的单总线通信协议。
   * TIM3 以特定频率产生 PWM,每个像素的 24-bit 颜色数据通过
   * DMA 搬运到 TIM3_CCR1,形成高/低电平的不同占空比表示"0码"和"1码"。
   */
  WS2812_Init(&ws2812, &htim3, TIM_CHANNEL_1, WS2812_PIXEL_COUNT);

  /* 设置 WS2812 亮度为 96/255 (约 38%) */
  WS2812_SetBrightness(&ws2812, 96U);

  /* 记录编码器当前计数值作为初始基准 */
  encoder_last_count = (int16_t)__HAL_TIM_GET_COUNTER(&htim1);
  /* 电机初始目标速度: 停止 */
  motor_target_speed_percent = 0;
  /* 启动力矩计时器初始化为 0 */
  motor_boost_until_tick = 0U;

  /*
   * NTC_Init(): 初始化 NTC 温度转换模块
   * NTC 使用 ADC 采集分压电阻上的电压,
   * 通过内置查表(电压→电阻→温度)计算实际温度。
   */
  NTC_Init(&ntc, &ntc_config);
  ntc_ready = 1U;  /* NTC 总是准备好(独立于 I2C,使用 ADC) */

  /*
   * AHT20_Init(): 初始化 AHT20 温湿度传感器
   * - 参数2: &hi2c1 — 挂载在 I2C1 总线上
   * - 参数3: AHT20_I2C_ADDR_7BIT — 7位 I2C 地址(0x38)
   *
   * AHT20 初始化需发送初始化命令并等待传感器就绪。
   * 初始化成功后立即读取一次数据作为初始值。
   */
  if (AHT20_Init(&aht20, &hi2c1, AHT20_I2C_ADDR_7BIT) == HAL_OK)
  {
    aht20_ready = 1U;
    (void)AHT20_ReadData(&aht20, &aht20_data);
  }

  /* 初次采样 NTC ADC 原始值并转换为温度 */
  SampleAdc(&ntc_raw_last);
  UpdateNtc(ntc_raw_last);

  /*
   * CH1116_Init(): 初始化 OLED 显示屏驱动
   * - 参数2: &hi2c1 — 也挂载在 I2C1 上
   * - 参数3: CH1116_I2C_ADDR_7BIT — OLED I2C 地址(0x3C)
   *
   * CH1116 是 128x64 单色 OLED 控制器,支持 I2C 接口。
   * 初始化成功后显示欢迎信息。
   */
  if (CH1116_Init(&oled, &hi2c1, CH1116_I2C_ADDR_7BIT) == HAL_OK)
  {
    oled_ready = 1U;
    CH1116_Clear(&oled);                                     /* 清屏 */
    CH1116_DrawString(&oled, 0, 0, "keysking_project", CH1116_COLOR_WHITE);  /* 显示项目名 */
    CH1116_DrawString(&oled, 0, 10, "OLED ready", CH1116_COLOR_WHITE); /* 显示就绪 */
    CH1116_UpdateScreen(&oled);                              /* 刷新显示 */
  }

  /* ---- 第 6 步: 打印初始化信息到调试串口 ---- */
  DebugPrint("\r\nkeysking_project boot ok\r\n");
  DebugPrint("USART2 idle interrupt reception enabled\r\n");
  DebugPrintf("AHT20: %s\r\n", aht20_ready ? "ready" : "not ready");
  DebugPrintf("BT24: ready on USART3 %s 9600 8N1 DMA\r\n", GetBt24RoleName(&bt24));
  DebugPrintf("NTC: standalone, Rfixed=%lu ohm\r\n",
              (uint32_t)(ntc.config.series_resistor_ohms + 0.5f));
  DebugPrintf("DRV8833 fan mode: %s %d%%\r\n",
              DRV8833_GetModeName(DRV8833_GetMode(&motor)),
              DRV8833_GetSpeed(&motor));
  DebugPrint("HCSR04: ready\r\n");
  DebugPrintf("WS2812: %s on PB4 TIM3+DMA\r\n", WS2812_GetModeName(WS2812_GetMode(&ws2812)));

  /* ---- 第 7 步: 重配置 PB0/PA6/PA7 为 RGB LED GPIO 输出 ---- */
  /*
   * 重新配置原理:
   *
   * CubeMX 默认配置了 PB0 为 TIM4_CH3 (PWM 舵机输出),
   * PA6/PA7 未配置(可能为默认状态或用作其他功能)。
   * 为了驱动 RGB LED (共阴极,高电平点亮),需要将它们重新初始化为 GPIO 输出。
   *
   * 为什么需要重新配置:
   * - PB0 (TIM4_CH3): CubeMX 配置为 PWM 模式,
   *   但 RGB LED 需要 GPIO 推挽输出模式。
   *   通过 HAL_GPIO_DeInit() 先解除外设复用,再重新配置为 GPIO 输出。
   * - PA6/PA7: 在 CubeMX 中可能未配置(默认模拟输入/浮空输入),
   *   需要显式初始化为 GPIO 输出模式并设置低电平(熄灭)。
   *
   * HAL_GPIO_DeInit() — 将引脚恢复到默认复位状态(浮空输入),
   * 解除 TIM4 对 PB0 的占用。
   * 然后重新调用 HAL_GPIO_Init() 配置为推挽输出。
   */
  {
    GPIO_InitTypeDef gpio = {0};

    /* PB0 -- RGB LED R (红色通道) */
    /*
     * 第一步: 去初始化,解除 TIM4_CH3 的引脚复用
     * HAL_GPIO_DeInit() 将引脚恢复为默认的浮空输入状态
     */
    HAL_GPIO_DeInit(GPIOB, GPIO_PIN_0);
    /*
     * 第二步: 重新初始化为 GPIO 推挽输出
     * Mode = GPIO_MODE_OUTPUT_PP: 推挽输出,可驱动 LED
     * Pull = GPIO_NOPULL: LED 不需要内部上拉/下拉
     * Speed = GPIO_SPEED_FREQ_LOW: LED 为低速信号,低频即可
     */
    gpio.Pin    = GPIO_PIN_0;
    gpio.Mode   = GPIO_MODE_OUTPUT_PP;
    gpio.Pull   = GPIO_NOPULL;
    gpio.Speed  = GPIO_SPEED_FREQ_LOW;
    HAL_GPIO_Init(GPIOB, &gpio);

    /* PA6, PA7 -- RGB LED G, B (绿色/蓝色通道) */
    /*
     * PA6/PA7 在 CubeMX 中可能未配置为输出,
     * 先使能 GPIOA 时钟(确保时钟已开启),
     * 再配置为推挽输出,初始低电平(熄灭)
     */
    __HAL_RCC_GPIOA_CLK_ENABLE();
    gpio.Pin    = GPIO_PIN_6 | GPIO_PIN_7;
    HAL_GPIO_Init(GPIOA, &gpio);

    /* 初始全部熄灭(低电平) */
    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_6 | GPIO_PIN_7, GPIO_PIN_RESET);
    DebugPrint("RGB LED (R=PB0 G=PA6 B=PA7): ready\r\n");
  }

  /* ---- 第 8 步: 启动 UART 空闲中断 + DMA/IT 接收 ---- */
  /*
   * 启动串口接收:
   * - USART2 (调试串口): 使用空闲中断模式(IT)
   * - USART3 (蓝牙模块): 使用空闲中断 + DMA 模式
   * 两者都采用"空闲中断"机制: 当串口收到一帧数据后,
   * 总线空闲(无新数据)时会触发空闲中断,
   * 在中断回调中处理接收到的数据。
   */
  StartDebugUartReception();
}

/* ========================================================================== */
/* 函数: App_Loop()                                                           */
/* 描述: 应用层主循环,在 main() 的 while(1) 中反复调用                        */
/*                                                                            */
/* 超级循环架构说明:                                                          */
/*   本系统不依赖 RTOS (实时操作系统),而是采用最经典的"超级循环"架构:         */
/*   - main() 主函数中:                                                       */
/*       while (1) { App_Loop(); }                                            */
/*   - 每一轮循环都执行 ADC 采样、输出更新、串口帧处理、流水灯                */
/*   - 定时任务(状态报告、I2C扫描、传感器读取)通过记录时间戳 + 周期比较实现:   */
/*     if ((now - last_tick) >= PERIOD_MS) { ... }                            */
/*   - 这种方式的优点是简单、可预测、无上下文切换开销;                          */
/*     缺点是长任务会阻塞其他任务的执行周期。                                   */
/*                                                                            */
/* 每轮循环执行的任务:                                                        */
/*   [必做]  ADC 采样 -> NTC 温度转换 -> 输出更新 -> 串口处理 -> 流水灯       */
/*   [定时]  状态报告 + OLED 刷新 (500ms)                                     */
/*   [定时]  I2C 总线扫描 (3s)                                                */
/*   [定时]  AHT20 温湿度读取 (2s)                                            */
/*   [条件]  按键事件处理 (消抖后执行)                                        */
/* ========================================================================== */
void App_Loop(void)
{
  /*
   * 静态局部变量: 函数返回后值保持不变
   * 记录上次执行各定时任务的时间戳
   */
  static uint32_t last_status_tick;       /* 上次状态报告时间 */
  static uint32_t last_i2c_scan_tick;     /* 上次 I2C 扫描时间 */
  static uint32_t last_aht20_tick;        /* 上次 AHT20 读取时间 */
  static uint32_t last_button_report_tick; /* 上次按键事件处理时间 */

  /* 获取当前系统滴答计时(ms), HAL_GetTick() 基于 SysTick 中断 */
  uint32_t now = HAL_GetTick();

  /* ---- 每轮必做任务(无周期限制,每次循环都执行) ---- */

  /*
   * ADC 采样: 读取 NTC 热敏电阻的 ADC 原始值
   * 每次循环都采样,确保数据实时性
   */
  SampleAdc(&ntc_raw_last);
  /*
   * NTC 温度转换: 将 ADC 原始值通过查表换算为实际温度
   * 每次循环都转换,让温度数据保持最新
   */
  UpdateNtc(ntc_raw_last);
  /*
   * 输出状态更新:
   * - 更新电机控制(编码器差值 + PID 速度调节)
   * - 更新超声波测距(触发/处理)
   * - 更新 WS2812 灯带特效
   * - 更新循迹传感器输出
   */
  UpdateOutputs();
  /*
   * 处理调试串口帧: 检查 USART2 是否有数据待处理
   * 收到数据后回显并通过 BT24 蓝牙转发
   */
  ProcessDebugUartFrame();
  /*
   * 处理蓝牙帧: 检查 USART3 是否有数据待处理
   * 解析 BT24 蓝牙命令并执行(风扇控制、灯带控制等)
   */
  ProcessBt24Frame();
  /*
   * 流水灯: RGB LED 循环点亮 (R→G→B→R...)
   * 每 500ms 步进一次
   */
  LedRunningLight();

  /* ---- 定时任务 1: 状态报告 + OLED 刷新 (500ms 周期) ---- */
  /*
   * 原理: 用当前时间减去上次执行时间,差值 >= 500ms 时执行
   * HAL_GetTick() 返回的是 uint32_t 毫秒计时,当系统运行约 49.7 天后
   * 会溢出回绕到 0,但由于使用了无符号减法(负值自动回绕为很大的正数),
   * 只要时间差不超过 49.7 天,溢出保护是安全的。
   */
  if ((now - last_status_tick) >= STATUS_PERIOD_MS)
  {
    last_status_tick = now;   /* 更新上次执行时间戳 */
    ReportStatus();           /* 打印状态报告到调试串口 */
    UpdateOled();             /* 刷新 OLED 显示屏 */
  }

  /* ---- 定时任务 2: I2C 总线扫描 (3s 周期) ---- */
  /*
   * 定时扫描 I2C 总线,检测是否有新设备接入或设备移除。
   * 这对于调试和动态设备管理很有用。
   * 扫描结果通过 ReportStatus() 打印出来。
   */
  if ((now - last_i2c_scan_tick) >= I2C_SCAN_PERIOD_MS)
  {
    last_i2c_scan_tick = now;
    ScanI2cBus();
  }

  /* ---- 定时任务 3: AHT20 温湿度读取 (2s 周期) ---- */
  /*
   * AHT20 数据更新速度较慢(典型单次测量约 80ms),
   * 不需要每次循环都读取,2s 刷新一次足够。
   * 若读取失败则标记为未就绪。
   */
  if ((now - last_aht20_tick) >= AHT20_PERIOD_MS)
  {
    last_aht20_tick = now;
    UpdateAht20();
  }

  /* ---- 按键事件处理(消抖后执行) ---- */
  /*
   * button_event_mask: 由 EXTI 中断回调设置位标志
   *   bit0 = KEY1 按下   -> 切换 WS2812 模式
   *   bit1 = KEY2 按下   -> 切换 OLED 页面
   *   bit2 = 编码器按键  -> 风扇停止
   *
   * 消抖机制: 按键触发后,等待 BUTTON_DEBOUNCE_MS (150ms) 才处理,
   * 确保按键信号稳定,避免因机械抖动导致的误触发。
   */
  if ((button_event_mask != 0U) && ((now - last_button_report_tick) >= BUTTON_DEBOUNCE_MS))
  {
    last_button_report_tick = now;

    /* KEY1 (PB12): 切换 WS2812 灯带效果模式 */
    if ((button_event_mask & 0x01U) != 0U)
    {
      /*
       * WS2812_SetMode(): 设置灯带工作模式
       * 模式循环: 当前模式 + 1 再取模,实现循环切换
       * 支持的 mode 定义在 ws2812.h: RAINBOW/BREATH/CHASE/COMET
       */
      WS2812_SetMode(&ws2812, (WS2812_Mode_t)(((uint32_t)WS2812_GetMode(&ws2812) + 1U) % WS2812_MODE_COUNT));
      DebugPrintf("WS2812 mode switched: %s\r\n", WS2812_GetModeName(WS2812_GetMode(&ws2812)));
      UpdateOled();  /* 刷新 OLED 显示模式信息 */
    }

    /* KEY2 (PB13): 切换 OLED 显示页面 */
    if ((button_event_mask & 0x02U) != 0U)
    {
      CycleOledPage();  /* STATUS -> SENSOR -> BT24 -> STATUS... */
      DebugPrintf("OLED page switched: %s\r\n", GetOledPageName());
      UpdateOled();
    }

    /* 编码器按键 (PB15): 风扇紧急停止 */
    if ((button_event_mask & 0x04U) != 0U)
    {
      SetFanSpeedPercent(0);  /* 目标速度设为 0 */
      /* 重置编码器基准计数值,防止停止后重新启动时速度突变 */
      encoder_last_count = (int16_t)__HAL_TIM_GET_COUNTER(&htim1);
      DebugPrintf("fan stop: %s %d%%\r\n",
                  DRV8833_GetModeName(DRV8833_GetMode(&motor)),
                  DRV8833_GetSpeed(&motor));
      UpdateOled();
    }

    /* 清除事件掩码,等待下一次中断触发 */
    button_event_mask = 0U;
  }

  /* ---- UART 溢出告警 ---- */
  /*
   * 如果 UART 接收溢出计数不为 0,打印告警信息并清零。
   * 溢出意味着主循环处理速度跟不上数据接收速度,
   * 导致数据帧被丢弃。
   */
  if (uart2_rx_overrun_count != 0U)
  {
    DebugPrintf("uart2 rx overrun count=%lu\r\n", uart2_rx_overrun_count);
    uart2_rx_overrun_count = 0U;
  }

  if (uart3_rx_overrun_count != 0U)
  {
    DebugPrintf("bt24 rx overrun count=%lu\r\n", uart3_rx_overrun_count);
    uart3_rx_overrun_count = 0U;
  }
}

/* ========================================================================== */
/* 函数: App_OnGpioExtiCallback()                                             */
/* 描述: GPIO EXTI 外部中断回调函数                                           */
/*                                                                            */
/* EXTI 外部中断原理:                                                         */
/*   STM32F103 的 EXTI (External Interrupt) 控制器支持最多 16 个外部中断线。   */
/*   当 GPIO 引脚检测到指定边沿(上升沿/下降沿/双边沿)时触发中断。             */
/*   本项目中以下引脚配置为 EXTI 中断:                                        */
/*   - PA10: HC-SR04 超声波回波信号(双边沿中断)                              */
/*           上升沿: 开始计时,下降沿: 结束计时,用脉宽计算距离                 */
/*   - PB12: KEY1 按键(下降沿触发,按键按下为低电平)                           */
/*   - PB13: KEY2 按键(下降沿触发)                                           */
/*   - PB15: 编码器按键(下降沿触发)                                          */
/*                                                                            */
/* 注意: 此函数在中断上下文中执行,应尽量简短快速,避免阻塞。                    */
/*       实际数据处理(按键动作、超声波距离计算)在 App_Loop() 主循环中完成。    */
/* ========================================================================== */
void App_OnGpioExtiCallback(uint16_t gpio_pin)
{
  uint32_t now = HAL_GetTick();

  switch (gpio_pin)
  {
    /* ---- 超声波回波捕获 ---- */
    /*
     * PA10 连接 HC-SR04 的 ECHO 引脚。
     * 超声波模块原理: TRIG 发 10us 高电平触发,模块自动发送 8 个 40kHz 脉冲,
     * ECHO 引脚输出与距离成正比的高电平脉宽。
     * 距离(cm) = 脉宽(us) / 58
     *
     * 上升沿中断: HCSR04_HandleEchoEdge() 记录开始时间
     * 下降沿中断: 记录结束时间,计算脉宽
     */
    case GPIO_PIN_10:
      /*
       * HAL_GPIO_ReadPin(): 读取当前引脚电平
       * 用于判断当前中断边沿类型:
       * - GPIO_PIN_SET (高电平) => 上升沿,回声开始
       * - GPIO_PIN_RESET (低电平) => 下降沿,回声结束
       */
      HCSR04_HandleEchoEdge(&hcsr04, HAL_GPIO_ReadPin(GPIOA, GPIO_PIN_10));
      break;

    /* ---- KEY1 (PB12): WS2812 模式切换 ---- */
    /*
     * 按键消抖实现:
     * 按键是机械开关,按下/释放时会产生抖动(10~20ms 的多次电平跳变)。
     * 软件消抖原理: 记录上次触发时间,只有间隔 >= BUTTON_DEBOUNCE_MS (150ms)
     * 时才认为是一次有效的按键事件。
     * 这里只设置状态位,具体动作在主循环中处理。
     */
    case GPIO_PIN_12:
      if ((now - key1_last_tick) >= BUTTON_DEBOUNCE_MS)
      {
        key1_last_tick = now;
        button_event_mask |= 0x01U;  /* 设置 KEY1 事件标志位 */
      }
      break;

    /* ---- KEY2 (PB13): OLED 页面切换 ---- */
    case GPIO_PIN_13:
      if ((now - key2_last_tick) >= BUTTON_DEBOUNCE_MS)
      {
        key2_last_tick = now;
        button_event_mask |= 0x02U;  /* 设置 KEY2 事件标志位 */
      }
      break;

    /* ---- 编码器按键 (PB15): 风扇停止 ---- */
    /*
     * 编码器按键没有做软件消抖,因为物理编码器开关通常抖动较小,
     * 且风扇停止功能允许少量误触发(安全考虑)。
     */
    case GPIO_PIN_15:
      button_event_mask |= 0x04U;  /* 设置编码器按键事件标志位 */
      break;

    default:
      break;
  }
}

/* ========================================================================== */
/* 函数: App_OnUartRxEventCallback()                                          */
/* 描述: UART 空闲中断 + DMA/IT 接收回调函数                                  */
/*                                                                            */
/* UART 空闲中断 + DMA 接收机制:                                              */
/*   这是 STM32 高效接收不定长数据的经典方案:                                 */
/*   1. 启动时调用 HAL_UARTEx_ReceiveToIdle_DMA()                             */
/*      让 DMA 自动将 UART 接收到的数据搬运到 rx_buffer                       */
/*   2. 当 UART 总线空闲(无新数据到达超过 1 个字符时间)时触发空闲中断          */
/*   3. 在中断回调中:                                                         */
/*      a. 将 DMA 缓冲区中的数据复制到帧复制缓冲区(rx_frame)                   */
/*      b. 设置帧就绪标志(rx_frame_ready)                                     */
/*      c. 重新启动下一次接收                                                  */
/*                                                                            */
/* 双缓冲设计:                                                                */
/*   rx_buffer 是 DMA 持续写入的"活"缓冲区,                                    */
/*   rx_frame 是主循环消费的"静"缓冲区。                                       */
/*   这样主循环在处理数据时,DMA 可以继续接收新数据到 rx_buffer,                 */
/*   互不干扰。                                `                               */
/*                                                                            */
/* USART2 (调试串口) 和 USART3 (蓝牙) 使用不同的接收模式:                      */
/*   - USART2: 使用 IT (中断) 模式 — 适用于调试,数据量较小                    */
/*   - USART3: 使用 DMA 模式 — 蓝牙数据量可能较大,DMA 减轻 CPU 负担           */
/* ========================================================================== */
void App_OnUartRxEventCallback(UART_HandleTypeDef *huart, uint16_t size)
{
  /* ---- USART2 调试串口接收处理 ---- */
  if ((huart->Instance == USART2) && (size > 0U))
  {
    uint16_t copy_size = size;

    /* 防止复制越界: 限制最大复制字节数不超过帧缓冲区大小 */
    if (copy_size > sizeof(uart2_rx_frame))
    {
      copy_size = sizeof(uart2_rx_frame);
    }

    /*
     * 溢出检测: 如果上一帧还未被主循环处理,
     * 则新数据无法复制到帧缓冲区,只能丢弃并记录溢出计数。
     */
    if (uart2_rx_frame_ready != 0U)
    {
      ++uart2_rx_overrun_count;  /* 丢弃新数据,增加溢出计数 */
    }
    else
    {
      /*
       * 帧复制: 将 DMA 缓冲区的数据复制到帧缓冲区
       * 使用 memcpy() 进行内存复制,效率高
       */
      memcpy(uart2_rx_frame, uart2_rx_buffer, copy_size);
      uart2_rx_frame_size = copy_size;
      uart2_rx_frame_ready = 1U;  /* 通知主循环有新帧待处理 */
    }

    /*
     * 重新启动空闲中断接收:
     * 这里使用 IT (中断) 模式而非 DMA 模式。
     * HAL_UARTEx_ReceiveToIdle_IT() — 以中断方式接收数据,
     * 每次接收一个字节触发一次中断,直到空闲中断触发。
     * 适合调试数据量较小的场景。
     */
    (void)HAL_UARTEx_ReceiveToIdle_IT(&huart2, uart2_rx_buffer, sizeof(uart2_rx_buffer));
  }
  /* ---- USART3 蓝牙模块接收处理 ---- */
  else if ((huart->Instance == USART3) && (size > 0U))
  {
    uint16_t copy_size = size;

    if (copy_size > sizeof(uart3_rx_frame))
    {
      copy_size = sizeof(uart3_rx_frame);
    }

    if (uart3_rx_frame_ready != 0U)
    {
      ++uart3_rx_overrun_count;
    }
    else
    {
      memcpy(uart3_rx_frame, uart3_rx_buffer, copy_size);
      uart3_rx_frame_size = copy_size;
      uart3_rx_frame_ready = 1U;
      /* 更新 BT24 收发统计和显示缓冲区 */
      bt24_rx_total_bytes += copy_size;            /* 累计接收字节数 */
      UpdateBt24LastText(uart3_rx_frame, copy_size);  /* 提取可打印文本 */
      UpdateBt24LastHex(uart3_rx_frame, copy_size);   /* 生成十六进制显示 */
    }

    /*
     * 重新启动 DMA 空闲中断接收:
     * HAL_UARTEx_ReceiveToIdle_DMA() — 使用 DMA 自动搬运数据,
     * 只有空闲中断时才触发回调,降低 CPU 占用率。
     *
     * __HAL_DMA_DISABLE_IT(huart3.hdmarx, DMA_IT_HT):
     * 禁用 DMA 半传输中断 (Half-Transfer)。
     * 在环形缓冲区模式下,DMA 在填满一半和全部填满时都会触发 HT 和 TC 中断。
     * 我们只关心空闲中断,不需要 HT 中断,禁用以减少不必要的中断处理。
     */
    (void)HAL_UARTEx_ReceiveToIdle_DMA(&huart3, uart3_rx_buffer, sizeof(uart3_rx_buffer));
    __HAL_DMA_DISABLE_IT(huart3.hdmarx, DMA_IT_HT);
  }
}

/* ========================================================================== */
/* 函数: ConfigureNtcAdcSingleChannel()                                       */
/* 描述: 将 ADC1 配置为单通道单次转换模式,专用于 NTC 热敏电阻采样              */
/*                                                                            */
/* ADC 配置详解:                                                              */
/*   - ScanConvMode = DISABLE:  非扫描模式,只转换一个通道                     */
/*     扫描模式用于多通道顺序转换,单通道不需要                                */
/*   - ContinuousConvMode = DISABLE:  单次转换模式                            */
/*     每次需软件触发启动转换,转换完自动停止                                 */
/*   - ExternalTrigConv = ADC_SOFTWARE_START:  软件触发(非定时器触发)         */
/*     调用 HAL_ADC_Start() 启动转换                                         */
/*   - DataAlign = ADC_DATAALIGN_RIGHT:  数据右对齐                          */
/*     STM32F1 的 ADC 是 12 位分辨率,结果存储在 16 位寄存器的低 12 位         */
/*                                                                            */
/*   NTC 测量通道:                                                            */
/*   - Channel = ADC_CHANNEL_4:  对应 PA4 引脚                               */
/*   - SamplingTime = ADC_SAMPLETIME_239CYCLES_5:  最长采样时间              */
/*     239.5 个 ADC 时钟周期,约 5.35us (ADC 时钟 14MHz @APB2=72MHz, 分频6)   */
/*     长采样时间确保 NTC 分压电路中电容充放电完全,采样值稳定。               */
/* ========================================================================== */
static void ConfigureNtcAdcSingleChannel(void)
{
  ADC_ChannelConfTypeDef sConfig = {0};

  /* 配置 ADC1 为单通道单次转换模式 */
  hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;        /* 禁用扫描模式 */
  hadc1.Init.NbrOfConversion = 1;                     /* 转换序列长度 = 1 */
  hadc1.Init.ContinuousConvMode = DISABLE;            /* 禁用连续转换 */
  hadc1.Init.DiscontinuousConvMode = DISABLE;         /* 禁用间断模式 */
  hadc1.Init.ExternalTrigConv = ADC_SOFTWARE_START;   /* 软件触发 */
  hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;         /* 数据右对齐 */

  /* 应用配置到 ADC1,若失败则触发错误处理 */
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }

  /* 配置通道 4 (PA4) 为第一个转换通道 */
  sConfig.Channel = ADC_CHANNEL_4;          /* ADC 输入通道 4 = PA4 */
  sConfig.Rank = ADC_REGULAR_RANK_1;        /* 规则组中的第 1 个(也是唯一一个) */
  sConfig.SamplingTime = ADC_SAMPLETIME_239CYCLES_5;  /* 最长采样时间,提高精度 */

  if (HAL_ADC_ConfigChannel(&hadc1, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }
}

/* ========================================================================== */
/* 函数: StartPeripherals()                                                   */
/* 描述: 启动各定时器外设并设置初始 PWM 占空比                                */
/*                                                                            */
/* 启动的外设:                                                                */
/*   - TIM1: 编码器模式,用于读取电机转速                                     */
/*   - TIM2 CH1/CH2: 双路 PWM,控制 DRV8833 电机驱动                          */
/*   - TIM4 CH3: PWM,控制舵机                                                */
/*                                                                            */
/* 编码器模式原理:                                                            */
/*   TIM1 配置为编码器模式,CH1(PA8)和 CH2(PA9)连接编码器的 A/B 相输出。       */
/*   定时器自动根据两相信号的相位差和脉冲数计算旋转方向和角度。                */
/*   计数值递增 = 正转,递减 = 反转。                                          */
/* ========================================================================== */
static void StartPeripherals(void)
{
  /*
   * HAL_TIM_Encoder_Start(): 启动定时器编码器模式
   * TIM_CHANNEL_ALL: 同时使能通道1和2 (CH1=PA8, CH2=PA9)
   * 编码器值可通过 __HAL_TIM_GET_COUNTER(&htim1) 读取
   */
  HAL_TIM_Encoder_Start(&htim1, TIM_CHANNEL_ALL);

  /*
   * HAL_TIM_PWM_Start(): 启动定时器 PWM 输出
   * TIM2 CH1 (PA0): DRV8833 电机 PWM A
   * TIM2 CH2 (PA1): DRV8833 电机 PWM B
   * TIM4 CH3 (PB0): 舵机控制 (注意: PB0 随后会被重配为 GPIO)
   */
  HAL_TIM_PWM_Start(&htim2, TIM_CHANNEL_1);
  HAL_TIM_PWM_Start(&htim2, TIM_CHANNEL_2);
  HAL_TIM_PWM_Start(&htim4, TIM_CHANNEL_3);

  /* 设置初始占空比 (通过 CCR 寄存器) */
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_1, 0);  /* 电机 PWM A = 0 (停止) */
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_2, 0);  /* 电机 PWM B = 0 (停止) */
  __HAL_TIM_SET_COMPARE(&htim4, TIM_CHANNEL_3, 1500); /* 舵机中位 1500us */
}

/* ========================================================================== */
/* 函数: StartDebugUartReception()                                            */
/* 描述: 启动两个 UART 的接收                                                */
/*                                                                            */
/* USART2 使用中断模式(IT),USART3 使用 DMA 模式:                              */
/* - IT 模式: 每个字节都产生中断,CPU 占用率高但延迟低                         */
/* - DMA 模式: DMA 自动搬运数据,空闲中断时才触发回调,CPU 占用率低             */
/* ========================================================================== */
static void StartDebugUartReception(void)
{
  /*
   * HAL_UARTEx_ReceiveToIdle_IT(): 以"空闲中断 + 中断"方式启动接收
   * 数据通过中断逐个字节接收,总线空闲时触发回调
   */
  (void)HAL_UARTEx_ReceiveToIdle_IT(&huart2, uart2_rx_buffer, sizeof(uart2_rx_buffer));

  /*
   * HAL_UARTEx_ReceiveToIdle_DMA(): 以"空闲中断 + DMA"方式启动接收
   * DMA 在后台自动将 UART 数据搬运到缓冲区,不占用 CPU
   */
  (void)HAL_UARTEx_ReceiveToIdle_DMA(&huart3, uart3_rx_buffer, sizeof(uart3_rx_buffer));

  /* 禁用 DMA 半传输中断(HT),减少不必要的 CPU 中断 */
  __HAL_DMA_DISABLE_IT(huart3.hdmarx, DMA_IT_HT);
}

/* ========================================================================== */
/* 函数: DebugPrint()                                                         */
/* 描述: 通过 USART2 向调试串口发送字符串                                     */
/*                                                                            */
/* HAL_UART_Transmit() 参数:                                                  */
/*   - &huart2: USART2 句柄                                                  */
/*   - data: 要发送的数据指针                                                 */
/*   - size: 数据长度(字节)                                                   */
/*   - timeout: 超时时间, HAL_MAX_DELAY 表示无限等待                          */
/* ========================================================================== */
static void DebugPrint(const char *text)
{
  HAL_UART_Transmit(&huart2, (uint8_t *)text, (uint16_t)strlen(text), HAL_MAX_DELAY);
}

/* ========================================================================== */
/* 函数: DebugPrintf()                                                        */
/* 描述: 格式化打印到调试串口,功能类似 printf()                               */
/*                                                                            */
/* 实现: 使用 vsnprintf() 将格式化字符串写入本地缓冲区,再通过 DebugPrint()     */
/* 发送到串口。缓冲区大小 192 字节,超过部分被截断。                            */
/* ========================================================================== */
static void DebugPrintf(const char *fmt, ...)
{
  char buffer[192];
  va_list args;
  int length;

  va_start(args, fmt);
  length = vsnprintf(buffer, sizeof(buffer), fmt, args);
  va_end(args);

  if (length <= 0)
  {
    return;  /* 格式化失败或为空串 */
  }

  if (length > (int)sizeof(buffer))
  {
    length = (int)sizeof(buffer) - 1;  /* 截断过长内容 */
  }

  HAL_UART_Transmit(&huart2, (uint8_t *)buffer, (uint16_t)length, HAL_MAX_DELAY);
}

/* ========================================================================== */
/* 函数: ReadAdcChannel()                                                     */
/* 描述: 读取指定 ADC 通道的电压值(单次采样,2 次平均)                        */
/*                                                                            */
/* ADC 单次采样流程:                                                           */
/*   1. 配置目标通道 (Channel, Rank, SamplingTime)                            */
/*   2. HAL_ADC_Start() — 启动 ADC 转换 (软件触发)                           */
/*   3. HAL_ADC_PollForConversion() — 轮询等待转换完成                        */
/*   4. HAL_ADC_GetValue() — 读取转换结果(12 位, 0~4095)                     */
/*   5. HAL_ADC_Stop() — 停止 ADC(连续模式已禁用,但显式停止确保状态复位)      */
/*                                                                            */
/* 为什么采样 2 次:                                                            */
/*   单次采样可能受噪声影响,连采 2 次取最后一次结果(第一次采样的结果丢弃       */
/*   效果,因为第一次采样时通道电容可能未完全建立)。这里实际并没有平均,          */
/*   只返回最后一次采样的值作为"较稳定"的结果。                                 */
/* ========================================================================== */
static uint16_t ReadAdcChannel(uint32_t channel)
{
  ADC_ChannelConfTypeDef sConfig = {0};
  uint16_t value = 0U;
  uint8_t sample_index;

  /* 配置 ADC 通道 */
  sConfig.Channel = channel;                        /* 目标通道号 */
  sConfig.Rank = ADC_REGULAR_RANK_1;                /* 规则组第 1 个 */
  sConfig.SamplingTime = ADC_SAMPLETIME_239CYCLES_5; /* 最长采样时间 */

  if (HAL_ADC_ConfigChannel(&hadc1, &sConfig) != HAL_OK)
  {
    return 0U;  /* 配置失败 */
  }

  /*
   * 连续采样 2 次,取最后一次的值
   * 第一次采样的结果稳定通道内部的采样电容
   */
  for (sample_index = 0U; sample_index < 2U; ++sample_index)
  {
    if (HAL_ADC_Start(&hadc1) != HAL_OK)
    {
      return 0U;  /* 启动失败 */
    }

    /*
     * PollForConversion: 轮询等待转换完成
     * 超时时间 10ms,若超时则跳过本次读取
     */
    if (HAL_ADC_PollForConversion(&hadc1, 10U) == HAL_OK)
    {
      value = (uint16_t)HAL_ADC_GetValue(&hadc1);  /* 读取 12 位 ADC 值 */
    }

    HAL_ADC_Stop(&hadc1);  /* 停止转换 */
  }

  return value;
}

/* ========================================================================== */
/* 函数: SampleAdc()                                                          */
/* 描述: 采集 NTC 热敏电阻通道的 ADC 原始值                                    */
/* @param ntc_raw  输出: 12 位 ADC 原始值(0~4095)                            */
/* ========================================================================== */
static void SampleAdc(uint16_t *ntc_raw)
{
  /* NTC 连接在 ADC1 的通道 4 (PA4) 上 */
  *ntc_raw = ReadAdcChannel(ADC_CHANNEL_4);
}

/* ========================================================================== */
/* 函数: TriangleWave()                                                       */
/* 描述: 生成三角波(对称锯齿波)信号值                                        */
/*                                                                            */
/* 三角波算法:                                                                */
/*   输入: 当前时间 tick, 周期 period_ms, 幅值 amplitude                      */
/*   输出: 0 到 amplitude 之间线性上升/下降的值                               */
/*                                                                            */
/*   在 0 ~ period_ms/2 期间: 从 0 线性增加到 amplitude                       */
/*   在 period_ms/2 ~ period_ms 期间: 从 amplitude 线性减小到 0               */
/*                                                                            */
/*   应用: WS2812 呼吸灯效果中,用三角波控制 LED 亮度                          */
/* ========================================================================== */
static uint32_t TriangleWave(uint32_t tick, uint32_t period_ms, uint32_t amplitude)
{
  uint32_t phase;
  uint32_t half_period;

  if ((period_ms == 0U) || (amplitude == 0U))
  {
    return 0U;
  }

  phase = tick % period_ms;       /* 当前时刻在一个周期内的相位(0 ~ period_ms-1) */
  half_period = period_ms / 2U;   /* 半周期长度 */

  if (half_period == 0U)
  {
    return amplitude;
  }

  if (phase < half_period)
  {
    /* 上升段: 从 0 线性增加到 amplitude */
    return (phase * amplitude) / half_period;
  }

  /* 下降段: 从 amplitude 线性减小到 0 */
  return ((period_ms - phase) * amplitude) / half_period;
}

/* ========================================================================== */
/* 函数: SmoothLevel()                                                        */
/* 描述: 对线性值应用 smoothstep 平滑曲线                                     */
/*                                                                            */
/* 算法: Smoothstep (平滑阶跃函数)                                             */
/*   smoothstep(t) = 3t² - 2t³, 其中 t 从 0 到 1                             */
/*   特性: 在 t=0 和 t=1 处斜率为 0,实现平滑起止                            */
/*   用途: 呼吸灯效果中,让亮度变化更加自然柔              和,而非线性跳变       */
/*                                                                            */
/* 实现: 使用定点数运算(Q16格式),避免浮点计算                                 */
/* ========================================================================== */
static uint32_t SmoothLevel(uint32_t linear_level, uint32_t amplitude)
{
  uint64_t x;
  uint64_t x2;
  uint64_t x3;
  uint64_t smooth;

  if (amplitude == 0U)
  {
    return 0U;
  }

  if (linear_level >= amplitude)
  {
    return amplitude;
  }

  /*
   * 将 linear_level/amplitude 归一化到 Q16 定点数 (1.0 = 1<<16 = 65536)
   * x = linear_level / amplitude, 以 Q16 格式表示
   */
  x = ((uint64_t)linear_level << 16) / amplitude;
  x2 = (x * x) >> 16;           /* x² */
  x3 = (x2 * x) >> 16;          /* x³ */

  /* smooth = 3*x² - 2*x³ (smoothstep 公式) */
  smooth = (3ULL * x2) - (2ULL * x3);

  /* 将结果映射回原始幅值范围 */
  return (uint32_t)((smooth * amplitude) >> 16);
}

/* ========================================================================== */
/* 函数: ColorWheel()                                                         */
/* 描述: 将 HSV 色环中的 H(色相)值转换为 RGB 值                              */
/*                                                                            */
/* 色环算法:                                                                  */
/*   色环将 360° 分为 6 段(每段 60°),每段中两个颜色通道渐变,一个固定:          */
/*   段 0:  R=255,  G=0→255,   B=0    (红→黄)                                */
/*   段 1:  R=255→0, G=255,    B=0    (黄→绿)                                */
/*   段 2:  R=0,    G=255,    B=0→255  (绿→青)                               */
/*   段 3:  R=0,    G=255→0,  B=255   (青→蓝)                                */
/*   段 4:  R=0→255, G=0,     B=255   (蓝→紫)                                */
/*   段 5:  R=255,  G=0,      B=255→0 (紫→红)                                */
/*                                                                            */
/*   输入 hue 范围: 0~1535 (对应 0°~360°, 每 256 步为 60°)                   */
/*   应用: WS2812 彩虹模式中,为不同 LED 分配不同色相值                        */
/* ========================================================================== */
static void ColorWheel(uint32_t hue, uint8_t *red, uint8_t *green, uint8_t *blue)
{
  /*
   * section: 色段索引(0~5)
   * offset:  在当前段内的偏移(0~255)
   * ramp:    渐变值,从 0 到 255 递增
   */
  uint32_t section = (hue / 256U) % 6U;  /* 6 段,每段 256 步 */
  uint32_t offset = hue % 256U;          /* 当前段内的偏移 */
  uint32_t ramp = offset & 0xFFU;        /* 0~255 渐变值 */

  switch (section)
  {
    case 0:
      *red = 255U;            /* R 满 */
      *green = (uint8_t)ramp; /* G 从 0 到 255 递增 */
      *blue = 0U;             /* B 关 */
      break;

    case 1:
      *red = (uint8_t)(255U - ramp);  /* R 从 255 到 0 递减 */
      *green = 255U;                  /* G 满 */
      *blue = 0U;                     /* B 关 */
      break;

    case 2:
      *red = 0U;
      *green = 255U;
      *blue = (uint8_t)ramp;          /* B 从 0 到 255 递增 */
      break;

    case 3:
      *red = 0U;
      *green = (uint8_t)(255U - ramp); /* G 从 255 到 0 递减 */
      *blue = 255U;                     /* B 满 */
      break;

    case 4:
      *red = (uint8_t)ramp;            /* R 从 0 到 255 递增 */
      *green = 0U;
      *blue = 255U;                    /* B 满 */
      break;

    default: /* case 5 */
      *red = 255U;                     /* R 满 */
      *green = 0U;
      *blue = (uint8_t)(255U - ramp);  /* B 从 255 到 0 递减 */
      break;
  }
}

/* ========================================================================== */
/* 函数: Bt24SendString()                                                     */
/* 描述: 通过 BT24 蓝牙模块发送字符串,同时更新发送字节统计                     */
/* @return HAL_OK 表示发送成功                                                */
/* ========================================================================== */
static HAL_StatusTypeDef Bt24SendString(const char *text)
{
  /*
   * DX_BT24_SendString(): 使用 BT24 驱动发送字符串
   * 参数3: HAL_MAX_DELAY — 无限等待发送完成
   */
  HAL_StatusTypeDef status = DX_BT24_SendString(&bt24, text, HAL_MAX_DELAY);

  if (status == HAL_OK)
  {
    bt24_tx_total_bytes += (uint32_t)strlen(text);  /* 更新发送统计 */
  }

  return status;
}

/* ========================================================================== */
/* 函数: Bt24Reply()                                                          */
/* 描述: 格式化回复消息到 BT24 蓝牙,类似 Bt24Reply("OK %d", value) 的用法    */
/* ========================================================================== */
static void Bt24Reply(const char *fmt, ...)
{
  char buffer[192];
  va_list args;
  int length;
  HAL_StatusTypeDef status;

  va_start(args, fmt);
  length = vsnprintf(buffer, sizeof(buffer), fmt, args);
  va_end(args);

  if (length > 0)
  {
    status = Bt24SendString(buffer);
    if (status != HAL_OK)
    {
      /* 蓝牙发送失败时,输出调试信息到串口 */
      DebugPrintf("bt24 reply send failed: %d\r\n", (int)status);
    }
  }
}

/* ========================================================================== */
/* 函数: CycleOledPage()                                                      */
/* 描述: OLED 页面循环切换: STATUS → SENSOR → BT24 → STATUS ...              */
/* ========================================================================== */
static void CycleOledPage(void)
{
  /* 3 个页面循环: 当前页 + 1 对 3 取模 */
  oled_page = (OledPage_t)(((uint32_t)oled_page + 1U) % 3U);
}

/* ========================================================================== */
/* 函数: SetOledPage()                                                        */
/* 描述: 直接设置 OLED 显示页面                                              */
/* ========================================================================== */
static void SetOledPage(OledPage_t page)
{
  oled_page = page;
}

/* ========================================================================== */
/* 函数: SetFanSpeedPercent()                                                 */
/* 描述: 设置风扇目标速度百分比,带启动力矩(boost)控制                         */
/*                                                                            */
/* 启动力矩逻辑:                                                              */
/*   风扇电机从静止启动时,需要较大的力矩克服静摩擦力矩。                       */
/*   当目标速度从 0→正数时,设置 boost_until_tick 为当前时间 + 300ms,           */
/*   在这 300ms 内电机将全速运转(100%)以克服静摩擦。                            */
/*   300ms 后恢复为正常的目标速度。                                            */
/*                                                                            */
/* 编码器基准重置:                                                             */
/*   每次设置风扇速度时同步重置编码器基准值,                                   */
/*   防止在调用 SetFanSpeedPercent() 时因编码器累计误差导致速度突变。            */
/* ========================================================================== */
static void SetFanSpeedPercent(int16_t speed_percent)
{
  uint32_t now = HAL_GetTick();

  /* 限幅: 速度范围 -100~100% (负值=反转) */
  if (speed_percent < -100)
  {
    speed_percent = -100;
  }
  else if (speed_percent > 100)
  {
    speed_percent = 100;
  }

  /* 从停止到启动: 设置启动力矩 */
  if ((motor_target_speed_percent == 0) && (speed_percent > 0))
  {
    motor_boost_until_tick = now + FAN_START_BOOST_MS;  /* 300ms 全速 */
  }
  /* 停止: 清除启动力矩 */
  else if (speed_percent == 0)
  {
    motor_boost_until_tick = 0U;
  }

  motor_target_speed_percent = speed_percent;  /* 更新目标速度 */
  /* 重置编码器基准值,防止速度突变 */
  encoder_last_count = (int16_t)__HAL_TIM_GET_COUNTER(&htim1);
}

/* ========================================================================== */
/* 函数: TryParseInt32()                                                      */
/* 描述: 安全地将字符串解析为 32 位整数,带错误检测                             */
/*                                                                            */
/* 使用 strtol() 进行解析,提供完善的错误检查:                                  */
/*   - 空字符串或 NULL 指针 → 返回 0                                         */
/*   - 解析后指针未移动(无数字) → 返回 0                                     */
/*   - 解析后指针未到末尾(有多余字符) → 返回 0                               */
/*   回调函数: App_OnUartRxEventCallback                                      */
/*                                                                            */
/* @return 1=解析成功, 0=解析失败                                              */
/* ========================================================================== */
static uint8_t TryParseInt32(const char *text, int32_t *value)
{
  char *end = NULL;
  long parsed;

  if ((text == NULL) || (value == NULL) || (*text == '\0'))
  {
    return 0U;  /* 无效输入 */
  }

  /*
   * strtol(): string to long — C 标准库字符串转长整型
   * 参数1: 待解析的字符串
   * 参数2: end — 解析结束后指向未被解析的第一个字符
   * 参数3: 10 — 十进制
   *
   * 错误检测:
   * - end == text: 没有解析出任何数字
   * - *end != '\0': 字符串末尾有多余字符(如 "123abc")
   */
  parsed = strtol(text, &end, 10);

  if ((end == text) || ((end != NULL) && (*end != '\0')))
  {
    return 0U;
  }

  *value = (int32_t)parsed;
  return 1U;
}

/* ========================================================================== */
/* 函数: NormalizeBt24Command()                                               */
/* 描述: 规范化 BT24 蓝牙接收到的命令字符串                                    */
/*                                                                            */
/* 规范化处理:                                                                */
/*   1. 大写转小写: 'A'~'Z' → 'a'~'z'                                       */
/*   2. 空白字符归一化: \r \n \t 全部替换为空格                               */
/*   3. 连续空格合并: 多个连续空格压缩为单个                                  */
/*   4. 去除首尾空格                                                          */
/*                                                                            */
/* 目的: 让蓝牙命令解析更加鲁棒,不区分大小写,容忍多余空白字符                  */
/* ========================================================================== */
static void NormalizeBt24Command(const uint8_t *data, uint16_t size, char *buffer, size_t buffer_size)
{
  size_t src_index = 0U;
  size_t dst_index = 0U;
  uint8_t previous_space = 1U;  /* 标记上一个字符是否为空格,初始为1以去除前导空格 */

  if ((buffer == NULL) || (buffer_size == 0U))
  {
    return;
  }

  while ((src_index < size) && (dst_index < (buffer_size - 1U)))
  {
    char ch = (char)data[src_index++];

    /* 大写转小写 */
    if ((ch >= 'A') && (ch <= 'Z'))
    {
      ch = (char)(ch - 'A' + 'a');
    }

    /* 换行/回车/制表符统一替换为空格 */
    if ((ch == '\r') || (ch == '\n') || (ch == '\t'))
    {
      ch = ' ';
    }

    if (ch == ' ')
    {
      /* 连续空格: 只保留第一个,跳过后续空格 */
      if (previous_space != 0U)
      {
        continue;  /* 已有空格,跳过 */
      }

      previous_space = 1U;
      buffer[dst_index++] = ' ';
    }
    else
    {
      previous_space = 0U;
      buffer[dst_index++] = ch;
    }
  }

  /* 去除末尾空格 */
  if ((dst_index > 0U) && (buffer[dst_index - 1U] == ' '))
  {
    --dst_index;
  }

  buffer[dst_index] = '\0';  /* 字符串终止 */
}

/* ========================================================================== */
/* 函数: CondenseRepeatedBt24Command()                                        */
/* 描述: 检测并压缩 BT24 命令中的重复模式                                     */
/*                                                                            */
/* 原理: 检查命令字符串是否由某个子串重复多次构成                             */
/*   例如: "fafafa" → "fa" (重复3次)                                         */
/*         "abcabc"  → "abc" (重复2次)                                       */
/*                                                                            */
/* 目的: 有些蓝牙遥控器或键盘在按键长按时会快速重复发送同样的命令,             */
/*       压缩重复命令可以减少不必要的处理。                                     */
/*                                                                            */
/* @return 1=找到并压缩了重复, 0=无重复                                        */
/* ========================================================================== */
static uint8_t CondenseRepeatedBt24Command(const char *command, char *buffer, size_t buffer_size)
{
  size_t command_len;
  size_t base_len;

  if ((command == NULL) || (buffer == NULL) || (buffer_size == 0U))
  {
    return 0U;
  }

  command_len = strlen(command);
  if (command_len == 0U)
  {
    return 0U;
  }

  /*
   * 尝试每种可能的基串长度(从1到命令长度的一半)
   * 因为重复至少2次,所以基串长度 <= command_len/2
   */
  for (base_len = 1U; base_len <= (command_len / 2U); ++base_len)
  {
    size_t repeat_index;

    /* 命令长度必须能被基串长度整除 */
    if ((command_len % base_len) != 0U)
    {
      continue;
    }

    /* 检查是否每个基串长度的块都相同 */
    for (repeat_index = base_len; repeat_index < command_len; repeat_index += base_len)
    {
      if (strncmp(command, command + repeat_index, base_len) != 0)
      {
        break;
      }
    }

    /* 所有块都匹配: 发现重复模式 */
    if (repeat_index >= command_len)
    {
      size_t copy_len = base_len;

      if (copy_len >= buffer_size)
      {
        copy_len = buffer_size - 1U;
      }

      memcpy(buffer, command, copy_len);
      buffer[copy_len] = '\0';
      return 1U;
    }
  }

  return 0U;
}

/* ========================================================================== */
/* 函数: FindBt24CommandBoundary()                                            */
/* 描述: 在命令流中查找下一个命令的起始边界                                   */
/*                                                                            */
/* 通过模式匹配查找已知命令关键字:                                            */
/*   常见命令: status, fan, ws, page, help, ?                                 */
/*   并优先匹配较长的命令(如 "fan stop" 优先于 "fan ")                        */
/*                                                                            */
/* @return 找到的边界索引(>=0), 未找到返回 -1                                 */
/* ========================================================================== */
static int FindBt24CommandBoundary(const char *text, size_t start_index)
{
  /*
   * 已知命令模式列表:
   * 按从长到短排列,确保优先匹配完整命令
   * 例如 "fan stop" 在 "fan " 之前,避免将 "fan stop" 误拆分为 "fan"+"stop"
   */
  static const char *patterns[] =
  {
    "status",
    "fan stop",
    "fan ",
    "fan",
    "ws next",
    "ws rainbow",
    "ws breath",
    "ws chase",
    "ws comet",
    "ws ",
    "ws",
    "page next",
    "page status",
    "page sensor",
    "page bt24",
    "page bt",
    "page ",
    "page",
    "help",
    "?"
  };
  size_t text_len;
  size_t index;

  if (text == NULL)
  {
    return -1;
  }

  text_len = strlen(text);
  if (start_index >= text_len)
  {
    return -1;
  }

  /*
   * 从 start_index + 1 开始查找下一个命令边界
   * (start_index 处是当前命令,所以我们向后查找)
   */
  for (index = start_index + 1U; index < text_len; ++index)
  {
    size_t pattern_index;

    /* 检查当前位置是否匹配任何一个已知命令模式 */
    for (pattern_index = 0U; pattern_index < (sizeof(patterns) / sizeof(patterns[0])); ++pattern_index)
    {
      size_t pattern_len = strlen(patterns[pattern_index]);

      /* 检查当前位置开始的子串是否匹配模式 */
      if ((pattern_len > 0U) && ((index + pattern_len) <= text_len) &&
          (strncmp(text + index, patterns[pattern_index], pattern_len) == 0))
      {
        return (int)index;  /* 找到边界 */
      }
    }
  }

  return -1;  /* 未找到更多命令 */
}

/* ========================================================================== */
/* 函数: ExecuteBt24CommandStream()                                           */
/* 描述: 执行 BT24 蓝牙命令流(可能包含多个连续的命令)                         */
/*                                                                            */
/* 命令流解析流程:                                                            */
/*   1. 跳过前导空格                                                          */
/*   2. 调用 FindBt24CommandBoundary() 找到下一个命令的边界                    */
/*   3. 提取当前命令(去除边界内的前/后空格)                                  */
/*   4. 调用 HandleBt24Command() 执行命令                                     */
/*   5. 跳到边界位置,继续解析下一个命令                                       */
/*   6. 重复直到命令流结束                                                    */
/*                                                                            */
/* 目的: 支持一次性发送多个命令,如 "fan 50 ws rainbow" 会依次执行              */
/* @return 1=至少处理了一个命令, 0=没有可处理的命令                            */
/* ========================================================================== */
static uint8_t ExecuteBt24CommandStream(const char *command_stream)
{
  char command[80];               /* 当前提取的命令缓冲区 */
  size_t stream_len;
  size_t start_index = 0U;        /* 当前处理位置 */
  uint8_t handled_any = 0U;       /* 是否至少处理了一个命令 */

  if ((command_stream == NULL) || (*command_stream == '\0'))
  {
    return 0U;
  }

  stream_len = strlen(command_stream);

  /* 循环处理命令流中的每个命令 */
  while (start_index < stream_len)
  {
    /*
     * 查找下一个命令的边界:
     * 如果找到,则当前命令从 start_index 到 boundary;
     * 如果没找到,则从 start_index 到末尾都是当前命令。
     */
    int boundary = FindBt24CommandBoundary(command_stream, start_index);
    size_t command_len;
    size_t copy_len;

    /* 跳过前导空格 */
    while ((start_index < stream_len) && (command_stream[start_index] == ' '))
    {
      ++start_index;
    }

    if (start_index >= stream_len)
    {
      break;
    }

    /* 计算命令长度 */
    if (boundary < 0)
    {
      command_len = stream_len - start_index;  /* 最后一个命令: 到字符串末尾 */
    }
    else
    {
      command_len = (size_t)boundary - start_index;
    }

    /* 去除命令末尾的空格 */
    while ((command_len > 0U) && (command_stream[start_index + command_len - 1U] == ' '))
    {
      --command_len;
    }

    /* 空命令(只有空格): 跳过 */
    if (command_len == 0U)
    {
      if (boundary < 0)
      {
        break;  /* 最后一段全是空格,结束 */
      }

      start_index = (size_t)boundary;
      continue;
    }

    /* 复制命令到本地缓冲区(防止越界) */
    copy_len = command_len;
    if (copy_len >= sizeof(command))
    {
      copy_len = sizeof(command) - 1U;
    }

    memcpy(command, command_stream + start_index, copy_len);
    command[copy_len] = '\0';

    /* 执行命令 */
    if (HandleBt24Command(command) != 0U)
    {
      handled_any = 1U;
    }

    if (boundary < 0)
    {
      break;
    }

    start_index = (size_t)boundary;  /* 移动到下一个命令的起始位置 */
  }

  return handled_any;
}

/* ========================================================================== */
/* 函数: HandleBt24Command()                                                  */
/* 描述: 解析并执行单条 BT24 蓝牙命令                                        */
/*                                                                            */
/* 支持的命令列表:                                                             */
/*   help / ?          — 显示帮助信息                                         */
/*   status            — 返回系统状态(风扇速度、灯带模式、页面、传感器数据)    */
/*   fan <0-100>       — 设置风扇速度百分比                                   */
/*   fan stop / fanstop — 停止风扇                                            */
/*   ws next / wsnext  — 切换灯带到下一个模式                                */
/*   ws rainbow        — 设置灯带为彩虹模式                                  */
/*   ws breath         — 设置灯带为呼吸灯模式                                */
/*   ws chase          — 设置灯带为追逐模式                                  */
/*   ws comet          — 设置灯带为彗星模式                                  */
/*   page next / pagenext      — OLED 页面切换到下一页                       */
/*   page status / pagestatus  — OLED 页面切换到状态页                       */
/*   page sensor / pagesensor  — OLED 页面切换到传感器页                     */
/*   page bt / pagebt / pagebt24 — OLED 页面切换到蓝牙页                     */
/* ========================================================================== */
static uint8_t HandleBt24Command(const char *command)
{
  char condensed_command[80];  /* 压缩后的命令缓冲区 */
  int32_t value = 0;

  if ((command == NULL) || (*command == '\0'))
  {
    return 0U;
  }

  /* 尝试压缩重复命令(如 "fafafa" → "fa") */
  if (CondenseRepeatedBt24Command(command, condensed_command, sizeof(condensed_command)) != 0U)
  {
    DebugPrintf("bt24 cmd condensed: %s -> %s\r\n", command, condensed_command);
    command = condensed_command;
  }

  DebugPrintf("bt24 cmd: %s\r\n", command);

  /* ---- help / ? : 显示帮助信息 ---- */
  if ((strcmp(command, "help") == 0) || (strcmp(command, "?") == 0))
  {
    Bt24Reply("OK cmds: status, fan <0-100>, fan stop, ws next|rainbow|breath|chase|comet, page next|status|sensor|bt\r\n");
    return 1U;
  }

  /* ---- status : 返回系统状态 ---- */
  if (strcmp(command, "status") == 0)
  {
    Bt24Reply("OK status fan=%d ws=%s page=%s dist=%.1f ntc=%.1f aht=%.1f/%.1f\r\n",
              motor_target_speed_percent,
              WS2812_GetModeName(WS2812_GetMode(&ws2812)),
              GetOledPageName(),
              HCSR04_GetDistanceCm(&hcsr04),
              ntc_data.temperature_c,
              aht20_data.temperature_c,
              aht20_data.humidity_rh);
    return 1U;
  }

  /* ---- fan stop / fanstop : 停止风扇 ---- */
  if ((strcmp(command, "fan stop") == 0) || (strcmp(command, "fanstop") == 0))
  {
    SetFanSpeedPercent(0);
    DebugPrintf("bt24 action: fan=%d\r\n", motor_target_speed_percent);
    Bt24Reply("OK fan=0\r\n");
    return 1U;
  }

  /* ---- fan <0-100> : 设置风扇速度 ---- */
  /*
   * 匹配 "fan " 前缀(注意有空格),后面跟数字
   * 例如 "fan 50" → 设置风扇为 50%
   */
  if (strncmp(command, "fan ", 4U) == 0)
  {
    if (TryParseInt32(command + 4U, &value) != 0U)
    {
      if ((value >= -100) && (value <= 100))
      {
        SetFanSpeedPercent((int16_t)value);
        DebugPrintf("bt24 action: fan=%d\r\n", motor_target_speed_percent);
        Bt24Reply("OK fan=%ld\r\n", value);
      }
      else
      {
        Bt24Reply("ERR fan range -100-100\r\n");
      }
    }
    else
    {
      Bt24Reply("ERR fan value\r\n");
    }

    return 1U;
  }

  /* ---- fan<0-100> : 无空格的风扇命令 ---- */
  /*
   * 匹配 "fan" 前缀(无空格),后面直接跟数字
   * 例如 "fan50" → 设置风扇为 50%
   */
  if (strncmp(command, "fan", 3U) == 0)
  {
    if (TryParseInt32(command + 3U, &value) != 0U)
    {
      if ((value >= -100) && (value <= 100))
      {
        SetFanSpeedPercent((int16_t)value);
        DebugPrintf("bt24 action: fan=%d\r\n", motor_target_speed_percent);
        Bt24Reply("OK fan=%ld\r\n", value);
      }
      else
      {
        Bt24Reply("ERR fan range -100-100\r\n");
      }
    }
    else
    {
      Bt24Reply("ERR fan value\r\n");
    }

    return 1U;
  }

  /* ---- ws next / wsnext : 切换到下一个灯带模式 ---- */
  if ((strcmp(command, "ws next") == 0) || (strcmp(command, "wsnext") == 0))
  {
    WS2812_SetMode(&ws2812, (WS2812_Mode_t)(((uint32_t)WS2812_GetMode(&ws2812) + 1U) % WS2812_MODE_COUNT));
    DebugPrintf("bt24 action: ws=%s\r\n", WS2812_GetModeName(WS2812_GetMode(&ws2812)));
    Bt24Reply("OK ws=%s\r\n", WS2812_GetModeName(WS2812_GetMode(&ws2812)));
    return 1U;
  }

  /* ---- ws <mode> : 设置指定灯带模式 ---- */
  if (strncmp(command, "ws ", 3U) == 0)
  {
    const char *mode_name = command + 3U;

    if (strcmp(mode_name, "rainbow") == 0)
    {
      WS2812_SetMode(&ws2812, WS2812_MODE_RAINBOW);
    }
    else if (strcmp(mode_name, "breath") == 0)
    {
      WS2812_SetMode(&ws2812, WS2812_MODE_BREATH);
    }
    else if (strcmp(mode_name, "chase") == 0)
    {
      WS2812_SetMode(&ws2812, WS2812_MODE_CHASE);
    }
    else if (strcmp(mode_name, "comet") == 0)
    {
      WS2812_SetMode(&ws2812, WS2812_MODE_COMET);
    }
    else
    {
      Bt24Reply("ERR ws mode\r\n");
      return 1U;
    }

    Bt24Reply("OK ws=%s\r\n", WS2812_GetModeName(WS2812_GetMode(&ws2812)));
    DebugPrintf("bt24 action: ws=%s\r\n", WS2812_GetModeName(WS2812_GetMode(&ws2812)));
    return 1U;
  }

  /* ---- page next / pagenext : OLED 页面切换到下一页 ---- */
  if ((strcmp(command, "page next") == 0) || (strcmp(command, "pagenext") == 0))
  {
    CycleOledPage();
    DebugPrintf("bt24 action: page=%s\r\n", GetOledPageName());
    Bt24Reply("OK page=%s\r\n", GetOledPageName());
    return 1U;
  }

  /* ---- page status / pagestatus : 切换到状态页 ---- */
  if ((strcmp(command, "page status") == 0) || (strcmp(command, "pagestatus") == 0))
  {
    SetOledPage(OLED_PAGE_STATUS);
    DebugPrintf("bt24 action: page=%s\r\n", GetOledPageName());
    Bt24Reply("OK page=%s\r\n", GetOledPageName());
    return 1U;
  }

  /* ---- page sensor / pagesensor : 切换到传感器页 ---- */
  if ((strcmp(command, "page sensor") == 0) || (strcmp(command, "pagesensor") == 0))
  {
    SetOledPage(OLED_PAGE_SENSOR);
    DebugPrintf("bt24 action: page=%s\r\n", GetOledPageName());
    Bt24Reply("OK page=%s\r\n", GetOledPageName());
    return 1U;
  }

  /* ---- page bt / page bt24 : 切换到蓝牙页 ---- */
  if ((strcmp(command, "page bt") == 0) || (strcmp(command, "page bt24") == 0) ||
      (strcmp(command, "pagebt") == 0) || (strcmp(command, "pagebt24") == 0))
  {
    SetOledPage(OLED_PAGE_BT24);
    DebugPrintf("bt24 action: page=%s\r\n", GetOledPageName());
    Bt24Reply("OK page=%s\r\n", GetOledPageName());
    return 1U;
  }

  /* ---- 未知命令 ---- */
  Bt24Reply("ERR unknown cmd\r\n");
  return 1U;
}

/* ========================================================================== */
/* 函数: GetOledPageName()                                                    */
/* 描述: 返回当前 OLED 页面的可读名称字符串                                   */
/* @return 页面名称: "STATUS" / "SENSOR" / "BT24"                            */
/* ========================================================================== */
static const char *GetOledPageName(void)
{
  switch (oled_page)
  {
    case OLED_PAGE_SENSOR:
      return "SENSOR";
    case OLED_PAGE_BT24:
      return "BT24";
    case OLED_PAGE_STATUS:
    default:
      return "STATUS";
  }
}

/* ========================================================================== */
/* 函数: GetBt24RoleName()                                                    */
/* 描述: 返回 BT24 模块的当前角色名                                           */
/* @return "MASTER" (主机) 或 "SLAVE" (从机)                                 */
/* ========================================================================== */
static const char *GetBt24RoleName(const DX_BT24_HandleTypeDef *bt24_handle)
{
  if ((bt24_handle != NULL) && (bt24_handle->role == DX_BT24_ROLE_MASTER))
  {
    return "MASTER";
  }

  return "SLAVE";
}

/* ========================================================================== */
/* 函数: GetBt24LinkName()                                                    */
/* 描述: 返回 BT24 蓝牙连接状态                                              */
/* @return "LINKED" (已连接) 或 "IDLE" (空闲)                                  */
/* ========================================================================== */
static const char *GetBt24LinkName(void)
{
  return DX_BT24_IsConnected(&bt24) ? "LINKED" : "IDLE";
}

/* ========================================================================== */
/* 函数: GetBt24WorkName()                                                    */
/* 描述: 返回 BT24 模块的工作模式名称                                         */
/* @return "NORMAL" / "LOWPWR" / "HIBER" / "UNKNOWN"                          */
/* ========================================================================== */
static const char *GetBt24WorkName(void)
{
  switch (DX_BT24_InferWorkState(&bt24))
  {
    case DX_BT24_WORK_NORMAL:
      return "NORMAL";
    case DX_BT24_WORK_LOW_POWER:
      return "LOWPWR";
    case DX_BT24_WORK_HIBERNATE:
      return "HIBER";
    case DX_BT24_WORK_UNKNOWN:
    default:
      return "UNKNOWN";
  }
}

/* ========================================================================== */
/* 函数: UpdateBt24LastText()                                                 */
/* 描述: 从 BT24 接收的原始数据中提取可打印文本,保存到显示缓冲区              */
/*                                                                            */
/* 规则:                                                                      */
/*   - 可打印 ASCII (32~126): 直接保留                                       */
/*   - 空白字符 (\r\n\t): 转换为空格                                         */
/*   - 其他不可见字符: 替换为 '.'                                            */
/* ========================================================================== */
static void UpdateBt24LastText(const uint8_t *data, uint16_t size)
{
  uint16_t src_index = 0U;
  uint16_t dst_index = 0U;

  while ((src_index < size) && (dst_index < (BT24_OLED_TEXT_SIZE - 1U)))
  {
    uint8_t ch = data[src_index++];

    if ((ch >= 32U) && (ch <= 126U))
    {
      /* 可打印 ASCII 字符 */
      bt24_last_text[dst_index++] = (char)ch;
    }
    else if ((ch == '\r') || (ch == '\n') || (ch == '\t'))
    {
      /* 换行/回车/制表符 → 空格,且不产生连续空格 */
      if ((dst_index > 0U) && (bt24_last_text[dst_index - 1U] != ' '))
      {
        bt24_last_text[dst_index++] = ' ';
      }
    }
    else
    {
      /* 其他不可见字符 → '.' */
      bt24_last_text[dst_index++] = '.';
    }
  }

  /* 如果没有任何有效字符,显示 '-' */
  if (dst_index == 0U)
  {
    bt24_last_text[0] = '-';
    dst_index = 1U;
  }

  bt24_last_text[dst_index] = '\0';  /* 字符串终止 */
}

/* ========================================================================== */
/* 函数: UpdateBt24LastHex()                                                  */
/* 描述: 将 BT24 接收的原始数据转换为十六进制格式,保存到显示缓冲区             */
/*                                                                            */
/* 格式: "48 65 6C 6C 6F" (每个字节以空格分隔)                               */
/* 应用: OLED 蓝牙页面显示十六进制原始数据                                   */
/* ========================================================================== */
static void UpdateBt24LastHex(const uint8_t *data, uint16_t size)
{
  static const char hex_digits[] = "0123456789ABCDEF";
  uint16_t src_index = 0U;
  uint16_t dst_index = 0U;

  while ((src_index < size) && ((dst_index + 2U) < (BT24_HEX_TEXT_SIZE - 1U)))
  {
    uint8_t value = data[src_index++];

    /* 将高 4 位转换为十六进制字符 */
    bt24_last_hex[dst_index++] = hex_digits[(value >> 4) & 0x0FU];
    /* 将低 4 位转换为十六进制字符 */
    bt24_last_hex[dst_index++] = hex_digits[value & 0x0FU];

    /* 在字节之间插入空格(除非是最后一个字节或缓冲区已满) */
    if ((src_index < size) && (dst_index < (BT24_HEX_TEXT_SIZE - 1U)))
    {
      bt24_last_hex[dst_index++] = ' ';
    }
  }

  /* 如果没有任何有效数据,显示 '-' */
  if (dst_index == 0U)
  {
    bt24_last_hex[0] = '-';
    dst_index = 1U;
  }

  bt24_last_hex[dst_index] = '\0';
}

/* ========================================================================== */
/* 函数: UpdateMotorControl()                                                 */
/* 描述: 电机速度控制 — 通过编码器差值计算实际转速,调整目标速度                */
/*                                                                            */
/* 编码器转速计算:                                                            */
/*   编码器接在 TIM1 上,配置为编码器模式。                                     */
/*   每次 UpdateMotorControl() 被调用时:                                       */
/*     1. 读取当前编码器计数值 (__HAL_TIM_GET_COUNTER)                        */
/*     2. 计算差值: delta = 当前值 - 上一次值                                */
/*     3. 差值反映了两轮调用之间编码器转过的步数 = 转速的间接量度             */
/*     4. 根据差值调整目标转速:                                               */
/*        new_speed = target_speed + delta * MOTOR_SPEED_STEP_PERCENT          */
/*                                                                            */
/* 速度调节原理:                                                              */
/*   这是一个简单的比例控制:                                                   */
/*   - 如果编码器正在旋转(差值为正),说明有人在手动旋转编码器调整转速             */
/*   - 每检测到 step(+1),速度增加 5%                                          */
/*   - 每检测到 step(-1),速度减少 5%                                          */
/*   - 如果编码器反转(差值为负),速度减少                                     */
/*   这实现了通过旋转编码器物理旋钮来调节风扇转速的交互方式。                   */
/*                                                                            */
/* 启动力矩(Boost)处理:                                                       */
/*   如果当前处于启动力矩期间(从停止到启动的 300ms 内),使用全速(100%)          */
/*   否则使用目标速度。                                                        */
/* ========================================================================== */
static void UpdateMotorControl(void)
{
  /*
   * 读取当前编码器计数值
   * TIM1 编码器模式: CH1(PA8) + CH2(PA9)
   * 返回值 int16_t (-32768 ~ 32767)
   */
  int16_t encoder_count = (int16_t)__HAL_TIM_GET_COUNTER(&htim1);
  /* 计算出从上一次读取到现在的编码器变化量 */
  int16_t delta = (int16_t)(encoder_count - encoder_last_count);
  uint32_t now = HAL_GetTick();

  /* 如果编码器有转动(差值不为 0),则调整目标速度 */
  if (delta != 0)
  {
    /*
     * 速度调整公式:
     * 新速度 = 当前目标速度 + 编码器步进值 × 速度步进百分比
     * delta > 0: 正转 → 加速
     * delta < 0: 反转 → 减速
     */
    int16_t next_speed = (int16_t)(motor_target_speed_percent + (delta * MOTOR_SPEED_STEP_PERCENT));

    /* 限幅: -100~100% */
    if (next_speed < -100)
    {
      next_speed = -100;
    }
    else if (next_speed > 100)
    {
      next_speed = 100;
    }

    /* 如果从停止状态开始转动,启动启动力矩 */
    if ((motor_target_speed_percent == 0) && (next_speed > 0))
    {
      motor_boost_until_tick = now + FAN_START_BOOST_MS;
    }

    motor_target_speed_percent = next_speed;
    encoder_last_count = encoder_count;  /* 更新基准值 */
  }

  /* ---- 根据当前状态设置电机实际速度 ---- */
  if (motor_target_speed_percent == 0)
  {
    /* 目标速度 0: 停止电机 */
    DRV8833_Stop(&motor);
  }
  else if ((int32_t)(motor_boost_until_tick - now) > 0)
  {
    /*
     * 处于启动力矩期间:
     * motor_boost_until_tick > now => 仍在 boost 窗口内
     * 使用全速(100%)启动,克服静摩擦力矩
     */
    DRV8833_SetSpeed(&motor, FAN_START_BOOST_PERCENT);
  }
  else
  {
    /* 正常速度控制 */
    DRV8833_SetSpeed(&motor, motor_target_speed_percent);
  }
}

/* ========================================================================== */
/* 函数: UpdateUltrasonic()                                                   */
/* 描述: 超声波测距更新 — 定时触发 HC-SR04 并处理测距结果                      */
/*                                                                            */
/* HC-SR04 工作流程:                                                          */
/*   1. TRIG 引脚输出 10us 高电平 (触发信号)                                  */
/*   2. 模块自动发送 8 个 40kHz 脉冲,ECHO 引脚输出高电平                     */
/*   3. ECHO 高电平脉宽 = 距离 × 58us/cm                                     */
/*   4. ECHO 信号通过 EXTI 捕获 (PA10, 双边沿中断):                           */
/*      - 上升沿: 开始计时                                                    */
/*      - 下降沿: 结束计时,计算脉宽                                           */
/*   5. 距离公式: 距离(cm) = 脉宽(us) / 58                                    */
/*                                                                            */
/* 触发周期: 由 HCSR04_MEASUREMENT_PERIOD_MS 定义(一般在 hcsr04.h 中定义)     */
/*   两次触发之间必须间隔 60ms 以上,以确保回波不串扰                           */
/* ========================================================================== */
static void UpdateUltrasonic(void)
{
  static uint32_t last_trigger_tick;  /* 上次触发时间 */
  uint32_t now = HAL_GetTick();

  /*
   * HCSR04_Process(): 处理超声波模块的状态机
   * 检查回波捕获是否完成,若完成则计算距离
   */
  HCSR04_Process(&hcsr04);

  /*
   * 触发条件:
   * - 不在等待回波中 (hcsr04.waiting_for_echo == 0)
   * - 距离上次触发已超过测量周期
   */
  if ((hcsr04.waiting_for_echo == 0U) && ((now - last_trigger_tick) >= HCSR04_MEASUREMENT_PERIOD_MS))
  {
    last_trigger_tick = now;
    HCSR04_Trigger(&hcsr04);  /* 发出 10us 触发脉冲 */
  }
}

/* ========================================================================== */
/* 函数: UpdateWs2812()                                                       */
/* 描述: WS2812 灯带特效更新                                                  */
/*                                                                            */
/* 支持的特效模式:                                                            */
/*   RAINBOW (彩虹):  每个 LED 显示不同颜色,效果随时间旋转                    */
/*   BREATH (呼吸灯):  所有 LED 同步由暗到亮再变暗                            */
/*   CHASE (追逐):    单个 LED 沿灯带移动                                    */
/*   COMET (彗星):    一个亮核带拖尾效果沿灯带移动                            */
/*                                                                            */
/* 每种特效的更新由 WS2812_UPDATE_MS (25ms) 定时器控制                         */
/* ========================================================================== */
static void UpdateWs2812(void)
{
  static uint32_t last_update_tick;  /* 上次更新灯带的时间戳 */
  uint32_t now = HAL_GetTick();
  uint32_t index;

  /* 限速: 每 WS2812_UPDATE_MS (25ms) 更新一次 */
  if ((now - last_update_tick) < WS2812_UPDATE_MS)
  {
    return;
  }

  last_update_tick = now;
  WS2812_Clear(&ws2812);  /* 清除所有像素(全部熄灭) */

  /* 根据当前模式绘制不同特效 */
  switch (WS2812_GetMode(&ws2812))
  {
    /* ---- 彩虹模式 ---- */
    /*
     * 每个 LED 显示色环中的一个颜色,整体效果随时间旋转。
     * hue 计算:
     *   now/8: 色相随时间缓慢变化(每 8ms 变化 1 色相步)
     *   index * 1536/pixel_count: 各个 LED 在色环上均匀分布
     * 1536 = 256*6 = 360° 色环总步数
     */
    case WS2812_MODE_RAINBOW:
      for (index = 0U; index < ws2812.pixel_count; ++index)
      {
        uint8_t red;
        uint8_t green;
        uint8_t blue;
        uint32_t hue = ((now / 8U) + (index * 1536U / ws2812.pixel_count)) % 1536U;

        ColorWheel(hue, &red, &green, &blue);
        WS2812_SetPixelRGB(&ws2812, (uint16_t)index, red, green, blue);
      }
      break;

    /* ---- 呼吸灯模式 ---- */
    /*
     * 所有 LED 同步呼吸:
     * TriangleWave(now, 2200, 255): 生成 0~255 的三角波,周期 2.2s
     * SmoothLevel(): 应用 smoothstep 曲线,让亮度变化更自然
     * 最终亮度 *180/255: 限制最大亮度为 180,避免过亮
     */
    case WS2812_MODE_BREATH:
    {
      uint8_t level = (uint8_t)((SmoothLevel(TriangleWave(now, 2200U, 255U), 255U) * 180U) / 255U);

      for (index = 0U; index < ws2812.pixel_count; ++index)
      {
        WS2812_SetPixelRGB(&ws2812, (uint16_t)index, level, level, level);
      }
      break;
    }

    /* ---- 追逐模式 ---- */
    /*
     * 一个橙色 (255,64,0) 的亮点沿灯带循环移动
     * head = (now/120) % pixel_count: 亮点每 120ms 移动一个像素
     */
    case WS2812_MODE_CHASE:
    {
      uint32_t head = (now / 120U) % ws2812.pixel_count;

      WS2812_SetPixelRGB(&ws2812, (uint16_t)head, 255U, 64U, 0U);
      break;
    }

    /* ---- 彗星模式(默认) ---- */
    /*
     * 一个蓝色彗星(亮核 + 4 像素拖尾)沿灯带循环移动
     * head: 每 100ms 移动一个像素
     * distance: 当前像素到彗核的距离
     * 距离 < 5: 属于彗星尾巴,亮度从核(最亮)到尾部(渐暗)递减
     *   核(distance=0):   亮度 5*48 = 240
     *   尾(distance=4):   亮度 1*48 = 48
     *   核颜色: 蓝色 (0, level/3, level)
     */
    case WS2812_MODE_COMET:
    default:
    {
      uint32_t head = (now / 100U) % ws2812.pixel_count;

      for (index = 0U; index < ws2812.pixel_count; ++index)
      {
        /* 计算当前像素到彗核的距离(循环距离) */
        uint32_t distance = (head + ws2812.pixel_count - index) % ws2812.pixel_count;

        if (distance < 5U)
        {
          uint8_t level = (uint8_t)((5U - distance) * 48U);
          WS2812_SetPixelRGB(&ws2812, (uint16_t)index, 0U, level / 3U, level);
        }
      }
      break;
    }
  }

  /* 将像素缓冲区数据通过 PWM+DMA 发送到灯带 */
  WS2812_Show(&ws2812);
}

/* ========================================================================== */
/* 函数: UpdateOutputs()                                                      */
/* 描述: 更新所有输出设备的状态                                               */
/*                                                                            */
/* 每次循环都会调用的输出更新函数,内容包括:                                    */
/*   1. 循迹传感器输入 → 反转输出到 PB9 (控制逻辑输出)                       */
/*   2. 电机控制 (编码器差值 → 速度调节)                                     */
/*   3. 超声波测距 (触发/处理)                                               */
/*   4. WS2812 灯带特效更新                                                   */
/* ========================================================================== */
static void UpdateOutputs(void)
{
  /*
   * 读取循迹传感器: 连接到 PB14
   * 循迹模块输出: 检测到黑线时输出低电平,白底时输出高电平
   */
  GPIO_PinState line_state = HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_14);

  /* 清除电机 PWM (防短路保护: 每次先归零再重新设置) */
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_1, 0U);
  __HAL_TIM_SET_COMPARE(&htim2, TIM_CHANNEL_2, 0U);

  /* 更新各模块 */
  UpdateMotorControl();   /* 电机 PID 速度控制 */
  UpdateUltrasonic();     /* 超声波测距更新 */
  UpdateWs2812();         /* WS2812 灯带特效 */

  /*
   * 循迹信号反转输出到 PB9:
   * 输入低(检测到黑线) → 输出高
   * 输入高(白底) → 输出低
   * 用于控制继电器或蜂鸣器
   */
  HAL_GPIO_WritePin(GPIOB, GPIO_PIN_9, (line_state == GPIO_PIN_RESET) ? GPIO_PIN_SET : GPIO_PIN_RESET);
}

/* ========================================================================== */
/* 函数: ScanI2cBus()                                                         */
/* 描述: 扫描 I2C1 总线,检测挂载的设备地址                                    */
/*                                                                            */
/* 扫描方法:                                                                  */
/*   对 1~127 的每个地址,调用 HAL_I2C_IsDeviceReady() 检测是否有设备响应。      */
/*   将响应的设备地址保存到 i2c_devices[] 数组。                               */
/*                                                                            */
/* HAL_I2C_IsDeviceReady() 参数:                                              */
/*   - hi2c: I2C 句柄                                                        */
/*   - (address << 1): 7 位地址左移 1 位得到 8 位 I2C 地址(左对齐)            */
/*   - 2: 最多尝试 2 次                                                      */
/*   - 5: 每次尝试超时 5ms                                                    */
/* ========================================================================== */
static void ScanI2cBus(void)
{
  uint8_t address;

  i2c_device_count = 0;  /* 清空上次扫描结果 */

  /*
   * 遍历 I2C 地址 1~127
   * 注意: 地址 0 是广播地址,不做扫描
   */
  for (address = 1; address < 128 && i2c_device_count < sizeof(i2c_devices); ++address)
  {
    /*
     * I2C 地址左移 1 位:
     * STM32 HAL 库的 I2C 地址需要左对齐 (7 位地址放在高 7 位)
     * 所以 7 位地址 a 需要转换为 (a << 1)
     */
    if (HAL_I2C_IsDeviceReady(&hi2c1, (uint16_t)(address << 1), 2, 5) == HAL_OK)
    {
      i2c_devices[i2c_device_count++] = address;
    }
  }
}

/* ========================================================================== */
/* 函数: UpdateAht20()                                                        */
/* 描述: 读取 AHT20 温湿度传感器数据                                          */
/*                                                                            */
/* 如果读取失败(通信错误),将 aht20_ready 标记为 0,                              */
/* 后续将停止读取直到重新初始化。                                              */
/* ========================================================================== */
static void UpdateAht20(void)
{
  if (aht20_ready == 0U)
  {
    return;  /* 传感器未就绪,跳过 */
  }

  /*
   * AHT20_ReadData(): 触发 AHT20 测量并读取结果
   * 触发测量后需等待约 80ms 才能读取数据
   * 返回结果包含: temperature_c (温度, °C), humidity_rh (相对湿度, %)
   */
  if (AHT20_ReadData(&aht20, &aht20_data) != HAL_OK)
  {
    aht20_ready = 0U;  /* 读取失败,标记为不可用 */
  }
}

/* ========================================================================== */
/* 函数: UpdateNtc()                                                          */
/* 描述: 将 NTC ADC 原始值转换为温度                                          */
/*                                                                            */
/* NTC 温度转换原理:                                                          */
/*   1. ADC 原始值 (12-bit, 0~4095) 代表 NTC 分压电路上的电压                 */
/*   2. NTC_ConvertRaw() 执行:                                                */
/*      a. 根据 ADC 值和已知的分压电阻 (10KΩ) 计算 NTC 当前电阻                */
/*      b. 通过查表法 (预存的 25°C 标定表) 将电阻值映射为温度                   */
/*      c. 使用插值提高精度                                                    */
/*   3. 结果存储在 ntc_data.temperature_c (°C) 和 .resistance_ohms (Ω)       */
/* ========================================================================== */
static void UpdateNtc(uint16_t ntc_raw)
{
  if ((ntc_ready == 0U) || (NTC_ConvertRaw(&ntc, ntc_raw, &ntc_data) != HAL_OK))
  {
    ntc_ready = 0U;
  }
}

/* ========================================================================== */
/* 函数: ReportStatus()                                                       */
/* 描述: 向调试串口打印系统状态报告                                           */
/*                                                                            */
/* 报告内容:                                                                  */
/*   - NTC ADC原始值、温度、电阻                                              */
/*   - AHT20 温湿度                                                          */
/*   - 电机模式、速度                                                        */
/*   - HC-SR04 距离、脉宽                                                    */
/*   - WS2812 灯带模式                                                        */
/*   - BT24 收发字节统计                                                      */
/*   - 编码器计数值                                                          */
/*   - 三个按键状态                                                          */
/*   - 循迹传感器状态                                                        */
/*   - OLED 页面名称                                                         */
/*   - I2C 设备列表                                                          */
/* ========================================================================== */
static void ReportStatus(void)
{
  /*
   * 采集当前所有状态值:
   * 将浮点数转换为定点数(x10)以简化格式化输出
   */
  int16_t encoder_count = (int16_t)__HAL_TIM_GET_COUNTER(&htim1);
  uint8_t key1 = (uint8_t)(HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_12) == GPIO_PIN_RESET);   /* KEY1: 按下=低电平 */
  uint8_t key2 = (uint8_t)(HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_13) == GPIO_PIN_RESET);   /* KEY2: 按下=低电平 */
  uint8_t key_enc = (uint8_t)(HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_15) == GPIO_PIN_RESET); /* 编码器按键: 按下=低电平 */
  uint8_t line_detected = (uint8_t)(HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_14) == GPIO_PIN_RESET); /* 循迹: 黑线=低电平 */
  uint8_t index;
  int32_t temp_x10 = (int32_t)(aht20_data.temperature_c * 10.0f);
  int32_t humi_x10 = (int32_t)(aht20_data.humidity_rh * 10.0f);
  int32_t ntc_temp_x10 = (int32_t)(ntc_data.temperature_c * 10.0f);
  uint32_t ntc_res_ohm = (uint32_t)(ntc_data.resistance_ohms + 0.5f);
  int32_t distance_x10 = (int32_t)(HCSR04_GetDistanceCm(&hcsr04) * 10.0f);

  /* 格式化输出到调试串口 */
  DebugPrintf("adc ntc=%u | ntc=%ld.%01ldC %luohm | aht=%ld.%01ldC %ld.%01ld%% | motor=%s %d%% | dist=%ld.%01ldcm | ws=%s | bt=%lu/%lu | enc=%d | key=%u%u%u | line=%u | echo_us=%lu | page=%s | i2c:",
              ntc_raw_last,
              ntc_temp_x10 / 10L,
              labs(ntc_temp_x10 % 10L),
              ntc_res_ohm,
              temp_x10 / 10L,
              labs(temp_x10 % 10L),
              humi_x10 / 10L,
              labs(humi_x10 % 10L),
              DRV8833_GetModeName(DRV8833_GetMode(&motor)),
              DRV8833_GetSpeed(&motor),
              distance_x10 / 10L,
              labs(distance_x10 % 10L),
              WS2812_GetModeName(WS2812_GetMode(&ws2812)),
              bt24_rx_total_bytes,
              bt24_tx_total_bytes,
              encoder_count,
              key1,
              key2,
              key_enc,
              line_detected,
              HCSR04_GetPulseWidthUs(&hcsr04),
              GetOledPageName());

  /* 打印 I2C 设备列表 */
  if (i2c_device_count == 0U)
  {
    DebugPrint(" none");
  }
  else
  {
    for (index = 0; index < i2c_device_count; ++index)
    {
      DebugPrintf(" 0x%02X", i2c_devices[index]);
    }
  }

  DebugPrint("\r\n");
}

/* ========================================================================== */
/* 函数: UpdateOled()                                                         */
/* 描述: 刷新 OLED 显示屏内容                                                */
/*                                                                            */
/* 根据当前 oled_page 显示不同页面:                                           */
/*   STATUS(状态页):  系统概览 — 页面名/灯带/BT/温湿度/NTC/电机/超声波/统计   */
/*   SENSOR(传感器页): 传感器明细 — NTC/AHT20/电机/按键/循迹/BT文本           */
/*   BT24(蓝牙页):    蓝牙信息 — 角色/连接/工作模式/收发统计/最后数据         */
/* ========================================================================== */
static void UpdateOled(void)
{
  char line[40];          /* 行缓冲区,用于格式化显示文本 */
  int16_t encoder_count;
  uint8_t key1;
  uint8_t key2;
  uint8_t key_enc;
  uint8_t line_detected;
  int32_t temp_x10;
  int32_t humi_x10;
  int32_t ntc_temp_x10;
  uint32_t ntc_res_ohm;
  int32_t distance_x10;

  if (oled_ready == 0U)
  {
    return;  /* OLED 未就绪,跳过 */
  }

  /* 采集显示所需的数据 */
  encoder_count = (int16_t)__HAL_TIM_GET_COUNTER(&htim1);
  key1 = (uint8_t)(HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_12) == GPIO_PIN_RESET);
  key2 = (uint8_t)(HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_13) == GPIO_PIN_RESET);
  key_enc = (uint8_t)(HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_15) == GPIO_PIN_RESET);
  line_detected = (uint8_t)(HAL_GPIO_ReadPin(GPIOB, GPIO_PIN_14) == GPIO_PIN_RESET);
  temp_x10 = (int32_t)(aht20_data.temperature_c * 10.0f);
  humi_x10 = (int32_t)(aht20_data.humidity_rh * 10.0f);
  ntc_temp_x10 = (int32_t)(ntc_data.temperature_c * 10.0f);
  ntc_res_ohm = (uint32_t)(ntc_data.resistance_ohms + 0.5f);
  distance_x10 = (int32_t)(HCSR04_GetDistanceCm(&hcsr04) * 10.0f);

  CH1116_Clear(&oled);  /* 清屏,准备重绘 */

  /* ---- STATUS 状态页 ---- */
  if (oled_page == OLED_PAGE_STATUS)
  {
    (void)snprintf(line, sizeof(line), "PAGE:%s", GetOledPageName());
    CH1116_DrawString(&oled, 0, 0, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "WS:%s BT:%s", WS2812_GetModeName(WS2812_GetMode(&ws2812)), GetBt24RoleName(&bt24));
    CH1116_DrawString(&oled, 0, 8, line, CH1116_COLOR_WHITE);

    if (aht20_ready != 0U)
    {
      (void)snprintf(line, sizeof(line), "AHT:%ld.%01ldC %ld.%01ld%%",
                     temp_x10 / 10L,
                     labs(temp_x10 % 10L),
                     humi_x10 / 10L,
                     labs(humi_x10 % 10L));
    }
    else
    {
      (void)snprintf(line, sizeof(line), "AHT:ERR");
    }
    CH1116_DrawString(&oled, 0, 16, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "ADC N:%4u", ntc_raw_last);
    CH1116_DrawString(&oled, 0, 24, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "M:%s %4d ENC:%4d",
                   DRV8833_GetModeName(DRV8833_GetMode(&motor)),
                   DRV8833_GetSpeed(&motor),
                   encoder_count);
    CH1116_DrawString(&oled, 0, 32, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "US:%ld.%01ldcm %5lu",
                   distance_x10 / 10L,
                   labs(distance_x10 % 10L),
                   HCSR04_GetPulseWidthUs(&hcsr04));
    CH1116_DrawString(&oled, 0, 40, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "BT RX:%lu TX:%lu", bt24_rx_total_bytes, bt24_tx_total_bytes);
    CH1116_DrawString(&oled, 0, 48, line, CH1116_COLOR_WHITE);

    CH1116_DrawString(&oled, 0, 56, "PB12:WS PB13:PAGE", CH1116_COLOR_WHITE);
  }
  /* ---- SENSOR 传感器页 ---- */
  else if (oled_page == OLED_PAGE_SENSOR)
  {
    (void)snprintf(line, sizeof(line), "PAGE:%s", GetOledPageName());
    CH1116_DrawString(&oled, 0, 0, line, CH1116_COLOR_WHITE);

    if (ntc_ready != 0U)
    {
      (void)snprintf(line, sizeof(line), "NTC:%ld.%01ldC",
                     ntc_temp_x10 / 10L,
                     labs(ntc_temp_x10 % 10L));
      CH1116_DrawString(&oled, 0, 8, line, CH1116_COLOR_WHITE);

      (void)snprintf(line, sizeof(line), "R:%luohm ADC:%4u", ntc_res_ohm, ntc_raw_last);
      CH1116_DrawString(&oled, 0, 16, line, CH1116_COLOR_WHITE);
    }
    else
    {
      CH1116_DrawString(&oled, 0, 8, "NTC:ERR", CH1116_COLOR_WHITE);
    }

    if (aht20_ready != 0U)
    {
      (void)snprintf(line, sizeof(line), "AHT:%ld.%01ldC %ld.%01ld%%",
                     temp_x10 / 10L,
                     labs(temp_x10 % 10L),
                     humi_x10 / 10L,
                     labs(humi_x10 % 10L));
    }
    else
    {
      (void)snprintf(line, sizeof(line), "AHT:ERR");
    }
    CH1116_DrawString(&oled, 0, 24, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "M:%s %4d ENC:%4d",
                   DRV8833_GetModeName(DRV8833_GetMode(&motor)),
                   DRV8833_GetSpeed(&motor),
                   encoder_count);
    CH1116_DrawString(&oled, 0, 32, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "KEY:%u%u%u LINE:%u", key1, key2, key_enc, line_detected);
    CH1116_DrawString(&oled, 0, 40, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "BT:%s", bt24_last_text[0] != '\0' ? bt24_last_text : "-");
    CH1116_DrawString(&oled, 0, 48, line, CH1116_COLOR_WHITE);

    CH1116_DrawString(&oled, 0, 56, "PB15:STOP", CH1116_COLOR_WHITE);
  }
  /* ---- BT24 蓝牙页 ---- */
  else
  {
    (void)snprintf(line, sizeof(line), "PAGE:%s", GetOledPageName());
    CH1116_DrawString(&oled, 0, 0, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "ROLE:%s %s", GetBt24RoleName(&bt24), GetBt24LinkName());
    CH1116_DrawString(&oled, 0, 8, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "WORK:%s", GetBt24WorkName());
    CH1116_DrawString(&oled, 0, 16, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "RX:%lu TX:%lu", bt24_rx_total_bytes, bt24_tx_total_bytes);
    CH1116_DrawString(&oled, 0, 24, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "LAST:");
    CH1116_DrawString(&oled, 0, 32, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "%s", bt24_last_text[0] != '\0' ? bt24_last_text : "-");
    CH1116_DrawString(&oled, 0, 40, line, CH1116_COLOR_WHITE);

    (void)snprintf(line, sizeof(line), "HEX:%s", bt24_last_hex[0] != '\0' ? bt24_last_hex : "-");
    CH1116_DrawString(&oled, 0, 48, line, CH1116_COLOR_WHITE);

    CH1116_DrawString(&oled, 0, 56, "PB12:WS PB13:PAGE", CH1116_COLOR_WHITE);
  }

  /* 将缓冲区内容发送到 OLED 显示屏 */
  CH1116_UpdateScreen(&oled);
}

/* ========================================================================== */
/* 函数: ProcessDebugUartFrame()                                              */
/* 描述: 处理 USART2 (调试串口) 接收到的数据帧                                */
/*                                                                            */
/* 处理流程:                                                                  */
/*   1. 检查 uart2_rx_frame_ready 标志                                        */
/*   2. 关中断 → 读取帧数据 → 清标志 → 开中断(保证线程安全)                   */
/*   3. 将接收到的数据回显到调试串口                                           */
/*   4. 通过 BT24 蓝牙转发数据(调试串口 → 蓝牙透传)                           */
/* ========================================================================== */
static void ProcessDebugUartFrame(void)
{
  uint16_t frame_size;

  if (uart2_rx_frame_ready == 0U)
  {
    return;  /* 无数据待处理 */
  }

  /*
   * 原子操作: 关中断读取帧数据,确保不被中断上下文干扰
   * __disable_irq() / __enable_irq(): CMSIS 全局中断开关
   */
  __disable_irq();
  frame_size = uart2_rx_frame_size;
  uart2_rx_frame_ready = 0U;
  __enable_irq();

  if (frame_size == 0U)
  {
    return;
  }

  /* 回显接收到的数据到调试串口 */
  HAL_UART_Transmit(&huart2, (uint8_t *)"\r\nrx2: ", 7, HAL_MAX_DELAY);
  HAL_UART_Transmit(&huart2, uart2_rx_frame, frame_size, HAL_MAX_DELAY);
  HAL_UART_Transmit(&huart2, (uint8_t *)"\r\n", 2, HAL_MAX_DELAY);

  /* 通过 BT24 蓝牙转发接收到的数据 */
  if (DX_BT24_Send(&bt24, uart2_rx_frame, frame_size, HAL_MAX_DELAY) == HAL_OK)
  {
    bt24_tx_total_bytes += frame_size;
  }
}

/* ========================================================================== */
/* 函数: ProcessBt24Frame()                                                   */
/* 描述: 处理 USART3 (BT24 蓝牙模块) 接收到的数据帧                           */
/*                                                                            */
/* 处理流程:                                                                  */
/*   1. 检查 uart3_rx_frame_ready 标志                                        */
/*   2. 原子读取帧数据                                                        */
/*   3. 回显原始数据到调试串口                                                 */
/*   4. 将原始数据规范化为小写、去空格、合并空白的命令字符串                  */
/*   5. 解析命令流并执行(可能包含多个连续命令)                                */
/*   6. 如果成功执行了命令,刷新 OLED 显示                                     */
/* ========================================================================== */
static void ProcessBt24Frame(void)
{
  char command[80];         /* 规范化后的命令缓冲区 */
  uint16_t frame_size;

  if (uart3_rx_frame_ready == 0U)
  {
    return;
  }

  /* 原子读取帧数据 */
  __disable_irq();
  frame_size = uart3_rx_frame_size;
  uart3_rx_frame_ready = 0U;
  __enable_irq();

  if (frame_size == 0U)
  {
    return;
  }

  /* 回显到调试串口 */
  HAL_UART_Transmit(&huart2, (uint8_t *)"\r\nbt24: ", 8, HAL_MAX_DELAY);
  HAL_UART_Transmit(&huart2, uart3_rx_frame, frame_size, HAL_MAX_DELAY);
  HAL_UART_Transmit(&huart2, (uint8_t *)"\r\n", 2, HAL_MAX_DELAY);
  DebugPrintf("bt24 hex: %s\r\n", bt24_last_hex);

  /*
   * 命令解析流程:
   * 1. NormalizeBt24Command(): 将原始字节数据转换为规范化命令字符串
   *    (小写、合并空格、去除特殊字符)
   * 2. ExecuteBt24CommandStream(): 解析并执行命令流(支持多个命令)
   */
  NormalizeBt24Command(uart3_rx_frame, frame_size, command, sizeof(command));
  if (ExecuteBt24CommandStream(command) != 0U)
  {
    /* 命令执行成功,更新 OLED 显示 */
    UpdateOled();
  }
}

/* ========================================================================== */
/* 函数: LedRunningLight()                                                    */
/* 描述: RGB LED 流水灯 — R(PB0) → G(PA6) → B(PA7) 循环                      */
/*                                                                            */
/* 状态机: 3 步循环,每步点亮一个颜色,其余熄灭                                 */
/*   led_step = 0: 点亮红色 (R=PB0)                                          */
/*   led_step = 1: 点亮绿色 (G=PA6)                                          */
/*   led_step = 2: 点亮蓝色 (B=PA7)                                          */
/*   步进周期: LED_RUNNING_STEP_MS = 500ms                                    */
/* ========================================================================== */
static void LedRunningLight(void)
{
  uint32_t now = HAL_GetTick();

  /* 时间到才步进 */
  if ((now - led_last_tick) < LED_RUNNING_STEP_MS)
  {
    return;
  }
  led_last_tick = now;

  /* 第一步: 全部熄灭 */
  HAL_GPIO_WritePin(LED_R_PORT, LED_R_PIN, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(LED_G_PORT, LED_G_PIN, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(LED_B_PORT, LED_B_PIN, GPIO_PIN_RESET);

  /* 第二步: 点亮当前步对应的 LED */
  switch (led_step)
  {
    case 0:
      HAL_GPIO_WritePin(LED_R_PORT, LED_R_PIN, GPIO_PIN_SET);  /* 红色 */
      break;
    case 1:
      HAL_GPIO_WritePin(LED_G_PORT, LED_G_PIN, GPIO_PIN_SET);  /* 绿色 */
      break;
    case 2:
      HAL_GPIO_WritePin(LED_B_PORT, LED_B_PIN, GPIO_PIN_SET);  /* 蓝色 */
      break;
    default:
      break;
  }

  /* 步进: 0→1→2→0→... 循环 */
  led_step = (led_step + 1U) % 3U;
}
