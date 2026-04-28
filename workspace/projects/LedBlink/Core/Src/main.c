#include "app_main.h"

int main(void)
{
    app_main_init();

    while (1) {
        app_main_loop();
    }

    return 0;
}
