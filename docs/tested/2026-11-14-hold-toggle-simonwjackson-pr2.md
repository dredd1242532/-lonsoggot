| Date | Build | OS / desktop | GPU / driver | CPU / RAM | Settings | Where | Result |
|---|---|---|---|---|---|---|---|
| 2026-11-14 | pr abecfa9, game-free run 2 | Linux (Nix toolchain), zao | No GPU | Native C/C++ fixtures | `test_pad_layout.py`, `test_menu_keys.py --cxx g++`, `test_menu_keys.py --cxx clang++` | Two interleaved main/PR pairs; no game launch or FPS claim | Run 2: pad-layout and menu-keys with g++ and clang++ all pass |
