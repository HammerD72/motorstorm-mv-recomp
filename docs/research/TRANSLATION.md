# Translation provenance and methods

The named corpus has50839 function names and870 structs, not a retail03.10 address map. NamedUS debug compilation includes41CUs GCC4.0.2 CELL4.1.15/rev1141 plus older linked CELL comments. Sibling Pacific Rift/AP viewers GCC4.1.1/SDK mixtures do not establish retail compiler provenance. Symbols/DWARF/ABI guide matching but cannot uniquely recover original source. Reuse existing semantics/tools before inventing infrastructure.

PPU ELF/reloc/function-scoped RPCS3 hashes and LLVM host objects do not recover source/assets. SPU cache identity depends on complete LS content and entry, not a name/address. Independently written reader validates v1BE8-byte header (CRC16/wordcount16/entry32), truncation and declared legacy no-CRC cases; payload is original opcodes with zero holes/overlays, kept private. Never merge variants into synthetic LS.

Exact debug export2770 verified blocks/143 multi-code addresses. Final declared-base comparison2663/2770 to13images,7ambiguous,107unmatched/17900bytes. Initial base0 wrapper610 was misleading and superseded. Retail2096 legacy blocks noCRC:514 matches to debug images/11ambiguous; not full compatibility or permission to mirror trees. Prefer smaller unmatched seams and full content verification.

Historical translation backlog (F-tr01..16) is research, not landed-completion proof:
- Named bounds + dataflow jump-table recovery; data-in-text guards to prevent silent no-ops; correct lswi/stswi where demonstrated.
- Position-independent SPU lifting requires correct PC-relative data/branches, absolute LS references, fixed host externs and resume contract; rebasing function addresses alone is insufficient.
- Function-level identification/DWARF ABI checks for missing DSP variants; staged image inventories should include real code extent/unsupported count/registration identity.
- Flat mapping/LTO/fence consolidation are profile-driven candidates, not measured wins.
- Shared upstream fixes are selective with attribution; no wholesale replacement of dirty runtime or GPL RPCS3 implementation copying.

Research method: identify target/version/data, capture exact input/operation, build independent bounded oracle, distinguish semantic bug from test setup, find first differing seam, prove red/green and real runtime effect separately. No original-game patches/forced completion. Source-only changes, coverage counts and FPS labels do not satisfy native acceptance.

Legacy RECOMP_PLAN/triage/milestone estimates and knowledge findings remain private provenance. Current [next steps](../NEXT_STEPS.md) supersedes their priority lists; [vendor](../VENDOR.md) owns upstream status.
