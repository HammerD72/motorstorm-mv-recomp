"""Game-free checks for prompt flip completion (GCM_FLIP_PROMPT, GCM_FLIP_AT_TAKE).

Compiles the vendored cellGcmSys.c with scripts/fixtures/gcm_flip_prompt.c in
four processes (both switches off, each alone, both on; they are read once):
- off: the waiter leaves delivery to HLE boundaries and presents complete
  nothing, as before;
- prompt: a published completion is delivered by the waiting thread itself;
- take: taking a frame for present completes the wait it belongs to, never
  a newer one, never with nothing waiting, and not within one vblank period
  of the last completion; direct and FIFO flip requests both qualify;
- with either switch, the real FIFO walker holds at a flip with the next frame
  queued behind it until that frame is taken, and the frame is presented on
  its own (no merged frames); off, the get == put gate is unchanged.
Ten source mutants must build and fail. POSIX only (pthreads, mmap); Linux CI.
No game input; no performance claim."""
import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parent.parent
PS3 = ROOT / 'vendor/ps3recomp'
SOURCE = PS3 / 'libs/video/cellGcmSys.c'
FIXTURE = ROOT / 'scripts/fixtures/gcm_flip_prompt.c'
MODES = {'off': {}, 'prompt': {'GCM_FLIP_PROMPT': '1'}, 'take': {'GCM_FLIP_AT_TAKE': '1'},
         'both': {'GCM_FLIP_PROMPT': '1', 'GCM_FLIP_AT_TAKE': '1'}}
MUTANTS = {  # name: (original, replacement, mode that must fail)
    'waiter-does-not-pump': ('        if (prompt) { ppu_gcm_pump(); if (s_flip_status != CELL_GCM_FLIP_STATUS_WAITING) break; }\n        Sleep(1);',
                             '        Sleep(1);', 'prompt'),
    'older-frame-completes': ('    if (s_flip_present_gen != s_flip_wait_gen) return 0;   /* an older frame */\n', '', 'take'),
    'no-vblank-cap': ('    if (now - s_flip_last_complete_ns < min_interval_ns) return 0;   /* keep the vblank cap */\n', '', 'take'),
    'completes-without-wait': ('    if (s_flip_status != CELL_GCM_FLIP_STATUS_WAITING) return 0;\n    if (s_flip_present_gen', '    if (s_flip_present_gen', 'take'),
    'direct-flip-not-drained': ('        s_current_display_buffer_id = bufferId;\n        gcm_flip_drained();\n',
                                '        s_current_display_buffer_id = bufferId;\n', 'take'),
    'take-does-not-complete': ('    gcm_flip_complete_taken(GCM_VBLANK_PERIOD_NS);\n', '', 'take'),
    'drain-ignores-hold': ('    if (s_flip_hold && s_flip_pending && gcm_flip_hold_enabled()) return;\n', '', 'prompt'),
    'present-needs-caught-up': ('if (get != put && !(gcm_flip_hold_enabled() && s_flip_hold)) return 0;', 'if (get != put) return 0;', 'take'),
    'hold-without-switch': ('static int gcm_flip_hold_enabled(void) { return gcm_flip_prompt() || gcm_flip_at_take(); }',
                            'static int gcm_flip_hold_enabled(void) { return 1; }', 'off'),
    'stale-hold-kept': ('    if (s_flip_hold && !s_flip_pending) s_flip_hold = 0;   /* that flip was not accepted */\n', '', 'take'),
}


def build(out, label, source_text):
    d = out / label
    d.mkdir()
    src = d / 'cellGcmSys.c'
    src.write_text(source_text)
    exe = d / 'fixture'
    cc = os.environ.get('CC') or shutil.which('cc')
    # The copy's own relative includes fall back to the vendored tree via -I.
    cmd = [cc, '-std=gnu17', '-O1', '-Wall', '-ffunction-sections', '-fdata-sections',
           '-I' + str(PS3 / 'include'), '-I' + str(PS3 / 'libs/video'), f'-DGCM_SOURCE="{src}"',
           str(FIXTURE), '-pthread', '-Wl,--gc-sections', '-o', str(exe)]
    b = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    if b.returncode:
        return None, (b.stdout + b.stderr)[-1500:]
    return exe, ''


def run(exe, mode):
    env = {k: v for k, v in os.environ.items() if k not in ('GCM_FLIP_PROMPT', 'GCM_FLIP_AT_TAKE')}
    env.update(MODES[mode])
    r = subprocess.run([str(exe), mode], capture_output=True, text=True, timeout=120, env=env)
    return r.returncode, (r.stdout + r.stderr).strip().splitlines()[-1:] or ['']


def main():
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    out = ROOT / 'temp/checks' / ('gcm-flip-prompt-' + stamp)
    out.mkdir(parents=True)
    report = dict(game_inputs=False)
    if os.name == 'nt':
        print('fixture: POSIX pthread/mmap fixture runs on Linux CI')
        (out / 'result.json').write_text(json.dumps(dict(report, skipped='windows'), indent=2))
        return 0
    ok = True
    source = SOURCE.read_text()
    exe, log = build(out, 'production', source)
    if not exe:
        print('BUILD FAILED\n' + log)
        return 1
    report['production'] = {}
    for mode in MODES:
        rc, last = run(exe, mode)
        report['production'][mode] = dict(returncode=rc, output=last[0])
        print(f'{mode}: {last[0]}')
        ok &= rc == 0
    report['mutants'] = {}
    for name, (old, new, mode) in MUTANTS.items():
        assert source.count(old) == 1, name
        exe, log = build(out, 'mutant-' + name, source.replace(old, new))
        if not exe:
            report['mutants'][name] = dict(killed=False, build_log=log)
            print(f'mutant {name}: BUILD FAILED')
            ok = False
            continue
        rc, last = run(exe, mode)
        killed = rc != 0
        report['mutants'][name] = dict(killed=killed, mode=mode, output=last[0])
        print(f'mutant {name} ({mode}): {"killed: " + last[0] if killed else "SURVIVED"}')
        ok &= killed
    (out / 'result.json').write_text(json.dumps(report, indent=2))
    print('PASS' if ok else 'FAIL', out)
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
