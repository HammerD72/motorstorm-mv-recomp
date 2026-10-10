# Retail mstream ATRAC hub — PARKED

This is the historical BCES00006 APP_VER03.10 lane, not the current named-US Havok task. Resume only when directed. Addresses below are deterministic for the matched original retail binary; never transplant them into a different revision.

Taskset running/enabled exit semantics, pending-ready signal, TI_ARGS priority and retained LS made the ATRAC hub redispatch/poll for a whole boot. Symptom remained race-load stall/no background video/~11FPS picker; this was not a cure.

flashATRAC staged image2 LS33900/entry90 polls512B command49527C80 (ready bit0): all zeros. Multistream PPU Update tid3/entryEFE608 is800us syscall141 usleep, not event receive. Named-registry passes clear block familyCB8A18, repost head to102CD280, poke7A120D at102CD088, PUTLLC flag102CD000.158K clears vs one init poison-fillCD8768. CD0378 recycle waits while item stamp[r30+10] equals consumer cursor[[r31]+4]; cursor never diverges.

Five PM image200 contexts stage jobmod215 at4000 without launch; zero observed PCs4000..16540. Logical/bind scan11B0/11B8 stops on empty9C580100. Zero mapped payloads:49527C80,4C754600+50,9C580100,4950A400,49322B00/4929B900.108C0100 next-node unmapped beyond original image end10310CD0. Corridor102CD000..102CD0FF saw no SPU PUT/module PUTLLC.

## Exonerated in this lane

Do not reinvestigate without contradictory matched evidence: MFC list engine (EA0 is real base transfer, tag completes), PM claim/stall/ACK cycle, SPU→PPU coherence/shared memory, taskset redispatch, active archive reads, avStream tids4/5/6 polling with no work. TSTATE411A/1388 were usleep arguments, not PCs/semaphore waits. Later fixes to shared edge cases do not retroactively convict these specific observations.

## Ranked resume

1. Working RPCS3 same game/revision to menu/race-load; dump49527C80,9C580100,4C754600,descriptor102CC580,queue102CD280. Identify first buffer filled and its writer; that names the broken seam.
2. Only if necessary, decode full avStream dequeue trigger entries including StreamCodecMP3 EFF248/Update EFE608 family.
3. Separately verify app-heap mapping if real allocated data above image end is silently zero-filled by VEH; empty pages alone do not prove it.

Historical launch_hub.cmd/logs/hub_pad.txt and memread9/10/find_elf are in temp/legacy/recomp/out/. Old knobs: DMA range102CC000..102D0000, LS watch338A0/13100, PPU watch102CD080 length80, SPU_SN_DEFER64, SPU_CTX_LOG1, FSLOG1; optional SCTRACE_TID3. They are archaeological settings, not current defaults. Current [operations](../OPERATIONS.md) owns safe input/guard/capture workflow.
