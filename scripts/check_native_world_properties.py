"""Check deterministic exact-domain forests and reject two implementation mutants.

Windows: run in an x64 C++ developer shell; set MV_CLANG to clang-cl if needed.
Linux: CXX=c++ python3 scripts/check_native_world_properties.py
Requires x86-64 AVX2/FMA plus OS XMM/YMM support. Unsupported hosts exit 77.
This is separate from check_native_world_transforms.py's rounding/fusion gate.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'src/motorstorm/world_transforms.cpp'
FIXTURES = ROOT / 'scripts/fixtures'
PROTECTED = [SOURCE, SOURCE.with_suffix('.h'), ROOT / 'scripts/check_native_world_transforms.py',
             FIXTURES / 'native_world_transforms.cpp']
DEFAULT_SEED = 0x186C0FFEE


def seed_value(value):
    number = int(value, 0)
    if not 0 <= number < 1 << 64:
        raise argparse.ArgumentTypeError('seed must be an unsigned 64-bit integer')
    return number


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seed', type=seed_value, default=DEFAULT_SEED)
    parser.add_argument('--case-index', type=int, help='explore just one case (0..511); mutation controls omitted')
    parser.add_argument('--replay', type=Path, help='replay a minimized WORLD_FOREST_V1 input; mutation controls omitted')
    parser.add_argument('--compiler-style', choices=('msvc', 'gnu'), default='msvc' if os.name == 'nt' else 'gnu',
                        help='compiler command syntax; Windows MinGW clang++ can use gnu')
    args = parser.parse_args(argv)
    if args.case_index is not None and not 0 <= args.case_index < 512:
        parser.error('--case-index must be in 0..511')
    if args.case_index is not None and args.replay:
        parser.error('--case-index and --replay are mutually exclusive')
    if args.replay and not args.replay.is_file():
        parser.error('--replay must name an existing authored input')
    if platform.machine().lower() not in ('amd64', 'x86_64') or platform.system() not in ('Windows', 'Linux'):
        print('UNSUPPORTED: requires an x86-64 Windows or Linux host', file=sys.stderr)
        return 77
    msvc = args.compiler_style == 'msvc'
    compiler = os.environ.get('MV_CLANG') or os.environ.get('CXX') or shutil.which('clang-cl' if msvc else 'c++')
    if not compiler:
        parser.error('set MV_CLANG/CXX to a C++ compiler path (use an x64 developer shell on Windows)')
    base = ROOT / 'build/self-tests/native-world-properties'
    base.mkdir(parents=True, exist_ok=True)
    out = Path(tempfile.mkdtemp(prefix='run-', dir=base))
    started = time.monotonic()
    before = {str(path.relative_to(ROOT)): digest(path) for path in PROTECTED}
    print(f'Seed: {args.seed} (0x{args.seed:x}); output: {out}', flush=True)
    manifest = dict(seed=args.seed, compiler=compiler, compiler_style=args.compiler_style,
                    protected_sha256=before, status='failed')

    def execute(command, name, timeout):
        (out / (name + '-command.json')).write_text(json.dumps(command, indent=2) + '\n', encoding='utf-8')
        try:
            result = subprocess.run(command, cwd=out, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f'{name}: exceeded {timeout}s; command is recorded in {out}') from exc
        (out / (name + '.log')).write_text(result.stdout + result.stderr, encoding='utf-8')
        return result

    def build(label, implementation, host=False):
        folder = out / label
        folder.mkdir()
        exe = folder / ('fixture.exe' if os.name == 'nt' else 'fixture')
        inputs = [str(FIXTURES / 'world_properties_host.cpp')] if host else [str(implementation), str(FIXTURES / 'native_world_properties.cpp')]
        if msvc:
            flags = ['/nologo', '/std:c++17', '/O2', '/UNDEBUG', '/EHsc', '/W4', '/WX', '/fp:strict']
            if not host:
                flags += ['/arch:AVX2']
            command = [compiler, *flags, '/I' + str(SOURCE.parent), *inputs,
                       '/Fo' + str(folder) + os.sep, '/Fe:' + str(exe)]
        else:
            flags = ['-std=c++17', '-O2', '-UNDEBUG', '-Wall', '-Wextra', '-Werror',
                     '-fno-fast-math', '-ffp-contract=off', '-frounding-math']
            flags += ['-mno-avx', '-mno-avx2', '-mno-fma'] if host else ['-mavx2', '-mfma', '-mfxsr']
            command = [compiler, *flags, '-I' + str(SOURCE.parent), *inputs, '-o', str(exe)]
        result = execute(command, label + '-build', 120)
        if result.returncode:
            raise RuntimeError(f'{label} compile failed:\n{result.stdout}{result.stderr}')
        return exe

    def run(exe, label):
        result = execute([str(exe), str(args.seed), str(args.case_index if args.case_index is not None else -1),
                          str(args.replay.resolve()) if args.replay else '-'], label + '-run', 30)
        match = re.search(r'BEGIN_REPRODUCER\r?\n(.*?)END_REPRODUCER', result.stderr, re.S)
        if match:
            reproducer = out / (label + '-reproducer.txt')
            reproducer.write_text(match[1], encoding='utf-8')
            print(f'{label}: minimized input saved to {reproducer}', flush=True)
            print(f'Replay: "{exe}" {args.seed} -1 "{reproducer}"', flush=True)
        return result

    try:
        # The baseline executable contains no AVX instructions and runs before
        # any separately compiled kernel/test executable can be entered.
        host = build('host', None, host=True)
        supported = execute([str(host)], 'host-run', 10)
        if supported.returncode == 77:
            print('UNSUPPORTED: AVX2/FMA or OS XMM/YMM support is absent; vector tests were not entered.', file=sys.stderr)
            manifest['status'] = 'unsupported'
            return 77
        if supported.returncode:
            raise RuntimeError('baseline capability probe failed: ' + supported.stdout + supported.stderr)
        print(supported.stdout.strip(), flush=True)
        production = build('production', SOURCE)
        result = run(production, 'production')
        print(result.stdout + result.stderr, end='', flush=True)
        if result.returncode:
            raise RuntimeError(f'production property check failed (exit {result.returncode}); see {out}')
        if args.replay or args.case_index is not None:
            manifest['status'] = 'exploration_passed'
            print('EXPLORATION ONLY: selected case/replay passed; full forest and mutation gates were not run.')
            return 0
        original = SOURCE.read_text(encoding='utf-8')
        mutations = {
            'wrong-root-translation': [('translations[h.root[i]].value', 'translations[0].value')],
            'reversed-parent-local': [
                ('const auto& parent = h.world[h.parent[i]];', 'const auto& parent = h.local[i];'),
                ('const auto* a = h.local[i].row[row].value;', 'const auto* a = h.world[h.parent[i]].row[row].value;'),
            ],
        }
        for label, replacements in mutations.items():
            changed = original
            for old, new in replacements:
                if changed.count(old) != 1:
                    raise RuntimeError(f'{label}: production mutation anchor changed; update the authored control')
                changed = changed.replace(old, new, 1)
            mutant = out / (label + '.cpp')
            mutant.write_text(changed, encoding='utf-8')
            exe = build(label, mutant)
            negative = run(exe, label)
            if negative.returncode != 1 or 'FAIL property=world_matrix phase=topology-dirty' not in negative.stderr:
                raise RuntimeError(f'{label} did not fail the independent matrix oracle:\n{negative.stdout}{negative.stderr}')
            print(f'PASS: {label} rejected by independent matrix oracle', flush=True)
            print(negative.stderr.strip(), flush=True)
        manifest['status'] = 'passed'
        print('PASS: full 512-forest suite, both orderings, caller modes and both mutation controls', flush=True)
        return 0
    except (OSError, RuntimeError) as exc:
        print(f'FAIL: {exc}', file=sys.stderr)
        return 1
    finally:
        after = {str(path.relative_to(ROOT)): digest(path) for path in PROTECTED}
        manifest['elapsed_seconds'] = round(time.monotonic() - started, 3)
        manifest['protected_inputs_unchanged'] = after == before
        (out / 'result.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        if after != before:
            raise RuntimeError('protected production/legacy inputs changed during the check')


if __name__ == '__main__':
    sys.exit(main())
