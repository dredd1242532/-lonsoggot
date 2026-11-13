/* The native give/count boundary shared by Debug and temporary co-op kits. */
#pragma once
#include "a64.h"
int oot_pouch_change(Ctx *c, const char *name, int delta);
int oot_pouch_count(Ctx *c, const char *name);
