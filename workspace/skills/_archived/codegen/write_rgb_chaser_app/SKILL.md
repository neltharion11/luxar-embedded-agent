---
title: Write RGB Chaser App
category: codegen
---

# Write RGB Chaser App for Project 2

## Description
Writes a new app_main.c with RGB LED chaser (flowing light) using PB0(R), PB6(G), PB7(B) on common-cathode RGB LED.

## File to write
C:\Users\Gugugu\Documents\Codex\LUXAR\workspace\projects\2\app_main.c

## Content

```c
#include "stm32f103x8.h"

/* RGB LED pins - common cathode (HIGH = ON) */
#define LED_R  (1 << 0)   /* PB0 - Red   */
#define LED_G  (1 << 6)   /* PB6 - Green */
#define LED_B  (1 << 7)   /* PB7 - Blue  */

#define LED_ALL (LED_R | LED_G | LED_B)

/* Simple busy-loop delay (~200ms at 8MHz HSI) */
static void delay(volatile uint32_t count)
{
    while (count--)
    {
        for (volatile uint32_t i = 0; i < 1000; i++);
    }
}

/* Color patterns */
typedef enum {
    COLOR_RED     = LED_R,
    COLOR_GREEN   = LED_G,
    COLOR_BLUE    = LED_B,
    COLOR_YELLOW  = LED_R | LED_G,
    COLOR_MAGENTA = LED_R | LED_B,
    COLOR_CYAN    = LED_G | LED_B,
    COLOR_WHITE   = LED_ALL,
    COLOR_OFF     = 0
} color_t;

static const color_t chaser[] = {
    COLOR_RED,
    COLOR_GREEN,
    COLOR_BLUE,
    COLOR_YELLOW,
    COLOR_MAGENTA,
    COLOR_CYAN,
    COLOR_WHITE,
    COLOR_OFF
};

static const int chaser_len = sizeof(chaser) / sizeof(chaser[0]);

int main(void)
{
    /* Enable GPIOB clock (APB2, bit 3) */
    RCC->APB2ENR |= RCC_APB2ENR_IOPBEN;

    /* Configure PB0, PB6, PB7 as push-pull output, 10MHz */
    /* PB0 is in CRL (bits 3:0 -> CNF0=00, MODE0=01 = 10MHz output) */
    GPIOB->CRL &= ~(GPIO_CRL_CNF0 | GPIO_CRL_MODE0);
    GPIOB->CRL |= GPIO_CRL_MODE0_0;  /* 10MHz output, push-pull */

    /* PB6, PB7 are in CRH (bits 31:24) */
    GPIOB->CRH &= ~(GPIO_CRH_CNF6 | GPIO_CRH_MODE6 | GPIO_CRH_CNF7 | GPIO_CRH_MODE7);
    GPIOB->CRH |= (GPIO_CRH_MODE6_0 | GPIO_CRH_MODE7_0);  /* 10MHz output, push-pull */

    /* Start with all LEDs off */
    GPIOB->BSRR = LED_ALL;  /* BSRR: set = on for common-cathode... wait, no. */
    /* Actually for common cathode: GPIO ODR high = LED on, low = LED off */
    /* BSRR: writing 1 to lower 16 bits sets the pin (ODR high) */
    /* Writing 1 to upper 16 bits resets the pin (ODR low) */
    /* So we want BR (upper 16 bits) to turn OFF initially */
    GPIOB->BRR = LED_ALL;   /* BRR resets pins -> all OFF */

    while (1)
    {
        for (int i = 0; i < chaser_len; i++)
        {
            /* Turn all off first */
            GPIOB->BRR = LED_ALL;
            /* Set the current color pattern */
            GPIOB->BSRR = chaser[i];
            delay(200);
        }
    }
}
```
