"""Render the FACING input (0xE) through the production decompiler, plus a
control where FACING decodes as zero (the old behaviour) and must fail."""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
VIDEO = ROOT / 'vendor/ps3recomp/libs/video'
CASE = '    case 0xE: return "(input.rsx_front ? float4(1,1,1,1) : float4(-1,-1,-1,-1))";\n'


def build(env, out, source, name):
    common = [env['MV_CLANG'], '/nologo', '/D_CRT_SECURE_NO_WARNINGS',
              '/I' + str(VIDEO), '/I' + str(ROOT / 'vendor/ps3recomp/include')]
    obj, exe = out / (name + '.obj'), out / (name + '.exe')
    subprocess.run(common + ['/c', str(source), '/Fo' + str(obj)],
                   cwd=out, env=env, check=True, timeout=120)
    subprocess.run(common + ['/std:c++17', '/EHsc',
                   str(ROOT / 'scripts/fixtures/rsx_fp_facing_gpu.cpp'),
                   str(obj), '/Fe' + str(exe), '/link',
                   'd3d11.lib', 'dxgi.lib', 'd3dcompiler.lib'],
                   cwd=out, env=env, check=True, timeout=120)
    return exe


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--warp-only', action='store_true')
    args = parser.parse_args()
    if os.name != 'nt':
        raise SystemExit('FACING GPU test requires Windows')
    from project import build_environment
    env = build_environment()
    source = (VIDEO / 'rsx_fp_decompiler.c').read_text(encoding='utf-8')
    assert source.count(CASE) == 1, 'FACING case not found'
    with tempfile.TemporaryDirectory(prefix='fp-facing-') as directory:
        out = Path(directory)
        control = out / 'rsx_fp_decompiler_zero_facing.c'
        control.write_text(source.replace(CASE, ''), encoding='utf-8')
        actual = build(env, out, VIDEO / 'rsx_fp_decompiler.c', 'facing')
        zero = build(env, out, control, 'zero')
        for hardware in ([False] if args.warp_only else [False, True]):
            label = 'hardware' if hardware else 'WARP'
            flag = ['--hardware'] if hardware else []
            result = subprocess.run([str(actual), *flag], capture_output=True, text=True, timeout=40)
            assert result.returncode == 0 and 'checks=4 failures=0' in result.stdout, result
            print(label + ': checks=4 failures=0 (FACING +1 front, -1 back, CW and CCW)')
            result = subprocess.run([str(zero), *flag], capture_output=True, text=True, timeout=40)
            assert result.returncode == 1 and 'FAIL' in result.stdout, result
            print(label + ' control (FACING decoded as zero): rejected')


if __name__ == '__main__':
    main()
