#include "app_main.h"

#include "cmsis_os.h"

void App_DefaultTask(void *argument)
{
    (void)argument;

    for (;;) {
        osDelay(1);
    }
}
