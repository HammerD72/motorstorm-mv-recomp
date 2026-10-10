# WWS policy, ownership and CPU evidence

WWS job-list header: manager+0, list-size+4, BE16 hint+10, taken+12, six BE16 minActive markers+14. PM atomic publication sites3180/32FC/3330. PPU wait is genuine. Native main SPURS4003F380 with five physical SPUs/wids0..3; audio1027DC00. Heap EAs vary;108D0100 is reservation payload, not a module (observed module100E6F80).

## Allocations and retention

Initial reserved pages0..15 and248..255=24. Reserve C00 at108D0100 requests16..156=141; C10 at9D580100 requests157..244=88. Union253, only245..247 free. PM allocation commit1B04/1B0C writes page maps,1B14 descriptor,1B1C command; new-job reset2FA4..2FF8 clears selected descriptor/gather/logical maps **without clearing page maps**. Growth1FF0/1FF4/classification1FF8/1FFC; baseline removal2CFC/2D00; release20CC/20D8/20DC; retire3130/314C/3150.

Later interrupt chronology demonstrates real24→165→253 allocations. A zero descriptor plus retained map is not automatically a host leak: incomplete history cannot justify page clearing or a root-cause claim.

## Bounded independent replay

| Unit | Inputs/checks | Scope |
|---|---|---|
| Ownership commit/reset/release |77/39616,0fail:17commit12reset48release|Canonical-byte scalar oracle, full128GPR/fullLS, actual span bytechecked |
| Reservation predicate2C34 continuation→2750→1AB0 |35/110,0fail; LS unchanged|Actual start157,length88 intersects retained maps: r18FFFFFFFF, original refuses→2178 clearsr10→1A1C |
| Page-mask helper2750 |111/73953,0fail,26instructions|Actual BI r6=1A60, endpoints/nonuniform; corrected modulo32 domain expectations |
| CPU span2C34..2C98 beforeBI2C9C |17×26instructions/8738,0fail|FullGPR/LS; watchdog10s and instruction budget64 |

Tests use literal original instruction fixtures and stay private. They exonerate tested spans/inputs, not helpers beyond the span, concurrency, lifetime or the full freeze. Initial bad test constants/types/math assertions were oracle/setup errors, not architectural failures. SELECT service keeps LS1DC id and resumes r0 link; a “wi->0” diagnostic did not mean interceptor wrote wi_state0.

Owner-read gate captured four owning-thread beforePC1A40 snapshots across45.281s with identical complete LS SHA256 f0a3b6372f699744582ddd3d2de49c651399d967ec163408fad180af01277e77, matching authentic PM bytes. PPU stalled~166.5s; queuehint2/taken2/minSPU4=1, other markersFFFF. Snapshots are not instruction chronology. Establish original provenance/live history rather than reinvestigating exonerated CPU spans.

A/B scheduling cap2/burst16 observed18–22presents/s vs cap4/burst1 26–27; defaults4/1 retained. This is a diagnostic window, not a60FPS benchmark, full-game improvement or license for title-specific scheduler policy.
