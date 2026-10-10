# Current handoff — 2026-10-10

The active workspace is this repository. The former Pacific Rift workspace retains its old Git history and parked Bevy work; never use that history to initialize this repository.

Current native target: **named US debug** `EBOOT_MSMVdbgUS.ELF`, paired with authentic launch-US BCUS98137 disc data (VERSION01.00/FW1.80). ELF SHA256 `3e68c72f8fbd6a30ef30d4535462cf70f1cc86d111f56574adb7dd2c255e0706`. Ignored `config/local.json` retains the user's F: originals. BCES00006 Complete APP_VER03.10 is a different revision. Keep generated target trees separate and apply shared fixes to each.

Latest accepted native outcome: normal PLAY reached authentic HavokResource fatal177 and `sys_process_exit(0)`. The exact failing asset remains uncaptured. Art/model were malformed or missing, frames became black, and native race/video/audio/save round-trip/minimum60FPS acceptance remains unmet. [Havok/reference evidence](docs/research/HAVOK_REFERENCE.md) owns the details and limits.

Retail mstream starvation is **PARKED** until directed; top-right flashing **CLOSED**, reopen only on reproduced regression; Bevy replacement **PARKED** until native works. [Parked lane](docs/research/PARKED_MSTREAM.md) retains exclusions and its resume differential.

Immediate native work: identify the exact failing resource, compare archive → raw/decompression → relocation/finishing → lookup. Working retail RPCS3 is the reference; debug-reference repair must not delay this. [Next steps](docs/NEXT_STEPS.md) owns the ordered gates.

The migrated private tree has ten PPU sources, 24 SPU images and **five capture hooks**: it is instrumented. Preserved original EXE/PDB live in `build/preserved/`; new build identities are in `build/native/build_identity.json`. Migration/build success is not a gameplay fix.

Work solo, muted, bounded and atomic. No fake assets/completion, game-code patches, title-specific runtime bypasses, suppressed errors or bypassed waits. Follow [operations](docs/OPERATIONS.md). Unchanged original notes/evidence remain in ignored storage; [source coverage](docs/SOURCE_COVERAGE.md) connects them to canonical topics.
