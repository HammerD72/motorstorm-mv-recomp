# Checked shader disk cache

Race-start compilation can hold the renderer long enough for a guest GPU wait
to expire. This cache reuses successful FXC output without changing source,
compile flags, draw order or pipeline-state identity.

RSX_SHADER_DISK_CACHE=0 disables disk reuse. With no override the cache uses
LocalAppData/ps3recomp/shader-cache-v2; an explicit local directory selects another
cache. This preserves the validated R48 default. Failure to initialize or write
is a miss, and the first attempted store failure reports its path, stage and
Win32 error once. Explicit disablement stays quiet.

Version 2 keys include the exact source, source name, entry, target, both flags
and SHA256 of the DLL containing the loaded D3DCompile export. The executable's
import thunk is rejected. Every read checks the complete key, file lengths,
DXBC bounds and payload SHA256. Missing, damaged, stale or incompatible entries
fall back to original compilation. Stores use unique temporary files and a
same-directory replacement; reader/removal/replacement operations share a lock.
Directory recreation preserves the reparse-point refusal. There is currently
no total directory quota; per-entry limits are 1 MiB source and 16 MiB bytecode.

The renderer coordinator owns disk access, bytecode caches and PSO creation.
A PSO rejection involving disk-origin bytecode invalidates those entries,
compiles the affected original sources and retries once. Device loss and
out-of-memory do not trigger that retry. A disk-loaded VS retains its original
HLSL within a 32 MiB budget; when that source cannot be retained, the disk blob
is refused and compilation proceeds normally.

Run the authored Windows checks with Python and a configured Windows compiler:
python tests/check_shader_pipeline.py --cc clang-cl in the runtime, or
python scripts/check_shader_pipeline.py --cc clang-cl in the curated game.
They cover exact keys, compiler changes, corrupt entries, concurrent replacement,
a 65 MiB authored executable and a distinct executable sharing one compiler,
once-only failure logs, quiet disablement, actual coordinator provenance and
PSO recovery, and deliberately broken import-thunk/integrity/recovery controls.
The complete backend translation unit is checked. No game inputs are required.
The runtime exposes this through its Windows CTest suite as well.

These tests establish storage/recovery behavior, not a native game deadline or
minimum FPS. The prior R48 owner runs motivate this port; the new public source
candidate still requires owner acceptance. Readable-span caching and unrelated
renderer fixes are outside this change.
