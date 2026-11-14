/* The macOS transport's real descriptors and failure paths, without a game or another account. Including the
   implementation lets this single-threaded fixture inspect listener/accepted state without production test hooks.
   The ordinary control_test.c still runs the full threaded protocol fixture. */
#if defined(__APPLE__)
#include "../host/posix/darwin_control_sockets.c"
#include "../host/control.c"
#include <signal.h>
#include <sys/wait.h>

#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL:%d %s (errno=%d)\n", __LINE__, #x, errno); exit(1); } } while (0)

static char last_log[600];
static void test_log(const char *line) { snprintf(last_log, sizeof last_log, "%s", line); }
static const HosControlGame test_game = {.log = test_log};
static volatile sig_atomic_t pipe_signals;
static void pipe_signal(int sig) { (void)sig; pipe_signals++; }

static void sized_path(char *path, size_t length, const char *folder) {
    const size_t prefix = strlen(folder);
    CHECK(prefix + 1 < length);
    memcpy(path, folder, prefix);
    path[prefix] = '/';
    memset(path + prefix + 1, 's', length - prefix - 1);
    path[length] = 0;
}

static void check_flags(int fd) {
    const int descriptor = fcntl(fd, F_GETFD), status = fcntl(fd, F_GETFL);
    CHECK(descriptor >= 0 && (descriptor & FD_CLOEXEC));
    CHECK(status >= 0 && (status & O_NONBLOCK));
}

static int connect_client(const struct sockaddr_un *address) {
    const int fd = socket(AF_UNIX, SOCK_STREAM, 0);
    CHECK(fd >= 0);
    CHECK(connect(fd, (const struct sockaddr *)address, address->sun_len) == 0);
    return fd;
}

static void check_exec(const char *exe, int accepted, int probe) {
    /* An unmarked descriptor must survive: this checks exec, not a fixture that blindly closed every descriptor. */
    const int kept = open("/dev/null", O_RDONLY);
    CHECK(kept >= 0 && fcntl(kept, F_GETFD) == 0);
    char l[24], a[24], p[24], k[24];
    snprintf(l, sizeof l, "%d", listen_fd);
    snprintf(a, sizeof a, "%d", accepted);
    snprintf(p, sizeof p, "%d", probe);
    snprintf(k, sizeof k, "%d", kept);
    fflush(NULL);
    const pid_t child = fork();
    CHECK(child >= 0);
    if (!child) {
        execl(exe, exe, "exec-check", l, a, p, k, (char *)NULL);
        _exit(127);
    }
    int status;
    CHECK(waitpid(child, &status, 0) == child && WIFEXITED(status) && WEXITSTATUS(status) == 0);
    close(kept);
}

int main(int argc, char **argv) {
    if (argc == 6 && !strcmp(argv[1], "exec-check")) {
        for (int i = 2; i < 5; i++) CHECK(fcntl(atoi(argv[i]), F_GETFD) == -1 && errno == EBADF);
        CHECK(fcntl(atoi(argv[5]), F_GETFD) == 0);
        puts("PASS: macOS exec closes listener, accepted and probe sockets; an unmarked descriptor survives");
        return 0;
    }
    CHECK(argc == 2);
    struct stat st;
    CHECK(!stat(argv[1], &st) && S_ISDIR(st.st_mode) && st.st_uid == getuid() && (st.st_mode & 0777) == 0700);
    struct sockaddr_un address = {0};
    const size_t capacity = sizeof address.sun_path;
    CHECK(capacity == 104);
    char too_long[sizeof address.sun_path + 1];
    sized_path(too_long, capacity, argv[1]);
    FILE *file = fopen(too_long, "w");
    CHECK(file && fputs("keep", file) >= 0 && !fclose(file));
    CHECK(!hos_control_serve(too_long, &test_game));
    CHECK(!G.game && !hos_control_on && listen_fd == -1 && strstr(last_log, "the socket's path is too long"));
    char kept[8] = {0};
    file = fopen(too_long, "r");
    CHECK(file && fgets(kept, sizeof kept, file) && !strcmp(kept, "keep") && !fclose(file));
    CHECK(!unlink(too_long));
    puts("PASS: macOS 104-byte path refused before opening or replacing anything");

    sized_path(address.sun_path, capacity - 1, argv[1]);
    address.sun_family = AF_UNIX;
    const socklen_t length = hos_darwin_control_address_length(&address);
    CHECK(length == offsetof(struct sockaddr_un, sun_path) + capacity && address.sun_len == length);
    const int other_listener = hos_darwin_control_socket();
    CHECK(other_listener >= 0);
    check_flags(other_listener);
    CHECK(!bind(other_listener, (struct sockaddr *)&address, length) && !listen(other_listener, 4));
    CHECK(!lstat(address.sun_path, &st));
    const ino_t live_inode = st.st_ino;
    G.game = &test_game;
    CHECK(!transport_open(address.sun_path));
    CHECK(strstr(last_log, "another game listens there") && listen_fd == -1);
    CHECK(!lstat(address.sun_path, &st) && st.st_ino == live_inode);
    int queued[4];
    for (int i = 0; i < 4; i++) queued[i] = connect_client(&address);
    CHECK(!transport_open(address.sun_path));   /* A full but live accept queue is not a stale socket. */
    CHECK(strstr(last_log, "another game listens there") && listen_fd == -1);
    CHECK(!lstat(address.sun_path, &st) && st.st_ino == live_inode);
    puts("PASS: macOS a full live listen queue is not unlinked as stale");
    close(other_listener);   /* Leave a real stale socket to be replaced, not a fake file. */
    for (int i = 0; i < 4; i++) close(queued[i]);
    CHECK(transport_open(address.sun_path));
    snprintf(G.path, sizeof G.path, "%s", address.sun_path);
    CHECK(!lstat(G.path, &st) && S_ISSOCK(st.st_mode) && (st.st_mode & 0777) == 0600);
    puts("PASS: macOS 103-byte path with sun_len; live socket kept, stale socket replaced, mode 0600");
    check_flags(listen_fd);
    CHECK(hos_darwin_control_accept(listen_fd, getuid()) == -1 && (errno == EAGAIN || errno == EWOULDBLOCK));

    int burst[CTL_CONNS + 4];
    for (int i = 0; i < CTL_CONNS + 4; i++) burst[i] = connect_client(&address);
    for (int i = 0; i < CTL_CONNS + 4; i++) transport_wait(0);
    for (int i = 0; i < CTL_CONNS; i++) CHECK(G.conns[i].used);
    for (int i = CTL_CONNS; i < CTL_CONNS + 4; i++) {
        char reply[256] = {0};
        struct pollfd ready = {burst[i], POLLIN, 0};
        CHECK(poll(&ready, 1, 1000) == 1 && read(burst[i], reply, sizeof reply - 1) > 0);
        CHECK(strstr(reply, "\"error\":\"busy\"") && strstr(reply, "too many clients"));
    }
    for (int i = 0; i < CTL_CONNS + 4; i++) close(burst[i]);
    for (int i = 0; i < CTL_CONNS; i++) conn_close(i);
    puts("PASS: macOS a queued burst reaches eight clients and four busy replies");

    /* The expected UID is deliberately different, but getpeereid still reads this connection from the kernel.
       This proves the refusal branch and close, not a real connection from a second user. */
    const int denied_client = connect_client(&address);
    const int next_fd = dup(denied_client);
    CHECK(next_fd >= 0 && !close(next_fd));
    CHECK(hos_darwin_control_accept(listen_fd, getuid() ^ 1u) == -1 && errno == EACCES);
    CHECK(fcntl(next_fd, F_GETFD) == -1 && errno == EBADF);
    struct pollfd denied = {denied_client, POLLIN, 0};
    char byte;
    CHECK(poll(&denied, 1, 1000) == 1 && read(denied_client, &byte, 1) == 0);
    close(denied_client);
    CHECK(!darwin_control_peer(-1, getuid()) && errno == EBADF);
    const int unconnected = hos_darwin_control_socket();
    CHECK(unconnected >= 0 && !darwin_control_peer(unconnected, getuid()) && errno == ENOTCONN);
    close(unconnected);
    puts("PASS: macOS kernel peer UID mismatch closes the accepted socket; unknown credentials fail closed");

    const int client = connect_client(&address);
    transport_wait(1000);   /* The actual transport admits the kernel-verified same-user connection. */
    Conn *connection = &G.conns[0];
    CHECK(connection->used && connection->fd >= 0 && darwin_control_peer(connection->fd, getuid()));
    check_flags(connection->fd);
    CHECK(recv(connection->fd, &byte, 1, 0) == -1 && (errno == EAGAIN || errno == EWOULDBLOCK));
    puts("PASS: macOS listener and accepted descriptors are nonblocking and close-on-exec; empty accept/read return EAGAIN");

    const int probe = darwin_control_flags(socket(AF_UNIX, SOCK_DGRAM, 0));   /* The stale probe's real setup. */
    CHECK(probe >= 0);
    check_flags(probe);
    check_exec(argv[0], connection->fd, probe);
    close(probe);

    struct sigaction action = {0}, previous;
    action.sa_handler = pipe_signal;
    CHECK(!sigemptyset(&action.sa_mask) && !sigaction(SIGPIPE, &action, &previous));
    sigset_t pipe_set, old_mask;
    CHECK(!sigemptyset(&pipe_set) && !sigaddset(&pipe_set, SIGPIPE));
    CHECK(!pthread_sigmask(SIG_UNBLOCK, &pipe_set, &old_mask));
    CHECK(!shutdown(client, SHUT_WR));
    transport_wait(1000);
    CHECK(connection->eof && !connection->closing && !hos_darwin_control_closed(connection->fd));
    transport_wait(0);
    CHECK(connection->eof && !connection->closing);
    close(client);
    CHECK(hos_darwin_control_closed(connection->fd));
    transport_wait(0);   /* No POLLIN requested after EOF: full closure must still be noticed. */
    CHECK(connection->closing && pipe_signals == 0);
    puts("PASS: macOS half-close keeps replies possible; full closure is noticed without POLLIN");
    buf_puts(&connection->out, "closed peer\n");
    errno = 0;
    conn_write(connection);   /* The real MSG_NOSIGNAL send, not a test replacement or a global SIGPIPE ignore. */
    CHECK(errno == EPIPE && connection->closing && pipe_signals == 0);
    CHECK(!pthread_sigmask(SIG_SETMASK, &old_mask, NULL) && !sigaction(SIGPIPE, &previous, NULL));
    puts("PASS: macOS closed peer returns EPIPE without SIGPIPE and the transport closes the connection");
    conn_close(0);
    transport_cleanup();
    CHECK(listen_fd == -1 && lstat(address.sun_path, &st) == -1 && errno == ENOENT);
    return 0;
}
#endif
