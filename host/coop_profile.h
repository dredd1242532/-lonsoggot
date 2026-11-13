/* Local-only temporary save storage. No caller may supply a network path.
   The normal user root is never renamed or replaced. Initialize before any mount;
   a one-use restart token selects an owned complete copy, never a partial folder. */
#pragma once
#include <stddef.h>
#include <stdint.h>

enum { COOP_PROFILE_PATH = 1024, COOP_PROFILE_ID = 32, COOP_PROFILE_LIMIT = 16,
       COOP_PROFILE_BYTES = 64 * 1024 * 1024 };
/* startup_token is passed only by the local relaunch path; NULL is an ordinary
   launch (including after a crash), which always selects the normal root. */
int coop_profile_init(const char *normal, const char *startup_token);
const char *coop_profile_root(void);
int coop_profile_active(void);
const char *coop_profile_note(void);
/* The caller holds the native save barrier. Copies only normal/save, never keys,
   options outside the save mount, account identities or arbitrary user files.
   1 queued for restart; 0 failed. No mount changes in the current process. */
int coop_profile_prepare(void);
int coop_profile_return(void);
/* One-use token for the process restart, not a public setting. Empty on a normal
   restart. A failed relaunch calls failed() so the pending ticket cannot be used. */
const char *coop_profile_restart_token(void);
void coop_profile_restart_failed(void);
/* Reject links/reparse points in every existing path component. Missing leaves
   may be created. Private owned directories are not a same-user sandbox. */
int coop_profile_path_safe(const char *path);
/* Bounded opaque two-file staging for save transfer. id is local random hex.
   No filenames, archive entries or account/settings files come from the wire. */
int coop_profile_stage(char id[COOP_PROFILE_ID + 1]);
int coop_profile_chunk(const char *id, unsigned file, uint32_t offset, const void *bytes, size_t n);
int coop_profile_read(const char *id, unsigned file, uint32_t offset, void *bytes, size_t n);
int coop_profile_publish(const char *id);
void coop_profile_abandon(const char *id);
struct CoopSaveSource;
/* Locally selected slot, captured under nn_fs.c's save barrier. Only progress.sav
   and caption.sav travel; the save's local account/options never do. */
int coop_profile_export(const char *save_root, unsigned slot, uint64_t generation, struct CoopSaveSource *out);
int coop_profile_export_read(const char *id, unsigned file, uint32_t offset, void *out, size_t n);
int coop_profile_seal(const char *id, const uint8_t manifest[352]);
