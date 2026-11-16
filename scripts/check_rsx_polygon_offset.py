"""Polygon offset: the NV4097 methods reach rsx_state, survive the packed PSO
cull key, and land in the D3D12 rasterizer depth bias. Mutants that drop the
decode, the key bits or the PSO hookup must be caught."""
import os
from pathlib import Path
import re
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / 'vendor/ps3recomp/libs/video'
BACKEND = VIDEO / 'rsx_d3d12_backend.c'
COMMANDS = VIDEO / 'rsx_commands.c'
HOOK = '    rsx_cull_depth_bias(cull, &pd.RasterizerState);\n'

HARNESS = r'''
#include <stdio.h>
#include <string.h>
typedef unsigned int u32;
typedef int INT;
typedef struct { INT DepthBias; float DepthBiasClamp; float SlopeScaledDepthBias; } D3D12_RASTERIZER_DESC;
#define NV4097_SET_POLY_OFFSET_FILL_ENABLE 0x0A68
#define NV4097_SET_POLYGON_OFFSET_SCALE_FACTOR 0x0A78
#define NV4097_SET_POLYGON_OFFSET_BIAS 0x0A7C
typedef struct { int cull_face_enable; u32 cull_face, front_face;
                 int poly_offset_fill; float poly_offset_factor, poly_offset_units; } rsx_state;
static int decode(rsx_state* state, u32 method, u32 data) {
@DECODE@
    return 1;
}
@KEY@
static u32 f(float v) { u32 u; memcpy(&u, &v, 4); return u; }
int main(void) {
    int failures = 0, checks = 0;
    struct { int on; float factor, units; u32 cull_enable, front; INT bias; float slope; } cases[] = {
        {1, 2.0f, 0.0f, 0, 0x0901, 0, 2.0f},      /* MotorStorm's per-frame offset */
        {1, 1.0f, 1.0f, 1, 0x0900, 1, 1.0f},
        {1, -1.5f, -4.0f, 1, 0x0901, -4, -1.5f},
        {1, 0.0f, 2047.0f, 0, 0x0900, 2047, 0.0f},
        {0, 2.0f, 4.0f, 1, 0x0901, 0, 0.0f},      /* fill offset off */
    };
    for (unsigned i = 0; i < sizeof cases / sizeof cases[0]; i++) {
        rsx_state st; memset(&st, 0, sizeof st);
        st.cull_face_enable = (int)cases[i].cull_enable; st.cull_face = 0x0405; st.front_face = cases[i].front;
        decode(&st, NV4097_SET_POLY_OFFSET_FILL_ENABLE, (u32)cases[i].on);
        decode(&st, NV4097_SET_POLYGON_OFFSET_SCALE_FACTOR, f(cases[i].factor));
        decode(&st, NV4097_SET_POLYGON_OFFSET_BIAS, f(cases[i].units));
        const u32 k = rsx_cull_key(&st);
        D3D12_RASTERIZER_DESC rd = {0, 0.0f, 0.0f};
        rsx_cull_depth_bias(k, &rd);
        const u32 low = (cases[i].cull_enable ? 1u : 0u) | (cases[i].front == 0x0901 ? 4u : 0u);
        const int ok = rd.DepthBias == cases[i].bias && rd.SlopeScaledDepthBias == cases[i].slope &&
                       (k & 7u) == low && (cases[i].on || k == low);
        checks++; failures += !ok;
        printf("%s case %u key=0x%08X bias=%d slope=%g\n", ok ? "PASS" : "FAIL", i, k,
               rd.DepthBias, rd.SlopeScaledDepthBias);
    }
    printf("checks=%d failures=%d\n", checks, failures);
    return failures ? 1 : 0;
}
'''


def extract(source, start, end_marker):
    i = source.index(start)
    j = source.index(end_marker, i)
    return source[i:j]


def harness(backend, commands):
    key = extract(backend, 'static int rsx_cull_clamp(', '\nstatic u32 rsx_blend_key(')
    m = re.search(r'    /\* Polygon offset: fill enable.*?OFFSET_BIAS\) \{.*?return 0;\n    \}\n',
                  commands, re.S)
    assert m, 'polygon offset decode not found in rsx_commands.c'
    return HARNESS.replace('@DECODE@', m.group(0)).replace('@KEY@', key)


def run(env, out, name, backend, commands):
    src = out / (name + '.c')
    src.write_text(harness(backend, commands), encoding='utf-8')
    exe = out / (name + '.exe')
    subprocess.run([env['MV_CLANG'], '/nologo', '/D_CRT_SECURE_NO_WARNINGS', str(src), '/Fe' + str(exe)],
                   cwd=out, env=env, check=True, timeout=120, capture_output=True)
    return subprocess.run([str(exe)], capture_output=True, text=True, timeout=30)


def main():
    if os.name != 'nt':
        raise SystemExit('polygon offset check builds with clang-cl on Windows')
    from project import build_environment
    env = build_environment()
    backend = BACKEND.read_text(encoding='utf-8')
    commands = COMMANDS.read_text(encoding='utf-8')
    assert backend.count(HOOK) == 1, 'VP PSO does not apply rsx_cull_depth_bias'
    with tempfile.TemporaryDirectory(prefix='poly-offset-') as directory:
        out = Path(directory)
        result = run(env, out, 'actual', backend, commands)
        assert result.returncode == 0 and 'failures=0' in result.stdout, result.stdout
        print('PASS decode -> cull key -> depth bias: ' + result.stdout.strip().splitlines()[-1])
        mutants = {
            'no-decode': (backend, commands.replace('state->poly_offset_fill = data ? 1 : 0;',
                                                    'state->poly_offset_fill = 0;')),
            'no-key-bits': (backend.replace('k |= 8u |', 'k |= 0u &'), commands),
            'units-unsigned': (backend.replace('(INT)((int)(cull << 16) >> 20)',
                                               '(INT)((cull >> 4) & 0xFFFu)'), commands),
        }
        for name, (b, c) in mutants.items():
            result = run(env, out, name, b, c)
            assert result.returncode == 1, (name, result.stdout)
            print('mutant ' + name + ': killed')
        print('PASS VP PSO applies the depth bias (hook present once)')


if __name__ == '__main__':
    main()
