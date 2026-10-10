#!/usr/bin/env python3
"""lift_ms1_debug_dsp.py -- reconstruct + lift the DEBUG-vintage mstream DSP
modules from staged local-store dumps.

The debug EBOOT (EBOOT_MSMVdbgUS.ELF) does NOT embed the six mstream DSP
.pic modules (its only embedded SPU images are spu/msngSPURS and
hkspursconstraint), and the beta disc (BCJS-30004) carries no
audio/110/pic/ directory -- the debug runtime's "audio/110/pic/mstream_dsp_"
path is unsatisfiable from anything on disk. The ONLY debug-vintage bytes in
existence here are the staged images captured in the probe-miss LS dumps:

    temp/captures/spu_staged_miss_2F010.bin
        window LS [0x2EC10, 0x37010)   (branch target 0x2F010 - 0x400)
        -- the only capture that covers the compressor PT_LOAD in full.

Staging model (validated on the capture): the loader places .pic file[0x100:]
at LS base+0x80, so [base, base+0x80) holds stale bytes; the PT_LOAD is
followed by the 12-byte ELF-note header (namesz 8, descsz 0x20, type 1),
the SPUNAME self-naming note and the .shstrtab names, all inside the
observable region. The .pic CRT carries a position probe
(`ila $r2, <module_rel(own+4)>; brsl $r79, .+4; sf $r79,$r2,$r79`) --
the ila immediate calibrates each module base exactly (cross-checked
against the retail compressor: ila 0xA8 for the brsl landing at 0xA8):

    compressor  base 0x2EF80  load [0x2F000,0x2FD00) fsz 0xD00
                (note header verified at 0x2FD00; probe fn at 0x2F430 =
                base+0x4B0 with ila 0x4B8)
    reverb      base 0x2FD00  load [0x2FD80,0x31F00) fsz 0x2180 entry +0xA0
                (probe fn at 0x2FDA0 = base+0xA0 with ila 0xA8)

GCC vintage 4.0.2 (CELL 4.1.11, $Rev: 1035 $) -- distinct from the retail
EBOOT's embedded GCC 4.1.1 Rev 1826 modules (already lifted, ids 3-8 in
the retail tree). The remaining four (distortion/eq/filter/IR) have no
bytes on disk anywhere; they stay interpreter-served until a run stages
them (the probe-miss dumper captures them at their bases).

Observed runtime branches into the staged compressor (img 1):
    0x2F430 = +0x4B0  lr 0x03C50 (runtime call -- primary entry)
    0x2F808 = +0x888  lr 0x043D0 (runtime call)
    0x2F010 = +0x090  lr 0x2F95C (internal brsl from +0x9D8)
    0x2F340 = +0x3C0  lr 0x2F974 (internal brsl from +0x9D0)
A probe only matches a pc when implied base (pc - entry) == lift_base, so
each first-branch offset that img 1 can jump to gets its own companion
probe over a shifted (but module-internal) fingerprint window; after the
first hit the resident-code slot covers the whole span and later internal
branches dispatch natively without probing.

This script:
  1. slices the PT_LOAD + note tail out of the dump for each module,
  2. rebuilds a .pic-shaped SPU ELF (ehdr + 3 phdrs + PT_LOAD@vaddr 0x80 +
     PT_NOTE) under ignored build/spu-inputs/images_dbg/,
  3. lifts each module module-relative (--base 0, ids 14/15) and per
     observed staging base (--base 0x2EF80 / 0x2FD00, ids 20/21) into explicitly
     supplied target-specific trees, seeding every
     observed branch target via --extra-funcs,
  4. prints the register_stage_probe lines (fingerprints are FNV-1a-64
     over the staged PT_LOAD slice -- exactly what the runtime hashes),
  5. removes stale per-base DSP dirs from previous (wrong-base) runs.

Usage:
    python lift_ms1_debug_dsp.py --dump PATH --trees DIR[,DIR]
"""

import argparse
import os
import shutil
import struct
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.abspath(os.path.join(HERE, "..", "..", "vendor", "ps3recomp", "tools"))
LIFTER = os.path.join(TOOLS, "spu_lifter.py")

IMAGES_DBG = os.path.join(HERE, "..", "..", "build", "extracted", "debug_dsp")

DUMP_BASE = 0x2EC10          # LS address of dump byte 0

def fnv1a64(b):
    h = 1469598103934665603
    for x in b:
        h = ((h ^ x) * 1099511628211) & 0xFFFFFFFFFFFFFFFF
    return h

# name, module base (LS), PT_LOAD start (LS), PT_LOAD size, .pic e_entry
# (module off), extra function seeds, probe entry offsets.
# The staged blob is file[0x100:] at base+0x80: PT_LOAD, then the 12-byte
# ELF-note header, then name/desc. The note header words sit at load+fsz,
# so the observable region ends 0xC past the true PT_LOAD end. Sizes below
# are the true fsz values (dump end minus 0xC).
MODULES = [
    dict(name="mstream_dsp_dbg_compressor", base=0x2EF80,
         load=0x2F000, fsz=0xD00, entry=0x90,
         # every observed branch target (module offsets); 0x2F010/0x2F340
         # are not auto-detected (no static branch edges to them).
         # NOTE +0x888 = LS 0x2F808: 0x2EF80 + 0x888 (a 0x388 here made the
         # companion probe compute implied base 0x2F480 and never match).
         extras=(0x90, 0x3C0, 0x888, 0x4B0),
         # first-branch companion entries: runtime calls (+0x4B0, +0x888)
         # and internal helpers reached when execution resumes mid-module
         # (+0x90, +0x3C0). One probe per offset.
         probe_entries=(0x4B0, 0x888, 0x90, 0x3C0)),
    dict(name="mstream_dsp_dbg_reverb", base=0x2FD00,
         load=0x2FD80, fsz=0x2180, entry=0xA0,
         extras=(), probe_entries=(0xA0,)),
]

# Image ids (both trees): 14/15 module-relative, 20/21 per-base.
IMG_ID = {"mstream_dsp_dbg_compressor": 14, "mstream_dsp_dbg_reverb": 15}
BASE_IMG_ID = {"mstream_dsp_dbg_compressor": 20, "mstream_dsp_dbg_reverb": 21}

# stale dirs from the superseded (wrong-base) analysis
STALE_DIRS = ["mstream_dsp_dbg_compressor_b2F3A0",
              "mstream_dsp_dbg_reverb_b2FD10"]


def build_pic(load, tail, fsz, entry):
    """Retail-.pic-shaped SPU ELF (ELF32 big-endian, ehdr + 3 phdrs, no
    sections): PT_LOAD at file 0x100 / vaddr 0x80 for fsz bytes, RW bss
    phdr, PT_NOTE of 0x34 bytes at file 0x100+fsz; the dump tail (note +
    .shstrtab names) follows verbatim, mirroring how Sony's .pic files lay
    the note and section-name bytes past the phdr-mapped file range."""
    load_off = 0x100
    note_off = load_off + fsz
    bss_vaddr = (0x80 + fsz + 0x7F) & ~0x7F

    def phdr(t, flags, off, vaddr, filesz, memsz, align):
        return struct.pack(">IIIIIIII", t, off, vaddr, vaddr,
                           filesz, memsz, flags, align)

    ehdr = struct.pack(
        ">16sHHIIIIIHHHHHH",
        bytes((0x7F, 0x45, 0x4C, 0x46, 1, 2, 1)) + b"\0" * 9,
        2, 23, 1,                      # ET_EXEC, EM_SPU, EV_CURRENT
        entry, 52, 0, 0,               # e_entry, e_phoff, e_shoff, e_flags
        52, 32, 3, 0, 0, 0)            # ehsize, phentsize, phnum, sh*
    ph1 = phdr(1, 5, load_off, 0x80, fsz, fsz, 0x80)          # PT_LOAD RX
    ph2 = phdr(1, 6, 0, bss_vaddr, 0, 0x20, 0x80)             # PT_LOAD RW bss
    ph3 = phdr(4, 4, note_off, 0, 0x34, 0, 4)                 # PT_NOTE R
    head = ehdr + ph1 + ph2 + ph3
    assert len(head) <= load_off, len(head)
    head += b"\0" * (load_off - len(head))
    return head + load + tail


def lift(pic, base, prefix, out, extras=()):
    cmd = [sys.executable, LIFTER, "--auto-functions", pic,
           "--base", hex(base), "--symbol-prefix", prefix, "-o", out,
           "--source-name", "spu_recomp.c", "--header-name", "spu_recomp.h"]
    if extras:
        # extra function starts (module vaddrs) the branch scanner cannot
        # see: indirect/runtime-dispatched targets such as compressor+0x4B0
        # (LS 0x2F430) and compressor+0x888 (LS 0x2F808).
        cmd += ["--extra-funcs", ",".join(hex(base + e) for e in extras)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write("".join("    " + l + "\n" for l in r.stdout.splitlines()))
    if r.returncode != 0:
        sys.stderr.write(r.stderr)
        sys.exit(f"lifter failed for {prefix}")
    return r.stdout


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dump", required=True)
    ap.add_argument("--trees", required=True, help="Comma-separated target-specific output trees")
    args = ap.parse_args()
    trees = [os.path.abspath(t.strip()) for t in args.trees.split(",") if t.strip()]
    if not trees:
        ap.error("--trees must contain at least one output directory")
    dump = open(args.dump, "rb").read()
    os.makedirs(IMAGES_DBG, exist_ok=True)
    for tree in trees:
        for d in STALE_DIRS:
            p = os.path.join(tree, d)
            if os.path.isdir(p):
                shutil.rmtree(p)
                print(f"removed stale dir {p}")

    for m in MODULES:
        lo = m["load"] - DUMP_BASE
        load = dump[lo:lo + m["fsz"]]
        assert len(load) == m["fsz"], m["name"]
        # PT_NOTE + .shstrtab tail: from load end up to the next 0x40-aligned
        # boundary past the section-name block (dump bytes are the file tail).
        tlo = lo + m["fsz"]
        tail = dump[tlo:tlo + 0x44]
        pic = build_pic(load, tail, m["fsz"], m["entry"])
        fnv = fnv1a64(load)
        pic_path = os.path.join(IMAGES_DBG, m["name"] + ".pic")
        with open(pic_path, "wb") as f:
            f.write(pic)
        print(f"{m['name']}: base 0x{m['base']:X} fsz 0x{m['fsz']:X} "
              f"entry 0x{m['entry']:X} loadfnv 0x{fnv:016X} "
              f"filefnv 0x{fnv1a64(pic):016X} -> {pic_path}")

        for tree in trees:
            # module-relative lift (identification + any-base interpreter path)
            lift(pic_path, 0, m["name"] + "_",
                 os.path.join(tree, m["name"]), m.get("extras", ()))
            # per-observed-base lift (native dispatch at the logged staging)
            lift(pic_path, m["base"], m["name"] + f"_b{m['base']:X}_",
                 os.path.join(tree, f"{m['name']}_b{m['base']:X}"),
                 m.get("extras", ()))

        img, bimg = IMG_ID[m["name"]], BASE_IMG_ID[m["name"]]
        for entry in m["probe_entries"]:
            if entry == m["probe_entries"][0]:
                vaddr, size = 0x80, m["fsz"]       # primary window
            else:
                # companion: shifted but module-internal window; keeps the
                # (fp, lift_base) dedup pair distinct per entry offset.
                vaddr = 0x80 + 0x20 * m["probe_entries"].index(entry)
                size = m["fsz"] - (vaddr - 0x80)
            win = load[vaddr - 0x80:vaddr - 0x80 + size]
            assert len(win) == size
            # Primary entry gets both the module-relative (interpreter
            # fallback at unseen bases) and the per-base (native) probe;
            # companions are per-base only -- the 16-slot probe table is
            # tight, and unseen-base stagings are covered by the primary
            # module probe for the runtime-call offset.
            lids = ((img, 0x0), (bimg, m["base"])) \
                if entry == m["probe_entries"][0] else ((bimg, m["base"]),)
            for lid, base in lids:
                tag = m["name"] if lid == img else f"{m['name']}@0x{m['base']:X}"
                print(f"    register_stage_probe(0x{fnv1a64(win):016X}ULL, "
                      f"{lid}, 0x{base:X}, 0x{entry:X}, 0x{vaddr:X}, "
                      f"0x{size:X}, \"{tag}+0x{entry:X}\");")


if __name__ == "__main__":
    main()
