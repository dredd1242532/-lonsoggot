# Pipeline cache file storage

Vulkan pipeline cache persistence uses file-backed contiguous storage rather than a
temporary byte vector the size of the cache. This applies to the initial base
cache, play-generation files, startup compaction and subsequent base-cache saves.
The existing Vulkan cache format, generation retention, growth threshold and
`RX_VK_PCACHE_LOAD` / `RX_VK_PLAY_CACHE` switches are unchanged.

The small device/driver header is checked before acquiring the complete payload.
Windows holds a read-only mapping with write sharing denied. Linux maps the
opened source directly with read-only `MAP_PRIVATE`: reads create no temporary
file and require no extra cache-sized disk write or free space. Both retain the
opened file/inode across the renderer's atomic replacements. Regular symlink
targets and Windows cloud files follow normal filesystem resolution; directories
and non-regular inputs are refused. External in-place truncation while a Linux
view is in use is unsupported; a private mapping does not make that safe.

Saving creates a unique owned sibling temporary, reserves the queried capacity
(`posix_fallocate` on Linux, before any mapped driver write)
and lets Vulkan write directly into its mapped bytes. Only `VK_SUCCESS` with a
valid actual length permits flush, unmap, truncation, close and atomic replacement.
`VK_INCOMPLETE`, failed I/O or denied replacement leaves the old published cache
and saved-size accounting intact, so a later save can retry. There is no in-place
overwrite fallback. On Windows a mapped destination can prevent replacement;
the old cache is retained until the reader releases it and a later save succeeds.

On macOS, the empty sibling temporary reserves its whole capacity with
`fcntl(F_PREALLOCATE)`, `F_ALLOCATEALL` and `F_PEOFPOSMODE`, then `ftruncate`
sets the logical length before the writable mapping is exposed. Physical
contiguity is not required. Either syscall failing returns an empty acquisition;
the temporary is removed and the published cache is left alone, as on Linux.
A filesystem that does not support preallocation is a save failure, not a
sparse-file fallback. Read views, publication and cleanup use the same POSIX
path, including the restriction on external in-place truncation.

The program cache's two small per-record read hints use `fcntl(F_RDADVISE)` on
macOS for the same head and tail ranges as Linux's `POSIX_FADV_WILLNEED`.
These are best effort: ordinary reads still perform and validate the I/O even
when the hint is refused. `F_RDAHEAD` is not used because it changes read-ahead
policy rather than requesting those particular ranges. These calls are described
in the macOS `fcntl(2)` and `truncate(2)` manual pages. This is cache-file support,
not a complete macOS renderer or game port.

At cache initialization, recognized base/play temporary names older than 24 hours
are collected. Published caches, other names, links and recent temporaries are
left alone. A POSIX writer holds an exclusive advisory lock;Windows write handles
deny sharing. An active writer is retained even if its temporary has an old
timestamp. Publication refreshes the timestamp before closing the file, so a
writer paused longer than 24 hours is also fresh during its close/rename gap.
Linux replacements preserve an existing owned regular cache's permission bits;
new files retain 0600, without overriding a restrictive umask.

This removes the temporary bulk private/anonymous allocation, not the Vulkan
driver's retained cache. File-backed pages still consume physical memory and I/O.
Filesystem-dependent acquisition timings are not a game startup or frame-rate
promise. Published files must be replaced atomically, and source bytes must not
be modified in place while the driver reads them. Mappings do not protect
against media failures: mapped I/O can raise
native in-page faults, which are not ordinary recoverable C++ I/O exceptions.

Game-free checks:

```sh
python -I -B tests/run_all.py pipeline_cache pipeline_play_cache pcache_header
```

`tests/test_pipeline_cache_allocation.py` adds macOS-only checks of the logical
file length and allocated 512-byte blocks before any mapped write, and a child
with a file-size limit that must fail cleanly, retain the old file and clean up.
The native fault fixture also refuses `F_PREALLOCATE` with `ENOSPC`, before any
mapping. Windows and Linux continue to use their existing code and checks.

These compile actual production functions/helpers, exercise native file ownership
and injected syscall refusals, and reject semantic mutations for incorrect saved
length, unchecked headers and leaked abandoned temporaries. Actual driver and
game runs remain separate local validation; no generated cache files belong in
the repository.
