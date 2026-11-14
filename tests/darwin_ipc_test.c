/* The real IPC client's private functions against an authored, temporary local listener.
   No presence thread, Discord handshake, application/account request, or real Discord socket is used. */
#ifndef DARWIN_IPC_SOURCE
#define DARWIN_IPC_SOURCE "../host/discord_rpc.c"
#endif
#include DARWIN_IPC_SOURCE
#include <signal.h>
#include <sys/stat.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL IPC line=%d: %s errno=%d\n", __LINE__, #x, errno); exit(1); } } while (0)
static volatile sig_atomic_t pipes;
static void pipe_signal(int sig) { (void)sig; pipes++; }
int main(int argc, char **argv) {
    CHECK(argc == 2);
    CHECK(setenv("XDG_RUNTIME_DIR", argv[1], 1) == 0);
    struct sockaddr_un address = {0};
    address.sun_family = AF_UNIX;
    const int length = snprintf(address.sun_path, sizeof address.sun_path, "%s/discord-ipc-0", argv[1]);
    CHECK(length > 0 && length < (int)sizeof address.sun_path);
    address.sun_len = (unsigned char)(offsetof(struct sockaddr_un, sun_path) + (size_t)length + 1);
    const int listener = socket(AF_UNIX, SOCK_STREAM, 0);
    CHECK(listener >= 0 && bind(listener, (struct sockaddr *)&address, address.sun_len) == 0 && listen(listener, 1) == 0);
    Ipc client = ipc_connect(0);
    CHECK(client != IPC_NONE && (fcntl(client, F_GETFD) & FD_CLOEXEC));
    const int peer = accept(listener, NULL, NULL);
    CHECK(peer >= 0);
    const char text[] = "authored IPC bytes";
    CHECK(ipc_write(client, text, sizeof text));
    char received[sizeof text];
    CHECK(read(peer, received, sizeof received) == sizeof received && !memcmp(text, received, sizeof text));
    struct sigaction signal_action = {0};
    signal_action.sa_handler = pipe_signal;
    sigemptyset(&signal_action.sa_mask);
    CHECK(sigaction(SIGPIPE, &signal_action, NULL) == 0);
    CHECK(shutdown(peer, SHUT_RDWR) == 0);
    close(peer);
    int wrote = 1;
    for (int i = 0; i < 10 && wrote; i++) wrote = ipc_write(client, text, sizeof text);
    CHECK(!wrote && pipes == 0);
    ipc_close(client);
    close(listener);
    CHECK(unlink(address.sun_path) == 0);
    char too_long[512];
    memset(too_long, 'x', sizeof too_long - 1);
    too_long[sizeof too_long - 1] = 0;
    CHECK(setenv("XDG_RUNTIME_DIR", too_long, 1) == 0);
    errno = 0;
    CHECK(ipc_connect(0) == IPC_NONE && errno == ENAMETOOLONG);
    puts("darwin_ipc PASS local_substitute=1 exact_sockaddr_length=1 cloexec=1 overlength_refused=1 no_sigpipe=1 Discord_connections=0");
    return 0;
}
