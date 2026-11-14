/* (longshot) macOS Unix sockets for host/control.c. Public APIs: fcntl(2), connect(2), accept(2), getpeereid(3),
   and sys/un.h's sockaddr_un. No Linux socket-type flags or accept4 on this platform. */
#if defined(__APPLE__)
#include "darwin_control_sockets.h"
#include <errno.h>
#include <fcntl.h>
#include <stddef.h>
#include <string.h>
#include <unistd.h>

static int darwin_control_flags(int fd) {
    if (fd < 0) return -1;
    /* There is a window from socket/accept to F_SETFD. A future macOS relaunch must quiesce creation/accept or use
       a child-side close-all policy: fcntl does not replace Linux's atomic CLOEXEC. Set it before any other work. */
    int flags = fcntl(fd, F_GETFD);
    if (flags < 0 || fcntl(fd, F_SETFD, flags | FD_CLOEXEC) < 0) goto failed;
    flags = fcntl(fd, F_GETFL);
    if (flags < 0 || fcntl(fd, F_SETFL, flags | O_NONBLOCK) < 0) goto failed;
    return fd;
failed:
    {
        const int error = errno;
        close(fd);
        errno = error;
        return -1;
    }
}

socklen_t hos_darwin_control_address_length(struct sockaddr_un *address) {
    address->sun_len = (unsigned char)(offsetof(struct sockaddr_un, sun_path) + strlen(address->sun_path) + 1);
    return address->sun_len;
}

int hos_darwin_control_socket(void) {
    return darwin_control_flags(socket(AF_UNIX, SOCK_STREAM, 0));
}

int hos_darwin_control_listen(int fd) {
    return listen(fd, SOMAXCONN);
}

int hos_darwin_control_probe(const struct sockaddr_un *address) {
    /* A stream connect to a full Darwin listen queue returns ECONNREFUSED too: it cannot prove staleness.
       A datagram probe instead gets EPROTOTYPE for a live stream socket, without entering its accept queue. */
    const int fd = darwin_control_flags(socket(AF_UNIX, SOCK_DGRAM, 0));
    if (fd < 0) return -1;
    const int connected = connect(fd, (const struct sockaddr *)address, address->sun_len);
    const int error = errno;
    close(fd);
    if (!connected || error == EPROTOTYPE) return 1;
    errno = error;
    return error == ECONNREFUSED ? 0 : -1;
}

static int darwin_control_peer(int fd, uid_t user) {
    uid_t peer;
    gid_t group;
    return getpeereid(fd, &peer, &group) == 0 && peer == user;
}

int hos_darwin_control_accept(int listener, uid_t user) {
    const int fd = darwin_control_flags(accept(listener, NULL, NULL));
    if (fd < 0) return -1;
    if (!darwin_control_peer(fd, user)) {
        close(fd);
        errno = EACCES;
        return -1;
    }
    return fd;
}

int hos_darwin_control_closed(int fd) {
    /* Darwin poll reports read EOF as POLLHUP even for a half-close, and reports nothing with events=0.
       A zero-byte send tests the remaining direction without adding bytes to the JSON stream. */
    return send(fd, "", 0, MSG_NOSIGNAL | MSG_DONTWAIT) < 0 &&
           errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR;
}
#endif
