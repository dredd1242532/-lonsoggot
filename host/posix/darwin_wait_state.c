/* Executable-owned Darwin address-wait state. GPL-3.0-or-later.
   Link only into the host, not its independently linked renderer shim. */
#if defined(__APPLE__)
#include "darwin_wait.h"

uint32_t *hos_posix_address_wait_sequences(void) {
    static uint32_t sequences[DARWIN_ADDRESS_WAIT_SEQUENCE_COUNT];
    return sequences;
}
#endif
