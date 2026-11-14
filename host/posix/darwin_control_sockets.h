/* (longshot) macOS-only parts of the control bridge's Unix transport. */
#pragma once
#if defined(__APPLE__)
#include <sys/socket.h>
#include <sys/types.h>
#include <sys/un.h>

/* The caller has checked the path against sizeof address->sun_path and copied it. Includes its terminating NUL. */
socklen_t hos_darwin_control_address_length(struct sockaddr_un *address);
/* Nonblocking and close-on-exec before returning; a failed setup closes the descriptor. Not atomic with exec. */
int hos_darwin_control_socket(void);
/* Queue bursts for the bridge's bounded busy replies; Darwin refuses connect when the listen queue is full. */
int hos_darwin_control_listen(int fd);
/* 1 live, 0 connection refused (stale), -1 not known: never remove an existing socket on an uncertain result. */
int hos_darwin_control_probe(const struct sockaddr_un *address);
/* A configured connection from the kernel-verified user only. EACCES means unverified or another user; closed. */
int hos_darwin_control_accept(int listener, uid_t user);
/* After read EOF: the peer's receiving side is gone too. Sends no bytes and cannot raise SIGPIPE. */
int hos_darwin_control_closed(int fd);
#endif
