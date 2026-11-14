// SPDX-License-Identifier: GPL-3.0-or-later
// (longshot) VK_EXT_memory_priority's rule (RX_VK_MEMORY_PRIORITY, off by default), apart from Vulkan so that
// tests/test_memory_priority.py can run it.
//
// When other programs hold VRAM, or the card is too small for what is loaded, the system moves part of the game's device memory
// out of VRAM (Windows: to shared memory, which the GPU reads over PCIe). It chooses what goes. The priority given with each
// vkAllocateMemory, 0 to 1, is the one say the game has: memory with a higher one "may be more likely to stay in device-local
// memory" (the extension's words; a driver may ignore it). Left alone everything has 0.5.
//
// The rule: the render targets' memory, written and read every frame, above everything; the sampled textures', the largest
// part and the one the cache already lets go of first, below everything; the rest (buffers, DLSS's and the frame
// interpolation's images, the scratch ones) where it was. A priority is a memory object's, so a pool's block has its pool's.
//
// Measured on one PC (an RTX 4090 on NVIDIA 621.24, Windows 11; the game standing in Hyrule Field while other processes
// took the VRAM; docs/tested/2026-11-14-memory-priority-*): the textures' lower priority is what helps. 0.5,0.25 did as well
// as 1,0.25, and 1,0.5 (the render targets raised alone) lost its gain within 90 s in its one run. No small card has run it
// yet, which is why it is off.
#pragma once

#include <algorithm>
#include <cstdlib>
#include <cstring>

#include "compat/longshot_env.h"

namespace memory_priority {

struct Tiers {
    bool on = false;
    float targets = 0.5f, textures = 0.5f, rest = 0.5f;   // (0.5: what the driver gives memory allocated without one)
};

/// RX_VK_MEMORY_PRIORITY as the environment has it. Unset, empty, 0, false, off or no: off, nothing asked of the driver.
/// 1 (or anything else): the render targets 1, the textures 0.25. <targets>,<textures>: those two instead, each held to 0..1
/// (a test: 1,0.5 raises the render targets alone)
inline Tiers parse(const char* env) {
    Tiers t;
    if (!env || env_value_off(env)) return t;
    t.on = true, t.targets = 1.0f, t.textures = 0.25f;
    if (const char* comma = strchr(env, ',')) {
        t.targets = (float)std::clamp(atof(env), 0.0, 1.0);
        t.textures = (float)std::clamp(atof(comma + 1), 0.0, 1.0);
    }
    return t;
}

/// An image's: a render target's, a sampled texture's (the textures' pool's), or the rest's
inline float image(const Tiers& t, bool target, bool textures) { return target ? t.targets : textures ? t.textures : t.rest; }

}   // namespace memory_priority
