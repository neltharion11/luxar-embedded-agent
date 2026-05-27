#include "app_main.h"

/* RGB LED: PB0=Red, PA6=Green, PA7=Blue (common cathode, HIGH=ON) */
#define LED_R_PORT  GPIOB
#define LED_R_PIN   GPIO_PIN_0
#define LED_G_PORT  GPIOA
#define LED_G_PIN   GPIO_PIN_6
#define LED_B_PORT  GPIOA
#define LED_B_PIN   GPIO_PIN_7

static uint8_t led_step;

void App_Init(void)
{
    /* ---- Reset ALL GPIO ports to default (floating input) ---- */
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_GPIOB_CLK_ENABLE();
    __HAL_RCC_GPIOC_CLK_ENABLE();
    __HAL_RCC_GPIOD_CLK_ENABLE();
    HAL_GPIO_DeInit(GPIOA, 0xFFFF);
    HAL_GPIO_DeInit(GPIOB, 0xFFFF);
    HAL_GPIO_DeInit(GPIOC, 0xFFFF);
    HAL_GPIO_DeInit(GPIOD, 0xFFFF);

    /* ---- Reset peripherals that drive external chips ---- */
    /* I2C1 (OLED on PB6/PB7) */
    __HAL_RCC_I2C1_CLK_ENABLE();
    __HAL_RCC_I2C1_FORCE_RESET();
    __HAL_RCC_I2C1_RELEASE_RESET();
    __HAL_RCC_I2C1_CLK_DISABLE();

    /* TIM3 (WS2812 LED strip on PB4) */
    __HAL_RCC_TIM3_CLK_ENABLE();
    __HAL_RCC_TIM3_FORCE_RESET();
    __HAL_RCC_TIM3_RELEASE_RESET();
    __HAL_RCC_TIM3_CLK_DISABLE();

    /* ---- Configure RGB LED pins ---- */
    GPIO_InitTypeDef gpio = {0};
    gpio.Mode  = GPIO_MODE_OUTPUT_PP;
    gpio.Pull  = GPIO_NOPULL;
    gpio.Speed = GPIO_SPEED_FREQ_LOW;

    gpio.Pin = GPIO_PIN_0;
    HAL_GPIO_Init(GPIOB, &gpio);
    gpio.Pin = GPIO_PIN_6 | GPIO_PIN_7;
    HAL_GPIO_Init(GPIOA, &gpio);

    HAL_GPIO_WritePin(GPIOB, GPIO_PIN_0, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(GPIOA, GPIO_PIN_6 | GPIO_PIN_7, GPIO_PIN_RESET);

    led_step = 0;
}

void App_Loop(void)
{
    HAL_GPIO_WritePin(LED_R_PORT, LED_R_PIN, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(LED_G_PORT, LED_G_PIN, GPIO_PIN_RESET);
    HAL_GPIO_WritePin(LED_B_PORT, LED_B_PIN, GPIO_PIN_RESET);

    switch (led_step) {
    case 0: HAL_GPIO_WritePin(LED_R_PORT, LED_R_PIN, GPIO_PIN_SET); break;
    case 1: HAL_GPIO_WritePin(LED_G_PORT, LED_G_PIN, GPIO_PIN_SET); break;
    case 2: HAL_GPIO_WritePin(LED_B_PORT, LED_B_PIN, GPIO_PIN_SET); break;
    }

    HAL_Delay(500);
    led_step = (led_step + 1) % 3;
}