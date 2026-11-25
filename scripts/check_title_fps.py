"""Compile the production title-bar statistics block against authored frames."""
from pathlib import Path
import subprocess
import tempfile
from check_rsx_submit_draws import VIDEO, compile_c
MARK = '    /* RPCS3-style titlebar stats'
FIXTURE = r'''#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
typedef uint64_t u64;
typedef unsigned long long ULONGLONG;
static struct { void* hwnd; u32 width, height; } s_d3d = { 0, 1280, 720 };
static int s_present_this_frame = 1;
static u32 s_dbg_last_draws = 7;
char g_rsx_title_base[128] = "T";
static ULONGLONG now = 5000;
static char title[256];
static int sets;
static ULONGLONG GetTickCount64(void) { return now; }
static void SetWindowTextA(void* hwnd, const char* text) {
    if (!hwnd) abort();
    sets++;
    memcpy(title, text, strlen(text) + 1);
}
static void frame(int present) {
    s_present_this_frame = present;
@BLOCK@
}
static void second_of(int presents, int batches) {   /* one second, presents spread over the batches */
    for (int i = 0; i < batches; i++) {
        now += (ULONGLONG)((i + 1) * 1000 / batches - i * 1000 / batches);
        frame((i + 1) * presents / batches != i * presents / batches);
    }
}
static int expect(const char* name, int count, int want_count, const char* want) {
    if (count == want_count && !strcmp(title, want)) return 0;
    fprintf(stderr, "FAIL %s: sets=%d title='%s'\n", name, count, title);
    return 1;
}
int main(void) {
    int bad = 0;
    frame(1);                                  /* starts the clock; no window yet */
    second_of(19, 19);                   /* a missing window only delays the update */
    bad |= expect("no window", sets, 0, "");
    s_d3d.hwnd = &s_d3d;
    now += 1000; frame(0);                     /* 20 presents and 21 batches over 2 s */
    bad |= expect("late window", sets, 1, "T | FPS: 10.00 | batches: 10.5 | draws: 7 | 1280x720");
    second_of(20, 200);
    bad |= expect("split", sets, 2, "T | FPS: 20.00 | batches: 200.0 | draws: 7 | 1280x720");
    second_of(0, 50);
    bad |= expect("nothing presented", sets, 3, "T | FPS: 0.00 | batches: 50.0 | draws: 7 | 1280x720");
    second_of(25, 25);
    bad |= expect("every batch presents", sets, 4, "T | FPS: 25.00 | batches: 25.0 | draws: 7 | 1280x720");
    now += 999; frame(1);
    bad |= expect("under a second", sets, 4, "T | FPS: 25.00 | batches: 25.0 | draws: 7 | 1280x720");
    printf("%s\n", bad ? "FAILED" : "ok");
    return bad;
}
'''
MUTANTS = (
    ('every batch counts as a frame', 'if (s_present_this_frame) s_tframes++;', 's_tframes++;', 'late window'),
    ('frames not reset', 's_tframes = 0; s_tbatches = 0;', 's_tbatches = 0;', 'split'),
    ('batches not reset', 's_tframes = 0; s_tbatches = 0;', 's_tframes = 0;', 'split'),
    ('interval not reset', '            s_tt0 = tnow;\n        }', '        }', 'split'),
    ('rates swapped', 's_tframes * 1000.0', 's_tbatches * 1000.0', 'late window'),
)


def block(text):
    """The statistics comment and the braced block after it."""
    start = text.index(MARK)
    assert text.count(MARK) == 1
    end = text.index('\n    }\n', start) + len('\n    }\n')
    return text[start:end]


def main():
    source = (VIDEO / 'rsx_d3d12_backend.c').read_text(encoding='utf-8')
    body = block(source)
    # The flag must still describe this frame: after Present, before the callers restore it.
    frame = source.index('static void render_frame(void)\n{')
    present = source.index('s_d3d.swap_chain->lpVtbl->Present(')
    assert frame < present < source.index(MARK) < source.index('s_present_this_frame = has_display;')
    assert 's_present_this_frame =' not in source[frame:source.index(MARK)]
    with tempfile.TemporaryDirectory(prefix='title-fps-') as directory:
        out = Path(directory)
        def run(name, text):
            unit = out / (name + '.c'); exe = out / (name + '.exe')
            unit.write_text(FIXTURE.replace('@BLOCK@', text), encoding='utf-8')
            compile_c(unit, exe, out)
            return subprocess.run([str(exe)], capture_output=True, text=True, timeout=10)
        r = run('production', body)
        if r.returncode != 0 or r.stdout.strip() != 'ok':
            raise RuntimeError(f'production: {r.returncode} {r.stdout} {r.stderr}')
        for i, (name, old, new, wrong) in enumerate(MUTANTS):
            assert body.count(old) == 1, name
            r = run(f'mutant{i}', body.replace(old, new))
            if r.returncode != 1 or f'FAIL {wrong}:' not in r.stderr:
                raise RuntimeError(f'{name} mutant not exposed by "{wrong}": {r.returncode} {r.stderr}')
    print(f'PASS: title-bar FPS counts presents, batches counts render_frame calls; 6 states, {len(MUTANTS)} mutants exposed')


if __name__ == '__main__':
    main()
