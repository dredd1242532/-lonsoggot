/* (longshot) Achievements (docs/achievements.md): the list is gpu/vk/oot_achievements.h, the menu's side (the pop-up, the
   Achievement pop-ups row) gpu/vk/vk_menu.cpp.

   host/hos_main.c oot_inventory_refresh calls hos_oot_achievements_check twice a second with the Inventory page's counts, on
   Link's update under its lock, so one thread only: no guest reads of its own, no game hook. A met condition unlocks its
   achievement once, appends it to the unlock file and tells the menu (rx_option_set). Nothing unlocks without a save loaded,
   or once a debug tool has been used in this run (hos_oot_achievements_cheated, from host/hos_main.c's cheat actions: the
   inventory editor, teleport, the age switch, the clock): they would hand them out. The menu is told
   (OOT_ACHIEVEMENTS_PAUSED_ID) for the Achievements page's notice.

   The unlock file, user/achievements.txt beside the saves (hos_user_dir), one line an unlock: "<id> <UTC time>", as
   "song_zelda 2026-11-14T18:03:22Z". Read once; written whole to achievements.txt.tmp, then moved over it, so a crash leaves
   the old file or the new one. Lines with ids this build does not know (a newer build's) are kept as they are. */
#include "hos_paths.h"   /* (first: windows.h, host/posix's on Linux: MoveFileExA) */
#include <stdio.h>
#include <string.h>
#include <time.h>
#include "../gpu/vk/oot_achievements.h"   /* (the list, and OOT_ACHIEVEMENTS_PAUSED_ID) */

void hos_log(const char *fmt, ...);
void rx_option_set(int id, int value);
extern const char *hos_user_dir;

typedef struct {
    const char *id, *title;
    int mode;
    const char *item[3];
} OotAchievement;

#define OOT_ACH_ROW(id, title, description, mode, a, b, c) {id, title, mode, {a, b, c}},
static const OotAchievement ach_list[] = {OOT_ACHIEVEMENTS(OOT_ACH_ROW)};
#define ACH_N ((int)(sizeof ach_list / sizeof ach_list[0]))

#define ACH_WHEN 24          /* "2026-11-14T18:03:22Z" and its 0 */
#define ACH_KEPT 4096        /* the unknown ids' lines, kept as they are */
static char ach_when[ACH_N][ACH_WHEN];   /* "" while locked */
static char ach_kept[ACH_KEPT];
static size_t ach_kept_n;
static int ach_loaded, ach_paused, ach_save_failed;

/* A debug tool's action ran (host/hos_main.c oot_update_player_cheats): nothing unlocks for the rest of this run */
void hos_oot_achievements_cheated(void) {
    if (ach_paused) return;
    ach_paused = 1;
    hos_log("achievements: paused: a debug tool was used in this run");
    rx_option_set(OOT_ACHIEVEMENTS_PAUSED_ID, 1);
}

static void ach_path(char *out, size_t cap, const char *suffix) {
    snprintf(out, cap, "%s/achievements.txt%s", hos_user_dir ? hos_user_dir : "user", suffix);
}

static int ach_index(const char *id) {
    for (int i = 0; i < ACH_N; i++)
        if (!strcmp(ach_list[i].id, id)) return i;
    return -1;
}

/* The unlock file into ach_when and ach_kept; a missing file is no unlocks yet */
static void ach_load(void) {
    char path[512], line[256];
    ach_path(path, sizeof path, "");
    FILE *f = fopen(path, "rb");
    if (!f) return;
    int known = 0;
    while (fgets(line, sizeof line, f)) {
        char id[64], when[ACH_WHEN];
        if (sscanf(line, "%63s %23s", id, when) != 2) continue;   /* (a blank or torn line) */
        const int i = ach_index(id);
        if (i >= 0) {
            if (!ach_when[i][0]) snprintf(ach_when[i], sizeof ach_when[i], "%s", when), known++;
        } else {
            const size_t n = strlen(line), nl = n && line[n - 1] == '\n' ? 0 : 1;
            if (ach_kept_n + n + nl < sizeof ach_kept) {
                memcpy(ach_kept + ach_kept_n, line, n), ach_kept_n += n;
                if (nl) ach_kept[ach_kept_n++] = '\n';
                ach_kept[ach_kept_n] = 0;
            }
        }
    }
    fclose(f);
    hos_log("achievements: %d of %d unlocked (%s)", known, ACH_N, path);
}

/* The whole file again: the temporary file first, then moved over the old one. 1 when it was written */
static int ach_save(void) {
    char path[512], tmp[512];
    ach_path(path, sizeof path, "");
    ach_path(tmp, sizeof tmp, ".tmp");
    FILE *f = fopen(tmp, "wb");
    if (!f) return 0;
    int ok = 1;
    for (int i = 0; i < ACH_N; i++)
        if (ach_when[i][0] && fprintf(f, "%s %s\n", ach_list[i].id, ach_when[i]) < 0) ok = 0;
    if (ach_kept_n && fwrite(ach_kept, 1, ach_kept_n, f) != ach_kept_n) ok = 0;
    if (fclose(f)) ok = 0;
    if (ok) {
#ifdef _WIN32
        ok = MoveFileExA(tmp, path, MOVEFILE_REPLACE_EXISTING) != 0;
#else
        ok = rename(tmp, path) == 0;
#endif
    }
    if (!ok) remove(tmp);
    return ok;
}

/* Seconds since 1970 as "2026-11-14T18:03:22Z": the civil date by days (Howard Hinnant's days_from_civil, inverted), so no
   gmtime and its thread or platform differences */
static void ach_stamp(char *out, size_t cap, long long t) {
    long long days = t / 86400, secs = t % 86400;
    if (secs < 0) secs += 86400, days--;
    days += 719468;
    const long long era = (days >= 0 ? days : days - 146096) / 146097, doe = days - era * 146097;
    const long long yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365, doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    const long long mp = (5 * doy + 2) / 153, d = doy - (153 * mp + 2) / 5 + 1, m = mp < 10 ? mp + 3 : mp - 9;
    const long long y = yoe + era * 400 + (m <= 2);
    snprintf(out, cap, "%04lld-%02lld-%02lldT%02lld:%02lld:%02lldZ", y, m, d, secs / 3600, secs / 60 % 60, secs % 60);
}

static int ach_count_of(const char *name, const char *const *names, const int *counts, int n) {
    for (int i = 0; i < n; i++)
        if (!strcmp(names[i], name)) return counts[i];
    return -1;
}

/* Whether Link holds what achievement a asks for */
static int ach_met(const OotAchievement *a, const char *const *names, const int *counts, int n) {
    int any = 0, all = 1, asked = 0;
    for (int k = 0; k < 3; k++) {
        if (!a->item[k][0]) continue;
        asked++;
        const int held = ach_count_of(a->item[k], names, counts, n) > 0;
        any |= held, all &= held;
    }
    return asked && (a->mode == OOT_ACH_ANY ? any : all);
}

/* Each refresh of the Inventory page's counts (names[i] holds counts[i], -1 unknown). loaded: a save's counts were read */
void hos_oot_achievements_check(const char *const *names, const int *counts, int n, int loaded) {
    if (!ach_loaded) {   /* (once: the file's unlocks to the menu, before anything new) */
        ach_loaded = 1;
        ach_load();
        for (int i = 0; i < ACH_N; i++)
            if (ach_when[i][0]) rx_option_set(OOT_ACHIEVEMENT_BASE + i, 1);
    }
    if (!loaded || !names || !counts || n <= 0 || ach_paused) return;
    int changed = 0;
    for (int i = 0; i < ACH_N; i++) {
        if (ach_when[i][0] || !ach_met(&ach_list[i], names, counts, n)) continue;
        ach_stamp(ach_when[i], sizeof ach_when[i], (long long)time(NULL));
        changed = 1;
        hos_log("achievements: unlocked %s (%s)", ach_list[i].id, ach_list[i].title);
        rx_option_set(OOT_ACHIEVEMENT_BASE + i, 1);
        rx_option_set(OOT_ACHIEVEMENT_TOAST_ID, i);
    }
    if (changed && !ach_save() && !ach_save_failed) {
        ach_save_failed = 1;   /* (the unlocks stay for this run; the next one that saves writes them all) */
        hos_log("achievements: could not write the unlock file in %s", hos_user_dir ? hos_user_dir : "user");
    }
}
