"""macOS cache reservations: real allocated blocks and a child-only file-size limit.

No Vulkan loader, GPU or game. --source-root selects another tree's storage header.
Windows and Linux retain their existing cache fixtures; this one runs only on Darwin.
"""
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

RUN_ALL_ARGS = ["--cxx", "{cxx}"]

ROOT = Path(__file__).resolve().parents[1]
COMPILER = "clang++"
SOURCE_ROOT = ROOT

FIXTURE = r'''
#if defined(__APPLE__)
#include "pipeline_cache_file.h"
#include <csignal>
#include <sys/mount.h>
#include <sys/resource.h>

#define CHECK(x) do { if (!(x)) { std::fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); return 1; } } while (0)

int main(int argc, char** argv) {
    CHECK(argc == 3);
    const std::string mode = argv[1];
    const std::filesystem::path area = argv[2];
    const std::string path = (area / "cache.bin").string();
    constexpr size_t capacity = (8u << 20) + 37; // Neither a host page nor a disk block multiple.
    uint8_t seed[64];
    std::memset(seed, 42, sizeof seed);
    FILE* file = std::fopen(path.c_str(), "wb");
    CHECK(file != nullptr);
    CHECK(std::fwrite(seed, 1, sizeof seed, file) == sizeof seed);
    CHECK(std::fclose(file) == 0);
    const auto valid = [](const uint8_t* head, uint64_t n) { return n == 64 && head[0] == 42; };

    if (mode == "allocate") {
        const auto sparse_path = area / "sparse.bin";
        const int sparse_fd = open(sparse_path.c_str(), O_RDWR | O_CREAT | O_EXCL, 0600);
        CHECK(sparse_fd >= 0);
        struct stat empty{}, sparse{}, reserved{}, published{};
        struct statfs filesystem{};
        CHECK(fstat(sparse_fd, &empty) == 0);
        CHECK(fstatfs(sparse_fd, &filesystem) == 0);
        CHECK(ftruncate(sparse_fd, (off_t)capacity) == 0);
        CHECK(fstat(sparse_fd, &sparse) == 0 && sparse.st_size == (off_t)capacity);
        CHECK(close(sparse_fd) == 0);

        auto output = mkwvk::PipelineCacheFile::writable(path, capacity);
        CHECK(!output.empty() && output.size() == capacity);
        std::filesystem::path temporary;
        for (const auto& entry : std::filesystem::directory_iterator(area)) {
            if (entry.path().filename().string().find("cache.bin.tmp-") == 0) {
                CHECK(temporary.empty());
                temporary = entry.path();
            }
        }
        CHECK(!temporary.empty() && stat(temporary.c_str(), &reserved) == 0);
        CHECK(reserved.st_size == (off_t)capacity);
        // stat(2): st_blocks is in 512-byte units, not st_blksize or host pages.
        // This is before the first read or write through the mapped pointer.
        CHECK(reserved.st_blocks > empty.st_blocks);
        CHECK(reserved.st_blocks >= (blkcnt_t)((capacity + 511) / 512));
        {
            auto old = mkwvk::PipelineCacheFile::read(path, valid);
            CHECK(!old.empty() && old.data()[63] == 42);
        }
        std::memset(output.data(), 90, capacity);
        CHECK(output.publish(capacity));
        CHECK(stat(path.c_str(), &published) == 0 && published.st_size == (off_t)capacity);
        auto read = mkwvk::PipelineCacheFile::read(path, [](const uint8_t* head, uint64_t n) {
            return n == capacity && head[0] == 90;
        });
        CHECK(!read.empty() && read.data()[capacity - 1] == 90);
        CHECK(read.reset());
        std::printf("{\"filesystem\":\"%s\",\"capacity\":%zu,\"emptyBlocks\":%lld,\"sparseBlocks\":%lld,"
                    "\"reservedBlocksBeforeWrite\":%lld,\"publishedBlocks\":%lld,\"passed\":true}\n",
                    filesystem.f_fstypename, capacity, (long long)empty.st_blocks, (long long)sparse.st_blocks,
                    (long long)reserved.st_blocks, (long long)published.st_blocks);
    } else {
        CHECK(mode == "limit");
        struct sigaction ignored{}, previous{};
        ignored.sa_handler = SIG_IGN;
        CHECK(sigemptyset(&ignored.sa_mask) == 0);
        CHECK(sigaction(SIGXFSZ, &ignored, &previous) == 0);
        struct rlimit original{}, limited{};
        CHECK(getrlimit(RLIMIT_FSIZE, &original) == 0);
        CHECK(original.rlim_max >= 4096);
        limited = original;
        limited.rlim_cur = 4096;
        CHECK(setrlimit(RLIMIT_FSIZE, &limited) == 0);
        auto refused = mkwvk::PipelineCacheFile::writable(path, capacity);
        CHECK(refused.empty() && refused.data() == nullptr && refused.size() == 0 && !refused.rejected());
        CHECK(setrlimit(RLIMIT_FSIZE, &original) == 0);
        CHECK(sigaction(SIGXFSZ, &previous, nullptr) == 0);
        {
            auto old = mkwvk::PipelineCacheFile::read(path, valid);
            CHECK(!old.empty() && old.data()[63] == 42);
        }
        auto retry = mkwvk::PipelineCacheFile::writable(path, capacity);
        CHECK(!retry.empty() && retry.size() == capacity && retry.reset());
        std::puts("{\"limitBytes\":4096,\"refusedEmpty\":true,\"oldOutputPreserved\":true,\"retryPassed\":true}");
    }
    for (const auto& entry : std::filesystem::directory_iterator(area))
        CHECK(entry.path().filename().string().find(".tmp-") == std::string::npos);
    std::puts("owned temporaries cleaned");
    return 0;
}
#endif
'''


@unittest.skipUnless(sys.platform == "darwin", "macOS preallocation and file-size-limit contract")
class PipelineCacheAllocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory(prefix="longshot-cache-allocation-")
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.area = Path(cls.temporary.name).resolve()
        source = cls.area / "allocation.cpp"
        source.write_text(FIXTURE, encoding="utf-8")
        cls.executable = cls.area / "allocation"
        subprocess.run([COMPILER, "-std=c++20", "-O2", "-Wall", "-Wextra", "-Werror",
                        "-I" + str(SOURCE_ROOT / "gpu/vk"), str(source), "-o", str(cls.executable)],
                       check=True, timeout=60)

    def run_case(self, mode):
        directory = self.area / mode
        directory.mkdir()
        # Each invocation is a fresh child: RLIMIT_FSIZE and SIGXFSZ never change this runner.
        result = subprocess.run([str(self.executable), mode, str(directory)], capture_output=True,
                                text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        print(result.stdout, end="")

    def test_capacity_has_backing_blocks_before_write(self):
        self.run_case("allocate")

    def test_file_size_limit_fails_without_publication(self):
        self.run_case("limit")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cxx", default=COMPILER)
    parser.add_argument("--source-root", type=Path, default=ROOT)
    args, remaining = parser.parse_known_args()
    COMPILER, SOURCE_ROOT = args.cxx, args.source_root.resolve()
    unittest.main(argv=[__file__, *remaining])
