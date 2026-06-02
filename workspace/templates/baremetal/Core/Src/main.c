#include "stm32f1xx_hal.h"
#include "app_main.h"

static void SystemClock_Config(void)
{
    /* Family-neutral default: use reset clock until the app configures clocks. */
}

int main(void)
{
    HAL_Init();
    SystemClock_Config();
    App_Init();

    while (1) {
        App_Loop();
    }
}
