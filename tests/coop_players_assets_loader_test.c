/* Authored host-context adapter for test_coop_players_assets.py.
   mods.c is compiled unchanged; directory enumeration and file indexing are real.
   No game, guest calls, asset decoding or prompt generation runs here. */
#include <stdarg.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

const char *hos_romfs_dir;

void hos_log(const char *fmt, ...) {
    va_list ap;
    va_start(ap, fmt);
    fputs("LOG\t", stdout);
    vprintf(fmt, ap);
    putchar('\n');
    va_end(ap);
}

int hos_ini_option(const char *key, int otherwise) {
    const char *value = getenv("TEST_PLAYERS_OPTION");
    return !strcmp(key, "coop_players") && value ? atoi(value) : otherwise;
}

int hos_prompts_romfs(const char *key, const char *mod, char *out, size_t cap) {
    (void)key; (void)mod; (void)out; (void)cap;
    return 0;
}

void hos_mods_init(const char *image_path);
int hos_coop_players_assets(void);
int hos_mods_file(const char *rel, char *out, size_t cap);

int main(int argc, char **argv) {
    hos_romfs_dir = getenv("TEST_ROMFS");
    hos_mods_init(NULL);
    printf("READY\t%d\n", hos_coop_players_assets());
    for (int i = 1; i < argc; i++) {
        char out[4096];
        const int found = hos_mods_file(argv[i], out, sizeof out);
        printf("FILE\t%s\t%s\n", argv[i], found ? out : "(base)");
    }
    return 0;
}
