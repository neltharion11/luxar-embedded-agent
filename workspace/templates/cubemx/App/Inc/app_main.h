/**
 * @file    app_main.h
 * @brief   应用层入口头文件
 *          本项目所有 BSP（板级支持包）模块在此集成，
 *          通过 App_Init() / App_Loop() 接口与 main.c 对接。
 *
 * @details 项目基于 STM32F103C8T6（Blue Pill），主频 72MHz。
 *          采用超级循环（Super Loop）架构，即在 main() 中
 *          先调用 App_Init() 完成初始化，再反复调用 App_Loop() 执行主循环。
 *
 *          集成的 BSP 模块清单：
 *          - CH1116 OLED 显示驱动（I2C 接口，128x64 单色屏）
 *          - AHT20 温湿度传感器（I2C 接口）
 *          - DX-BT24 蓝牙模块（UART3，9600 波特率）
 *          - DRV8833 直流电机驱动（PWM 控制风扇）
 *          - HC-SR04 超声波测距模块（GPIO 触发 + EXTI 捕获回波）
 *          - NTC 热敏电阻温度传感器（ADC 单通道采样）
 *          - WS2812 智能灯带（TIM3 PWM + DMA 驱动，10 个像素）
 *          - 按键 KEY1(PB12)、KEY2(PB13)、编码器按键(PB15)
 *          - RGB LED (R=PB0, G=PA6, B=PA7)
 *          - 循迹传感器（GPIO 输入，PB14）
 *          - 编码器（TIM1 编码器模式）
 */

#ifndef APP_MAIN_H
#define APP_MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

#include "stm32f1xx_hal.h"

/**
 * @brief  应用层初始化函数
 *          在主循环开始前调用一次，完成以下操作：
 *          - ADC 校准 & 配置单通道模式
 *          - 启动 TIM 编码器、PWM 外设
 *          - I2C 总线扫描，发现挂载的设备
 *          - 初始化各 BSP 驱动模块（BT24、DRV8833、HCSR04、WS2812、NTC、AHT20、OLED）
 *          - 重新配置 PB0/PA6/PA7 为 GPIO 输出（原为 PWM/未配置）
 *          - 启动 UART 空闲中断 + DMA 接收
 */
void App_Init(void);

/**
 * @brief  应用层主循环函数（超级循环）
 *          在 main() 的 while(1) 中反复调用。
 *          每轮执行：ADC 采样、输出更新、串口帧处理、流水灯，
 *          定时执行：状态报告（500ms）、I2C 扫描（3000ms）、AHT20 读取（2000ms）、
 *          按键事件处理（消抖 150ms）。
 */
void App_Loop(void);

/**
 * @brief  GPIO EXTI 外部中断回调函数
 *          在 HAL_GPIO_EXTI_Callback() 中被调用。
 *          处理以下中断：
 *          - PA10：HC-SR04 超声波回波信号捕获
 *          - PB12：按键 KEY1（WS2812 模式切换）
 *          - PB13：按键 KEY2（OLED 页面切换）
 *          - PB15：编码器按键（风扇停止）
 * @param gpio_pin  触发中断的 GPIO 引脚编号
 */
void App_OnGpioExtiCallback(uint16_t gpio_pin);

/**
 * @brief  UART 空闲中断 + DMA 接收回调函数
 *          在 HAL_UARTEx_RxEventCallback() 中被调用。
 *          USART2 用于调试串口（115200），USART3 用于 DX-BT24 蓝牙模块（9600）。
 *          使用双缓冲机制：DMA 持续写入 rx_buffer，
 *          空闲中断触发后将数据复制到 rx_frame 供主循环处理。
 * @param huart    触发回调的 UART 句柄
 * @param size     本次接收到的数据长度（字节数）
 */
void App_OnUartRxEventCallback(UART_HandleTypeDef *huart, uint16_t size);

#ifdef __cplusplus
}
#endif

#endif /* APP_MAIN_H */
