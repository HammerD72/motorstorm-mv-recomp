#!/usr/bin/env python3
"""Lift a flash codec SPU ELF at a FIXED staging (link) base.

flashATRAC / flashMP3 stage themselves into SPURS task LS at runtime-chosen
bases (MS1 flashATRAC: 0x32880, 0x33880, 0x2F780).  The stock module lift is
module-relative (base 0): its labels, links and data absolutes are module
offsets, so it can only be served by the interpreter at a runtime base.
A lift built at base B runs natively exactly like the PM jobmods' link-base
0x4000 lifts: spu_lifter.py --auto-functions <elf> --base B moves every
function bound, label and registration address to B+off (vendor change), and
ila-encoded extern hops keep their original low-LS SPURS targets.

Usage:
  python lift_flash_codec_at_base.py --elf build/spu-inputs/flashATRAC.elf \
      --base 0x32880 --prefix flashATRAC_b32880 --code-end 0x7DC0 \
      --out build/generated/named/spu/flashATRAC_b32880 \
      --module-lift build/generated/named/spu/flashATRAC/spu_recomp.c

--module-lift (optional): the original module lift.  Its extern stub targets
are read and restored in the generated source if the base shift biased any
relative-encoded (brsl) extern call.
"""

import argparse
import os
import re
import subprocess
import sys

HERE    = os.path.dirname(os.path.abspath(__file__))
TOOLS   = os.path.normpath(os.path.join(
    HERE, "..", "..", "vendor", "ps3recomp", "tools"))


def debase_includes(path):
    """Rewrite '../../../runtime/spu/x.h' includes to bare 'x.h' so the TU
    builds from any tree (same rewrite build_spu_workloads.py applies)."""
    with open(path, "r", encoding="utf-8") as f:
        txt = f.read()
    new = re.sub(r'#include\s+"(?:\.\./)+runtime/spu/([A-Za-z0-9_]+\.h)"',
                 r'#include "\1"', txt)
    if new != txt:
        open(path, "w", encoding="utf-8", newline="\n").write(new)


def module_extern_stubs(module_lift_c):
    """Extern stub targets from the original module lift:

    void <p>spu_func_XXXXXXXX(spu_context* ctx) {
        ctx->pc = 0xXXXXXXu; g_spu_trampoline_fn = spu_indirect_branch; ... }

    A stub's own address and its ctx->pc target match in the module lift."""
    stubs = set()
    pat = re.compile(
        r"void\s+\w*spu_func_([0-9A-Fa-f]{8})\(spu_context\* ctx\)\s*\{\s*"
        r"ctx->pc = 0x([0-9A-Fa-f]+)u;\s*g_spu_trampoline_fn = spu_indirect_branch;")
    with open(module_lift_c, "r", encoding="utf-8") as f:
        for m in pat.finditer(f.read()):
            if int(m.group(1), 16) == int(m.group(2), 16):
                stubs.add(int(m.group(1), 16))
    return stubs


def fix_extern_stubs(out_c, base, externs):
    """Restore original extern targets the base shift biased.

    Every extern hop in these codecs is a RELATIVE branch: under the base
    shift the disassembler computes base+target and masks it to 18 bits
    (0x3FFFF), so a target like 0x164C0 lands at (base+0x164C0)&0x3FFFF.
    Rewrite the stub name and its ctx->pc back to the original target --
    the externs live in the host task's fixed low-LS SPURS area and must
    NOT follow the codec's staging base."""
    with open(out_c, "r", encoding="utf-8") as f:
        txt = f.read()
    fixed = 0
    for a in sorted(externs):
        biased = (base + a) & 0x3FFFF
        name_old, name_new = f"spu_func_{biased:08X}", f"spu_func_{a:08X}"
        fixed += txt.count(name_old)
        txt = txt.replace(name_old, name_new)
        for pc_old, pc_new in ((f"ctx->pc = 0x{biased:X}u", f"ctx->pc = 0x{a:X}u"),
                               (f"ctx->pc = 0x{biased:X};", f"ctx->pc = 0x{a:X};")):
            fixed += txt.count(pc_old)
            txt = txt.replace(pc_old, pc_new)
    with open(out_c, "w", encoding="utf-8", newline="\n") as f:
        f.write(txt)
    return fixed


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--elf", required=True)
    ap.add_argument("--base", required=True, help="staging base, e.g. 0x32880")
    ap.add_argument("--prefix", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--code-end", default=None,
                    help="passed through: trims data-tail pseudo-functions")
    ap.add_argument("--module-lift", default=None,
                    help="original module lift spu_recomp.c (extern stub source)")
    args = ap.parse_args()

    base = int(args.base, 0)
    cmd = [sys.executable, os.path.join(TOOLS, "spu_lifter.py"),
           "--auto-functions", args.elf,
           "--base", hex(base),
           "--symbol-prefix", args.prefix + "_",
           "--output", args.out]
    if args.code_end:
        # --code-end is expressed in MODULE space; the base shift moves the
        # bounds past it, so hand the lifter the shifted address.
        cmd += ["--code-end", hex(base + int(args.code_end, 0))]
    os.makedirs(args.out, exist_ok=True)
    subprocess.run(cmd, check=True)

    for fn in os.listdir(args.out):
        if fn.endswith((".c", ".h")):
            debase_includes(os.path.join(args.out, fn))

    if args.module_lift:
        externs = module_extern_stubs(args.module_lift)
        fixed = 0
        for fn in ("spu_recomp.c", "spu_recomp.h"):
            fixed += fix_extern_stubs(os.path.join(args.out, fn), base, externs)
        print(f"extern stubs restored: {fixed} (targets "
              f"{', '.join(hex(a) for a in sorted(externs))})")
    print(f"lifted {args.elf} at base 0x{base:X} -> {args.out}")


if __name__ == "__main__":
    main()
