# OpenXR

The Khronos OpenXR headers, used by `gpu/vk/vk_xr.cpp` (the game in a PC VR headset). Unmodified upstream copy.

- Upstream: https://github.com/KhronosGroup/OpenXR-SDK-Source
- Version: release-1.1.65 (`include/openxr/` of `openxr_loader_windows-1.1.65.zip`, the release's own package)
- License: Apache-2.0 (the headers are dual-licensed Apache-2.0 OR MIT), in `LICENSE`. Keep it with any distribution.

What is here and why:

| | |
|---|---|
| `include/openxr/` | The API's headers. `vk_xr.cpp` is built with `XR_NO_PROTOTYPES` and asks the loader for every function by name, so nothing of OpenXR is linked |
| `LICENSE` | The Apache-2.0 text |

The loader, `openxr_loader.dll`, is not here and is never committed: it is `tools.lock`'s `openxr-loader`, the same
release's package as Khronos publishes it (`x64/bin/openxr_loader.dll`), which the Builder downloads and checks and
`nx2/build.py` copies beside `longshot_vk.dll`. Without it VR stays off and the game plays on the desktop as before
(`quest/README.md`).

Nothing in this folder is edited. A change belongs in a new folder with its own note, per `CONTRIBUTING.md`.

tree-sha256: 261bab7efcfc0e971fc80771576e8523e47b7b650ddba78de5b86afafcd4a59c
