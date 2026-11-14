/* The actual hos_main.c notice functions, compiled without the game or display. Any GUI/SDL call fails. */
#include <windows.h>
#include "../gpu/vk/compat/longshot_env.h"
#include <errno.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/sysctl.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL notice line=%d: %s\n", __LINE__, #x); exit(1); } } while (0)
static unsigned logged, dynamic_swap, queried;
void hos_log(const char *format, ...) {
    char line[1024];
    va_list ap;
    va_start(ap, format);
    vsnprintf(line, sizeof line, format, ap);
    va_end(ap);
    logged++;
    dynamic_swap |= strstr(line, "macOS manages swap dynamically") != NULL;
    queried |= strstr(line, "MB of memory") != NULL;
    CHECK(strstr(line, "cannot query") == NULL && strstr(line, "unavailable") == NULL);
}
int hos_sdl_load(void) { CHECK(0); return 0; }
static int forbidden_message_box(HWND w, LPCSTR text, LPCSTR caption, UINT type) {
    (void)w; (void)text; (void)caption; (void)type;
    CHECK(0);
    return 0;
}
#define MessageBoxA forbidden_message_box
#define HOS_MEMORY_NEEDED_MB 10240ull
#include "darwin_memory_notice.inc"
int main(void) {
    CHECK(setenv("HOS_HEADLESS", "1", 1) == 0);
    memory_notice();
    CHECK(logged >= 2 && dynamic_swap && queried);
    char text[512];
    CHECK(memory_notice_text(8u << 10, 32u << 10, text, sizeof text));
    CHECK(strstr(text, "This Mac") && strstr(text, "manages swap dynamically") && strstr(text, "Close other programs"));
    CHECK(!memory_notice_text(16u << 10, 0, text, sizeof text));
    puts("darwin_memory_notice PASS native_queries=RAM_and_allocated_swap physical_RAM_floor=1 dynamic_swap_text=1 GUI_calls=0");
    return 0;
}
