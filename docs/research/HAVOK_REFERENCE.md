# Havok resource and reference diagnosis

## Native failure and controls

Normal PLAY press at 225s was consumed; at ~323s original HavokResource line 177 reported “Could not find physics or animation data in root level object”, then sys_process_exit(0). SELECTABIKE showed MOHAWK/INDIANA with malformed art/no model before black submitted frames. The exact failing resource has **not** been captured; no proof that SPU, PVS, schema or a default bike caused this fatal.

Two IStream constructors 72E624/72EE9C both contain the gate; HavokType::Load 72F714 calls sibling 72EE9C. Initial observation covered only one; later broad 128-event quota exhausted on successful menu animations. Final raw/fatal observer reserves same-resource raw pairing; the instrumented tree retains five hooks. Root is r24 after reader, r22 is the 32-byte lookup result; allocation this+24 may differ from aligned raw destination; length this+20. Validate original field accesses, not stale analyzer labels.

Captured controls: 26 raw/root pairs, 8190 local/global fixups checked with 0 mismatch, 25 successful animation lookups. These do not exonerate failing physics/virtual fixes/scalars/finishing/source bytes. Packfiles use Havok 4.1.0-r1, version 4, pointer 4 big-endian layout [4,0,1,1], not Pacific Rift 5.5 assumptions.

| Original resource control | ID | Packfile offset/bytes | Root |
|---|---|---|---|
| animations/havok_000a0ebe40f7a99b.evo | 000a0ebe40f7a99b | 24/26240 | hkAnimationContainer |
| global/havok_000a7d721a8a8dc5.evo | 000a7d721a8a8dc5 | 24/22304 | hkPhysicsData |
| vehicles/bike01.rpk embedded record | 000ab2baec08ebb3 | 3459566/33872 | hkPhysicsData |

Animation raw SHA256 d89f5becc46c6ec634e647a87743cb94f286b1f3cae9a744bdb1bd9b8525f389 matches archive extraction. Global 9cacc297d64b075972bf4aecef0ac9faa225eb8eca6e8d007f6db8228b98bde1; bike 3cee8002cb2d6cdff28e34dbd1618299db9bb7459b0bc424e0b287432110b676. Bike01 is not identified as the failing/selected resource.

## PVS is separate

Matched EXE/PDB unwind 29 frames established PVSPPUThread::Wait 87D278 → JobListPrivate::StallForJobMarker C5C424, requested marker 8/min 6 in that capture. Other runs/PCs differ. This is a genuine wait with unresolved producer, not demonstrated cause of Havok 177. Do not bypass it. Earlier raw stack candidates were incorrectly promoted to running PCs; see operations for symbol/PC rules.

## Reference compatibility

Ordinary retail RPCS3 BCES00006 and BCUS98137 both work; a full lap was driven in both as reference. Old isolated debug used 20237-55a3aff3 while ordinary was 20273-987129bc with other effective config differences; those old crashes were not matched differentials. Corrected v2 uses 20273-987129bc, identical full configs including PSID and shared authentic disc/HDD baseline. Earlier PyYAML rewrote 16:9 to 969 and PSID to decimal; correction was byte-preserving volume-only edit with reverse reconstruction. Original shader warnings retained.

Authentic installed pack01 decrypted TOC contains extensionless modes/asset_time_attack plus CRLF. Original debug scanner calls strrchr('.') at 642468, receives NULL, unchecked underscore store 642478 faults (logged JIT CIA 64246c). Capture was frozen **post-fault**, not a successful pre-store breakpoint. This proves a debug/DLC incompatibility, not native Havok causality.

Private prelaunch pack01 quarantine preserved original data. Scanner checked pack00/04/03/02/01, all ENOENT, and passed. User normal PLAY→bike→race-start then debug null READ D46EC/main_thread at guesttime 1:21.597 (VehicleViewer class). Retail under identical quarantine completed race. Debug repair is bounded; use working retail with explicit address/callee mapping.

## Mapping evidence and next boundary

Debug constructors 72E624/72EE9C map to retail 6AFAA4/6B031C: each 542 instructions, 493 exact, 27 call and 22 TOC displacement differences only. Lookup A11980/98E614 exact 384 bytes; getName AF7AC0/A74754 exact 8 bytes. Eleven reader comparisons: 8 exact; remaining 3 only 3 TOC loads/1 call differences. Referenced callee/data equivalence still needs proof. Previously fixed generic TOC is not reopened by these results.

Capture exact failing asset, independently recover original bytes, compare raw first, then normalized relocation/virtual finishing, then lookup/ABI only if inputs match. No schema rewrite or forced non-null return is justified. Exact public fatal search found no applicable fix. [Frame-rate patch research](https://illusion0001.com/legacy-website/patches/2022/05/10/Motorstorm-Framerate-Patch/) is about timestep/frame modes, not this root lookup.

Private artifacts/atomic source ownership are indexed in [source coverage](../SOURCE_COVERAGE.md); logs known overwritten there cannot be reused as original evidence.
