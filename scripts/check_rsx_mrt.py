"""Run fork MRT method and WARP pixel regression against vendored sources."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / 'vendor/ps3recomp/libs/video'
FIXTURES = ROOT / 'scripts/fixtures'


def main():
    if os.name != 'nt':
        raise SystemExit('MRT WARP test requires Windows')
    from project import build_environment
    env = build_environment()
    with tempfile.TemporaryDirectory(prefix='mrt-check-') as directory:
        out = Path(directory)
        common = [env['MV_CLANG'], '/nologo', '/D_CRT_SECURE_NO_WARNINGS',
                  '/I' + str(VIDEO), '/I' + str(ROOT / 'vendor/ps3recomp/include')]
        cases = [
            ('masks', [FIXTURES / 'rsx_mrt_masks.c', VIDEO / 'rsx_commands.c'], []),
            ('observer', [FIXTURES / 'rsx_mrt_observer.c'], []),
            ('gpu', [FIXTURES / 'rsx_mrt_masks_gpu.cpp'],
             ['/std:c++17', '/EHsc', '/link', 'd3d12.lib', 'dxgi.lib', 'd3dcompiler.lib']),
        ]
        for name, sources, options in cases:
            exe = out / (name + '.exe')
            command = common + [str(p) for p in sources] + ['/Fe' + str(exe)] + options
            subprocess.run(command, cwd=out, env=env, check=True, timeout=120)
            command = ([sys.executable, str(FIXTURES / 'rsx_mrt_masks_verify.py'), str(exe)]
                       if name == 'gpu' else [str(exe)])
            subprocess.run(command, cwd=out, env=env, check=True, timeout=150)
    return 0


if __name__ == '__main__':
    sys.exit(main())
