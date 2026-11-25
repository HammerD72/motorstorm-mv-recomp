#!/usr/bin/env python3
"""Whole-vector VMX float arithmetic in the vendored PPU lifter: same bits as the per-lane form.

vaddfp, vsubfp, vmaddfp and vnmsubfp used to be emitted as four byte-swapped
scalar lanes each. They now call the ppu_vx_* helpers of the source preamble,
which do the same single-precision operations on the whole vector with SSE2
(and keep the per-lane loops without it). This check compiles the lifter's
actual output for hand-assembled instructions next to the previous output and
compares every vector register bit for bit:

- operands: random bits, normal ranges, near-cancellation, zeros, denormals,
  infinities, NaNs with payloads;
- every MXCSR rounding mode, with and without flush-to-zero and
  denormals-are-zero (16 modes);
- distinct registers and each way the destination can alias a source;
- the SSE2 helpers and the per-lane fallback (PPU_VX_SCALAR);
- the lifted code's flags (clang -O1, no contraction, no strict aliasing) and -O2.

Lanes where both operands of one operation are NaN are compared too. Which NaN
comes out there follows the compiler's operand order, in both forms: with these
flags they agree, but with plain clang-cl /O1 the per-lane add returns the other
NaN. So the check builds with clang (clang-cl on Windows), as the lift does.

Compiled mutants of the helpers and of the emission must each differ.

No SDK, no ELF, no game bytes. Run: python scripts/check_ppu_vmx_float.py
"""

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SDK = ROOT / 'vendor/ps3recomp'
sys.path.insert(0, str(SDK / 'tools'))

import ppu_disasm                                   # noqa: E402
import ppu_lifter                                   # noqa: E402

# The emission before the helpers, per op (operands in encoding order vD,vA,vB,vC).
PREVIOUS = {
    'vmaddfp': ('{{ float a[4],b[4],c[4],d[4]; ppu_vldf4(&ctx->vr[{a}],a); '
                'ppu_vldf4(&ctx->vr[{b}],b); ppu_vldf4(&ctx->vr[{c}],c); '
                'for(int i=0;i<4;i++) d[i]=a[i]*c[i]+b[i]; ppu_vstf4(&ctx->vr[{d}],d); }}'),
    'vnmsubfp': ('{{ float a[4],b[4],c[4],d[4]; ppu_vldf4(&ctx->vr[{a}],a); '
                 'ppu_vldf4(&ctx->vr[{b}],b); ppu_vldf4(&ctx->vr[{c}],c); '
                 'for(int i=0;i<4;i++) d[i]=b[i]-a[i]*c[i]; ppu_vstf4(&ctx->vr[{d}],d); }}'),
    'vaddfp': ('{{ float a[4],b[4],d[4]; ppu_vldf4(&ctx->vr[{a}],a); ppu_vldf4(&ctx->vr[{b}],b); '
               'for(int i=0;i<4;i++) d[i]=a[i]+b[i]; ppu_vstf4(&ctx->vr[{d}],d); }}'),
    'vsubfp': ('{{ float a[4],b[4],d[4]; ppu_vldf4(&ctx->vr[{a}],a); ppu_vldf4(&ctx->vr[{b}],b); '
               'for(int i=0;i<4;i++) d[i]=a[i]-b[i]; ppu_vstf4(&ctx->vr[{d}],d); }}'),
}


def va_form(xo, d, a, b, c):
    return (4 << 26) | (d << 21) | (a << 16) | (b << 11) | (c << 6) | xo


def vx_form(xo, d, a, b):
    return (4 << 26) | (d << 21) | (a << 16) | (b << 11) | xo


ENCODE = {
    'vmaddfp': lambda d, a, b, c: va_form(46, d, a, b, c),
    'vnmsubfp': lambda d, a, b, c: va_form(47, d, a, b, c),
    'vaddfp': lambda d, a, b, c: vx_form(10, d, a, b),
    'vsubfp': lambda d, a, b, c: vx_form(74, d, a, b),
}

# (vD, vA, vB, vC): distinct, then the destination aliasing each source, then all one.
REGISTERS = [(3, 4, 5, 6), (4, 4, 5, 6), (5, 4, 5, 6), (6, 4, 5, 6), (7, 7, 7, 7), (8, 9, 8, 9)]

DRIVER = r"""
#include <xmmintrin.h>
#include <stdio.h>
typedef void (*ppu_vx_test_fn)(ppu_context*);
struct ppu_vx_test { const char* op; ppu_vx_test_fn now, before; };
static const struct ppu_vx_test TESTS[] = { TEST_TABLE };
static uint64_t rng = 0x9E3779B97F4A7C15ull;
static uint32_t next32(void) { rng ^= rng << 13; rng ^= rng >> 7; rng ^= rng << 17; return (uint32_t)(rng >> 21); }
static uint32_t operand(int kind) {
    static const uint32_t special[] = {
        0x00000000u, 0x80000000u, 0x7F800000u, 0xFF800000u, 0x7FC00000u, 0xFFC00000u, 0x7F800001u,
        0xFFA5A5A5u, 0x7FC12345u, 0x00000001u, 0x80000001u, 0x007FFFFFu, 0x00800000u, 0x80800000u,
        0x7F7FFFFFu, 0xFF7FFFFFu, 0x3F800000u, 0xBF800000u, 0x3F800001u, 0x34000000u, 0x33800000u};
    uint32_t r = next32();
    switch (kind) {
    case 0: return r;
    case 1: return (r & 0x807FFFFFu) | ((100u + next32() % 54u) << 23);   /* moderate normals */
    case 2: return special[r % (sizeof special / sizeof special[0])];
    case 3: return (r & 0x80000000u) | ((next32() % 4u) << 23) | (next32() & 0x7FFFFFu); /* tiny */
    case 4: return (r & 0x800FFFFFu) | ((120u + next32() % 8u) << 23);    /* close values */
    default: return (r & 0x807FFFFFu) | ((240u + next32() % 15u) << 23); /* near overflow */
    }
}
static void fill(ppu_context* ctx, int kind) {
    for (int v = 0; v < 32; v++)
        for (int i = 0; i < 4; i++) {
            uint32_t x = operand(kind == 6 ? (int)(next32() % 6u) : kind);
            ctx->vr[v].b[4 * i] = (uint8_t)(x >> 24); ctx->vr[v].b[4 * i + 1] = (uint8_t)(x >> 16);
            ctx->vr[v].b[4 * i + 2] = (uint8_t)(x >> 8); ctx->vr[v].b[4 * i + 3] = (uint8_t)x;
        }
    if (kind == 4)   /* b close to -(a*c) in register 5 of the distinct case: cancellation */
        for (int i = 0; i < 4; i++) {
            float a, c, p; uint32_t u;
            for (int k = 0; k < 4; k++) { ((uint8_t*)&u)[3 - k] = ctx->vr[4].b[4 * i + k]; } memcpy(&a, &u, 4);
            for (int k = 0; k < 4; k++) { ((uint8_t*)&u)[3 - k] = ctx->vr[6].b[4 * i + k]; } memcpy(&c, &u, 4);
            p = -(a * c); memcpy(&u, &p, 4); u ^= next32() & 7u;
            for (int k = 0; k < 4; k++) ctx->vr[5].b[4 * i + k] = (uint8_t)(u >> (24 - 8 * k));
        }
}
int main(int argc, char** argv) {
    long rounds = argc > 1 ? atol(argv[1]) : 2000;
    static const unsigned modes[4] = {_MM_ROUND_NEAREST, _MM_ROUND_DOWN, _MM_ROUND_UP, _MM_ROUND_TOWARD_ZERO};
    const unsigned saved = _mm_getcsr();
    long cases = 0, differ = 0, nan_choice = 0;
    static int shown[sizeof TESTS / sizeof TESTS[0]];
    static ppu_context x, y;
    const int strict_nan = argc > 2 && argv[2][0] == '1';
    for (long n = 0; n < rounds; n++)
        for (int m = 0; m < 16; m++) {
            fill(&x, (int)(n % 7));
            for (unsigned t = 0; t < sizeof TESTS / sizeof TESTS[0]; t++) {
                memcpy(&y, &x, sizeof x);
                ppu_context before; memcpy(&before, &x, sizeof x);
                _mm_setcsr((saved & ~0xE040u) | modes[m & 3] | ((m & 4) ? 0x8000u : 0) | ((m & 8) ? 0x0040u : 0));
                TESTS[t].now(&y);
                TESTS[t].before(&before);
                _mm_setcsr(saved);
                cases++;
                /* Lane by lane: a lane where both results are NaN but not the
                 * same NaN is the compiler's operand order (counted apart). */
                int lane_differ = 0, lane_nan = 0;
                for (int v = 0; v < 32; v++)
                    for (int i = 0; i < 4; i++) {
                        uint32_t p, q; memcpy(&p, &y.vr[v].b[4 * i], 4); memcpy(&q, &before.vr[v].b[4 * i], 4);
                        if (p == q) continue;
                        p = ppu_vbswap32(p); q = ppu_vbswap32(q);
                        if ((p & 0x7FFFFFFFu) > 0x7F800000u && (q & 0x7FFFFFFFu) > 0x7F800000u) lane_nan = 1;
                        else lane_differ = 1;
                    }
                nan_choice += lane_nan;
                if (lane_differ || (strict_nan && lane_nan)) {
                    if (!shown[t]++) printf("DIFFER %s test=%u mode=%d round=%ld\n", TESTS[t].op, t, m, n);
                    differ++;
                }
            }
        }
    printf("cases=%ld differ=%ld nan_choice=%ld\n", cases, differ, nan_choice);
    return differ != 0;
}
"""


def emitted(lifter, op, regs):
    word = ENCODE[op](*regs)
    insn = ppu_disasm.decode(word, 0x10000)
    if insn is None or insn.mnemonic != op:
        raise SystemExit(f'ENCODING {op} {word:08X} -> {insn and insn.mnemonic}')
    return lifter._translate_op(insn, ppu_lifter.LiftedFunction())


def translation_unit(preamble, emit):
    """A C++ unit: the lifter's own source preamble, then each op twice."""
    lines = [preamble.replace('#include "ppu_recomp.h"', '#include "ppu_recomp.h"\n#include <stdlib.h>'), '']
    table = []
    for k, (op, regs) in enumerate((op, r) for op in PREVIOUS for r in REGISTERS):
        d, a, b, c = regs
        lines.append(f'static void now_{k}(ppu_context* ctx) {{ {emit(op, regs)} }}')
        lines.append(f'static void before_{k}(ppu_context* ctx) {{ {PREVIOUS[op].format(d=d, a=a, b=b, c=c)} }}')
        table.append(f'{{"{op}", now_{k}, before_{k}}}')
    lines.append(DRIVER.replace('TEST_TABLE', ', '.join(table)))
    return '\n'.join(lines)


def main():
    if os.name == 'nt' and not (os.environ.get('INCLUDE') and os.environ.get('LIB')):
        sys.path.insert(0, str(ROOT / 'scripts'))
        from project import build_environment
        os.environ.update(build_environment())
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--compiler', default=os.environ.get('MV_CLANGXX') or ('clang-cl' if os.name == 'nt' else 'clang++'))
    ap.add_argument('--rounds', type=int, default=2000)
    args = ap.parse_args()
    cc = shutil.which(args.compiler) or args.compiler
    msvc = Path(cc).name.lower() in ('clang-cl', 'clang-cl.exe')
    out = ROOT / 'temp/checks' / f'ppu-vmx-float-{time.time_ns()}'
    out.mkdir(parents=True)
    (out / 'ppu_recomp.h').write_text(ppu_lifter.HEADER_PREAMBLE, encoding='utf-8')
    lifter = ppu_lifter.PPULifter()
    preamble = '\n'.join(lifter._preamble_lines())
    failures = []

    def build_and_run(name, source, opt='O1', define=None, strict_nan=False):
        cpp = out / f'{name}.cpp'
        cpp.write_text(source, encoding='utf-8')
        exe = out / (name + ('.exe' if os.name == 'nt' else ''))
        inc = [out, SDK / 'include']
        if msvc:
            cmd = [cc, '/nologo', '/TP', '/std:c++17', '/EHsc', f'/clang:-{opt}', '/Ob1', '/clang:-fno-strict-aliasing',
                   '/clang:-ffp-contract=off', '/w', '/D_CRT_SECURE_NO_WARNINGS',
                   *(['/D' + define] if define else []), *['/I' + str(x) for x in inc],
                   str(cpp), '/Fe' + str(exe), '/Fo' + str(out) + os.sep]
        else:
            cmd = [cc, '-std=c++17', f'-{opt}', '-fno-strict-aliasing', '-ffp-contract=off', '-w',
                   *(['-D' + define] if define else []), *['-I' + str(x) for x in inc], str(cpp), '-o', str(exe)]
        r = subprocess.run([str(x) for x in cmd], capture_output=True, text=True, errors='replace', timeout=300)
        if r.returncode:
            print(r.stdout[-3000:], r.stderr[-3000:])
            raise SystemExit(f'BUILD {name} failed')
        r = subprocess.run([str(exe), str(args.rounds), '1' if strict_nan else '0'], capture_output=True, text=True, errors='replace', timeout=600)
        return r.returncode, r.stdout.strip().splitlines()

    lift = lambda op, regs: emitted(lifter, op, regs)
    for op in PREVIOUS:
        text = lift(op, REGISTERS[0])
        if 'ppu_vx_' not in text or 'for(' in text:
            print(f'FAIL {op} is not emitted through a ppu_vx_ helper: {text}')
            failures.append(f'emission {op}')

    # At the lift's -O1 every lane must match, NaN choice included. At -O2 the
    # compiler may order operands differently in either form: only lanes where
    # both results are NaN may differ there, and they are reported.
    for name, opt, define, strict in [('sse2', 'O1', None, True), ('sse2-O2', 'O2', None, False),
                                      ('scalar', 'O1', 'PPU_VX_SCALAR', True)]:
        rc, lines = build_and_run(name, translation_unit(preamble, lift), opt, define, strict)
        status = 'ok  ' if rc == 0 else 'FAIL'
        print(f'{status} {name}: {lines[-1] if lines else "no output"}')
        if rc:
            print('\n'.join(lines[:8]))
            failures.append(name)

    # Each mutant must differ from the previous emission in lanes that are not both NaN.
    def swap_ld(text, old, new):
        assert text.count(old) == 1, old
        return text.replace(old, new)
    helper_mutants = [
        ('madd-multiplies-the-addend', 'vmaddfp',
         '_mm_add_ps(_mm_mul_ps(ppu_vx_ld(a), ppu_vx_ld(c)), ppu_vx_ld(b))',
         '_mm_add_ps(_mm_mul_ps(ppu_vx_ld(a), ppu_vx_ld(b)), ppu_vx_ld(c))'),
        ('nmsub-sign', 'vnmsubfp',
         '_mm_sub_ps(ppu_vx_ld(b), _mm_mul_ps(ppu_vx_ld(a), ppu_vx_ld(c)))',
         '_mm_sub_ps(_mm_mul_ps(ppu_vx_ld(a), ppu_vx_ld(c)), ppu_vx_ld(b))'),
        ('sub-order', 'vsubfp',
         '_mm_sub_ps(ppu_vx_ld(a), ppu_vx_ld(b))', '_mm_sub_ps(ppu_vx_ld(b), ppu_vx_ld(a))'),
        ('load-swaps-halves-only', 'vaddfp',
         '    __m128i x = _mm_loadu_si128((const __m128i*)v);\n    x = _mm_or_si128(_mm_slli_epi16(x, 8), _mm_srli_epi16(x, 8));\n',
         '    __m128i x = _mm_loadu_si128((const __m128i*)v);\n'),
        ('store-without-word-swap', 'vaddfp',
         '    x = _mm_shufflehi_epi16(_mm_shufflelo_epi16(x, _MM_SHUFFLE(2, 3, 0, 1)), _MM_SHUFFLE(2, 3, 0, 1));\n    _mm_storeu_si128',
         '    _mm_storeu_si128'),
    ]
    for name, op, old, new in helper_mutants:
        source = translation_unit(swap_ld(preamble, old, new), lift)
        rc, lines = build_and_run('mutant-' + name, source)
        hit = any(f'DIFFER {op} ' in x for x in lines)
        print(f'{"ok  " if rc and hit else "FAIL"} mutant {name} differs in {op}: {lines[-1] if lines else ""}')
        if not (rc and hit):
            failures.append('mutant ' + name)
    # The emission must keep the encoding order: vB is the addend, vC the multiplicand.
    swapped = lambda op, regs: lift(op, (regs[0], regs[1], regs[3], regs[2]) if op in ('vmaddfp', 'vnmsubfp') else regs)
    rc, lines = build_and_run('mutant-emission-b-c', translation_unit(preamble, swapped))
    hit = all(any(f'DIFFER {op} ' in x for x in lines) for op in ('vmaddfp', 'vnmsubfp'))
    print(f'{"ok  " if rc and hit else "FAIL"} mutant emission with vB and vC swapped differs: {lines[-1] if lines else ""}')
    if not (rc and hit):
        failures.append('mutant emission-b-c')

    if failures:
        print('FAILED: ' + ', '.join(failures))
        return 1
    print('PASS: whole-vector vaddfp/vsubfp/vmaddfp/vnmsubfp give the per-lane bits; 6 mutants rejected')
    return 0


if __name__ == '__main__':
    sys.exit(main())
