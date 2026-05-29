#ifndef APP_MAIN_H
#define APP_MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

#include "stm32f1xx_hal.h"

void App_Init(void);
void App_Loop(void);

#ifdef __cplusplus
}
#endif

#endif /* APP_MAIN_H */
