// The game folder's path for the files the game keeps there (caches, the shader list, settings): host/hos_paths.h's rule, for
// this DLL. The game is bin\longshot.exe beside its launcher since the launcher came; its files stay in the folder above bin.
// game_module_path answers as GetModuleFileNameA(nullptr, ...) does, with the folder's path when the exe is in a "bin" folder
// beside image.bin.
#pragma once
#ifndef NOMINMAX
#define NOMINMAX
#endif
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#undef min
#undef max
#include <cstring>
#include <filesystem>
#include <string>

namespace mkwvk {
inline DWORD game_module_path(char* buf, DWORD size) {
    DWORD n = GetModuleFileNameA(nullptr, buf, size);
    if (!n || n >= size) return n;
    char* name = buf + n;
    while (name > buf && name[-1] != '\\' && name[-1] != '/') name--;
    if (name - buf < 5) return n;
    char* dir = name - 1;
    char* bin = dir;
    while (bin > buf && bin[-1] != '\\' && bin[-1] != '/') bin--;
    if (dir - bin != 3 || _strnicmp(bin, "bin", 3) != 0) return n;
    char probe[MAX_PATH + 16];
    const size_t root = (size_t)(bin - buf);
    if (root + 10 >= sizeof probe) return n;
    std::memcpy(probe, buf, root);
    std::strcpy(probe + root, "image.bin");
    if (GetFileAttributesA(probe) == INVALID_FILE_ATTRIBUTES) return n;
    std::memmove(buf + root, name, std::strlen(name) + 1);
    return (DWORD)std::strlen(buf);
}
/// The game folder as a path, game_module_path's rule. On Windows from GetModuleFileNameW: a folder with characters outside
/// the ANSI code page, or longer than MAX_PATH, is still the right one (open it with std::filesystem or the streams)
inline std::filesystem::path game_dir_path() {
#ifdef _WIN32
    std::wstring exe(32768, L'\0');
    const DWORD n = GetModuleFileNameW(nullptr, exe.data(), (DWORD)exe.size());
    if (!n || n >= exe.size()) return {};
    exe.resize(n);
    std::filesystem::path dir = std::filesystem::path(exe).parent_path();
    if (!_wcsicmp(dir.filename().c_str(), L"bin") &&
        GetFileAttributesW((dir.parent_path() / L"image.bin").c_str()) != INVALID_FILE_ATTRIBUTES)
        dir = dir.parent_path();
    return dir;
#else
    char exe[MAX_PATH];
    const DWORD n = game_module_path(exe, MAX_PATH);
    return std::filesystem::path(std::string(exe, n)).parent_path();
#endif
}
}  // namespace mkwvk
