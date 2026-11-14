// The three real menu functions, with their child targets replaced by this authored executable.
// Nothing launches /usr/bin/open, pbcopy, a GUI, browser, or account action.
#include "darwin_process.h"
#include <algorithm>
#include <cerrno>
#include <csignal>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <fstream>
#include <iterator>
#include <libproc.h>
#include <string>
#include <sys/wait.h>
#include <unistd.h>
#include <vector>
extern char** environ;
#define CHECK(x) do { if (!(x)) { fprintf(stderr, "FAIL share line=%d: %s errno=%d\n", __LINE__, #x, errno); exit(1); } } while (0)
static std::string self, receipt;
static pid_t last_pid;
static unsigned copies, opens, reveals;
static int substitute_spawn(pid_t* pid, const char* path, char* const argv[], char* const env[], const int standard[3], int replace) {
    std::vector<std::string> args;
    if (!strcmp(path, "/usr/bin/pbcopy")) {
        CHECK(argv[0] && !argv[1] && !replace);
        copies++;
        args = {self, "--copy", receipt};
    } else if (!strcmp(path, "/usr/bin/open")) {
        CHECK(argv[1] && !replace);
        const bool select = !strcmp(argv[1], "-R");
        const int at = select ? 2 : 1;
        CHECK(argv[at] && !strcmp(argv[at], "--") && argv[at + 1] && !argv[at + 2]);
        select ? reveals++ : opens++;
        args = {self, select ? "--reveal" : "--open", receipt, argv[at + 1]};
    } else {
        CHECK(!strcmp(path, self.c_str()) || !strcmp(path, "/no/such/DarwinHost-share"));
        const int e = darwin_spawn(pid, path, argv, env, standard, replace);
        if (!e) last_pid = *pid;
        return e;
    }
    std::vector<char*> native;
    for (auto& arg : args) native.push_back(arg.data());
    native.push_back(nullptr);
    const int e = darwin_spawn(pid, native[0], native.data(), env, standard, replace);
    if (!e) last_pid = *pid;
    return e;
}
#define darwin_spawn substitute_spawn
#include "darwin_share_functions.inc"
#undef darwin_spawn
static void reaped(void) {
    for (int i = 0; i < 2000 && kill(last_pid, 0) == 0; i++) usleep(1000);
    int status;
    errno = 0;
    CHECK(waitpid(last_pid, &status, WNOHANG) == -1 && errno == ECHILD);
}
static std::string read_receipt(void) {
    std::ifstream f(receipt, std::ios::binary);
    CHECK(f.good());
    return std::string(std::istreambuf_iterator<char>(f), {});
}
static void no_extra_fds(void) {
    proc_fdinfo descriptors[256];
    const int bytes = proc_pidinfo(getpid(), PROC_PIDLISTFDS, 0, descriptors, sizeof descriptors);
    CHECK(bytes > 0 && bytes < (int)sizeof descriptors);
    for (int i = 0; i < bytes / (int)sizeof descriptors[0]; i++) CHECK(descriptors[i].proc_fd <= 2);
}
int main(int argc, char** argv) {
    if (argc > 1 && !strcmp(argv[1], "--fail")) return 7;
    if (argc > 2 && (!strcmp(argv[1], "--copy") || !strcmp(argv[1], "--open") || !strcmp(argv[1], "--reveal"))) {
        no_extra_fds();
        std::string value;
        if (!strcmp(argv[1], "--copy")) {
            char buffer[4096];
            for (;;) {
                const ssize_t n = read(0, buffer, sizeof buffer);
                if (n < 0 && errno == EINTR) continue;
                if (n <= 0) break;
                value.append(buffer, (size_t)n);
            }
        } else { CHECK(argc == 4); value = std::string(argv[1]) + "|" + argv[3]; }
        const int out = open(argv[2], O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
        CHECK(out >= 0 && write(out, value.data(), value.size()) == (ssize_t)value.size() && close(out) == 0);
        return 0;
    }
    CHECK(argc == 2);
    char executable[4096];
    CHECK(!darwin_executable_path(executable, sizeof executable));
    self = executable;
    receipt = argv[1];
    const std::string text("authored UTF-8 Grüße\0with NUL", sizeof("authored UTF-8 Grüße\0with NUL") - 1);
    CHECK(share_clipboard(text));
    reaped();
    CHECK(read_receipt() == text);
    CHECK(share_open("-not-an-option local file"));
    reaped();
    CHECK(read_receipt() == "--open|-not-an-option local file");
    CHECK(share_open("local file to select", true));
    reaped();
    CHECK(read_receipt() == "--reveal|local file to select");
    const std::string large(1u << 20, 'x');
    CHECK(!share_spawn({self, "--fail"}, &large));
    reaped();
    CHECK(!share_spawn({"/no/such/DarwinHost-share"}));
    CHECK(!share_spawn({}));
    CHECK(!share_spawn({std::string("bad\0argument", 12)}));
    CHECK(copies == 1 && opens == 1 && reveals == 1);
    CHECK(unlink(receipt.c_str()) == 0);
    puts("darwin_share PASS copied=1 opened=1 selected=1 binary_input=1 child_failure=reported all_children=reaped GUI_browser_clipboard_actions=0");
    return 0;
}
