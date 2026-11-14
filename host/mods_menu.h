/* F1 menu access to RomFS mod selections. ExeFS loading is unaffected. */
#pragma once

#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

int hos_mods_romfs_menu_count(void);
int hos_mods_romfs_menu_get(int index, char *name, size_t cap, int *enabled, int *has_exefs);
int hos_mods_romfs_menu_set(int index, int enabled);
int hos_mods_romfs_menu_changed(void);
int hos_mods_romfs_menu_reorder(int from, int to);

#ifdef __cplusplus
}
#endif
