"""Per-draw replay lookups: game-free checks that the faster forms answer exactly as before.

1. Sampler tables. Compiles the production guest_sampler_hash and
   guest_sampler_find (rsx_d3d12_sampler_tables.inc) with an authored
   D3D12_SAMPLER_DESC of the real 52-byte layout. The hash is only a
   pre-check, so find must return exactly what a memcmp-only search returns:
   checked over heaps of authored tables that share long prefixes, differ in
   one byte, and repeat. The hash must depend on the bytes alone and change
   under every single-bit flip of a table (so every byte is read).
2. Render-target index. Compiles the production off_rt_find, its index and
   off_rt_changed with an authored pool of the fields it reads (res, off).
   Random runs of the changes off_rt_get and off_rt_grow make (create, evict,
   release on resize, failed create, growth by realloc, re-initialization)
   compare every lookup with the original scan, including offsets held by two
   live slots (the lowest slot wins), dead slots, absent offsets and 0, and
   with allocation failures (the scan answers). Growth and re-initialization
   are also run without off_rt_changed, so the pool and capacity checks are
   exercised on their own.
3. Wiring. In the backend, every write of an OffRT slot's res or off is in
   off_rt_get and is followed by off_rt_changed(); no other video source
   writes them.

Mutants of each part must fail. No GPU, no game data, no performance claim.
Run: python scripts/check_rsx_render_lookups.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIDEO = ROOT / 'vendor/ps3recomp/libs/video'
BACKEND = VIDEO / 'rsx_d3d12_backend.c'
TABLES = VIDEO / 'rsx_d3d12_sampler_tables.inc'
WINDOWS = os.name == 'nt'


def read(path):
    return path.read_text(encoding='utf-8').replace('\r\n', '\n')


def function(text, signature):
    start = text.index(signature)
    opening = text.index('{', start)
    clean = re.sub(r"/\*[\s\S]*?\*/|//[^\n]*|\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'", lambda m: ' ' * len(m[0]), text)
    depth, i = 1, opening + 1
    while depth:
        depth += (clean[i] == '{') - (clean[i] == '}')
        i += 1
    return text[start:i]


def compiler():
    if WINDOWS:
        if (ROOT / 'scripts/project.py').exists():
            sys.path.insert(0, str(ROOT / 'scripts'))
            from project import build_environment
            os.environ.update(build_environment())
        cc = os.environ.get('MV_CLANG') or shutil.which('clang-cl')
    else:
        cc = os.environ.get('CC') or shutil.which('cc') or shutil.which('clang')
    if not cc:
        raise SystemExit('C compiler required')
    return cc


def build(cc, source, exe, directory):
    src = directory / (exe.stem + '.c')
    src.write_text(source, encoding='utf-8')
    if WINDOWS:
        cmd = [cc, '/nologo', '/TC', '/O2', '/UNDEBUG', '/D_CRT_SECURE_NO_WARNINGS', str(src),
               '/Fo' + str(directory) + os.sep, '/Fe' + str(exe)]
    else:
        cmd = [cc, '-std=gnu11', '-O2', '-UNDEBUG', str(src), '-o', str(exe)]
    r = subprocess.run(cmd, cwd=directory, capture_output=True, text=True, timeout=180)
    if r.returncode:
        raise SystemExit(f'compile failed: {" ".join(cmd)}\n{r.stdout}\n{r.stderr}')


def run(exe):
    try:
        return subprocess.run([str(exe)], capture_output=True, text=True, timeout=20)
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess([str(exe)], 124, '', 'timeout')


def mutate(text, old, new):
    assert text.count(old) == 1, f'mutant anchor not unique: {old[:60]!r}'
    return text.replace(old, new)


# ------------------------------------------------------------ 1. sampler tables
SAMPLER = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef struct {                 /* d3d12.h layout: 13 four-byte fields */
    int Filter, AddressU, AddressV, AddressW;
    float MipLODBias; unsigned MaxAnisotropy; int ComparisonFunc;
    float BorderColor[4]; float MinLOD, MaxLOD;
} D3D12_SAMPLER_DESC;
typedef struct { int unused; } ID3D12DescriptorHeap;
#define GUEST_SAMPLER_TABLES 128
@HEAP@
@HASH@
@FIND@
static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); if (++fails > 20) exit(1); } } while (0)
static unsigned seed = 12345u;
static unsigned rnd(void) { seed = seed * 1103515245u + 12345u; return seed >> 8; }

/* The search the hash pre-checks: the first table equal byte for byte. */
static GuestSamplerHeap* reference_find(GuestSamplerHeap* h, const D3D12_SAMPLER_DESC* d, unsigned* slot)
{
    for (; h; h = h->next)
        for (unsigned i = 0; i < h->count; ++i)
            if (!memcmp(d, h->tables[i], 16 * sizeof(*d))) { *slot = i; return h; }
    return NULL;
}
static void author(D3D12_SAMPLER_DESC* d, unsigned family)
{
    unsigned char* b = (unsigned char*)d;
    for (size_t i = 0; i < 16 * sizeof(*d); ++i) b[i] = (unsigned char)(family * 31u + (i % 7u == 0 ? i : 0));
}
int main(void)
{
    if (sizeof(D3D12_SAMPLER_DESC) != 52) { printf("FAIL desc layout\n"); return 1; }
    D3D12_SAMPLER_DESC a[16], b[16];
    author(a, 3);
    memcpy(b, a, sizeof a);
    CHECK(guest_sampler_hash(a) == guest_sampler_hash(b));
    const unsigned long long base = guest_sampler_hash(a);
    unsigned same = 0;
    for (size_t bit = 0; bit < sizeof(a) * 8; ++bit) {
        ((unsigned char*)b)[bit / 8] ^= (unsigned char)(1u << (bit % 8));
        same += guest_sampler_hash(b) == base;
        ((unsigned char*)b)[bit / 8] ^= (unsigned char)(1u << (bit % 8));
    }
    CHECK(same == 0);

    static GuestSamplerHeap heaps[3];
    unsigned found = 0, absent = 0;
    for (int round = 0; round < 400; ++round) {
        memset(heaps, 0, sizeof heaps);
        heaps[0].next = &heaps[1];
        GuestSamplerHeap* lists[2] = {&heaps[0], &heaps[2]};
        for (int k = 0; k < 3; ++k) {
            heaps[k].count = rnd() % (GUEST_SAMPLER_TABLES + 1);
            for (unsigned i = 0; i < heaps[k].count; ++i) {
                author(heaps[k].tables[i], rnd() % 6);               /* long shared prefixes */
                if (rnd() % 3) ((unsigned char*)heaps[k].tables[i])[rnd() % sizeof(a)] ^= (unsigned char)(1 + rnd() % 255);
                heaps[k].hash[i] = guest_sampler_hash(heaps[k].tables[i]);
            }
        }
        for (int q = 0; q < 200; ++q) {
            D3D12_SAMPLER_DESC d[16];
            GuestSamplerHeap* src = &heaps[rnd() % 3];
            if (src->count && rnd() % 4) memcpy(d, src->tables[rnd() % src->count], sizeof d);
            else { author(d, rnd() % 6); if (rnd() % 2) ((unsigned char*)d)[rnd() % sizeof(a)] ^= 0x40; }
            for (int l = 0; l < 2; ++l) {
                unsigned s1 = 999, s2 = 999;
                GuestSamplerHeap* g = guest_sampler_find(lists[l], guest_sampler_hash(d), d, &s1);
                GuestSamplerHeap* w = reference_find(lists[l], d, &s2);
                CHECK(g == w);
                if (w) { CHECK(s1 == s2); ++found; } else ++absent;
            }
        }
    }
    CHECK(found > 1000 && absent > 1000);
    if (fails) return 1;
    printf("sampler: %u found, %u absent, every bit flip changes the hash\n", found, absent);
    return 0;
}
'''


def sampler_source(tables):
    heap = re.search(r'typedef struct GuestSamplerHeap \{[\s\S]*?\} GuestSamplerHeap;', tables)[0]
    return (SAMPLER.replace('@HEAP@', heap)
            .replace('@HASH@', function(tables, 'static unsigned long long guest_sampler_hash('))
            .replace('@FIND@', function(tables, 'static GuestSamplerHeap* guest_sampler_find(')))


SAMPLER_MUTANTS = [
    ('hash-drops-a-lane', 'for (unsigned k = 0; k < 4; ++k) {\n            unsigned long long w;',
     'for (unsigned k = 0; k < 3; ++k) {\n            unsigned long long w;'),
    ('hash-mix-drops-a-lane', 'for (unsigned k = 0; k < 4; ++k) { r =', 'for (unsigned k = 1; k < 4; ++k) { r ='),
    ('hash-word-overlap', 'memcpy(&w, b + i + 8 * k, 8);', 'memcpy(&w, b + i + 4 * k, 8);'),
]

# -------------------------------------------------------- 2. render-target index
INDEX = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32;
typedef struct { void* res; u32 off; } OffRT;         /* the fields the index reads */
static struct { OffRT* off_rt; int off_rt_cap; } s_d3d;
static int fail_alloc;                                 /* refuse the next N allocations */
static unsigned refused;
static void* test_malloc(size_t n) { if (fail_alloc > 0) { --fail_alloc; ++refused; return NULL; } return malloc(n); }
#define malloc test_malloc
@INDEX@
#undef malloc
static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); if (++fails > 20) exit(1); } } while (0)
static unsigned seed = 777u;
static unsigned rnd(void) { seed = seed * 1103515245u + 12345u; return seed >> 8; }
static char live;                                     /* any non-NULL res */

static int reference_find(u32 off)
{
    if (!off) return -1;
    for (int i = 0; i < s_d3d.off_rt_cap; i++)
        if (s_d3d.off_rt[i].res && s_d3d.off_rt[i].off == off) return i;
    return -1;
}
static u32 some_off(void)
{
    static const u32 hot[] = {0x100000u, 0x200000u, 0x100040u, 0x3F00000u, 0x10u};
    return (rnd() % 3) ? hot[rnd() % 5] + 0x10000u * (rnd() % 8) : rnd();
}
static unsigned queries;
static void compare(void)
{
    for (int q = 0; q < 32 && s_d3d.off_rt_cap; ++q) {
        const u32 o = s_d3d.off_rt[rnd() % s_d3d.off_rt_cap].off;   /* live, dead and shared */
        CHECK(off_rt_find(o) == reference_find(o));
        ++queries;
    }
    for (int q = 0; q < 24; ++q) { u32 o = some_off(); CHECK(off_rt_find(o) == reference_find(o)); ++queries; }
    CHECK(off_rt_find(0) == -1);
}
static void grow(int notify)                           /* off_rt_grow: realloc, new slots zero */
{
    int cap = s_d3d.off_rt_cap ? s_d3d.off_rt_cap * 2 : 16;
    if (cap > 512) return;
    OffRT* p = (OffRT*)realloc(s_d3d.off_rt, (size_t)cap * sizeof(OffRT));
    if (!p) exit(2);
    memset(p + s_d3d.off_rt_cap, 0, (size_t)(cap - s_d3d.off_rt_cap) * sizeof(OffRT));
    s_d3d.off_rt = p;
    s_d3d.off_rt_cap = cap;
    if (notify) off_rt_changed();
}
static void reinit(int notify)                         /* backend init: memset, new pool */
{
    OffRT* old = s_d3d.off_rt;
    memset(&s_d3d, 0, sizeof s_d3d);
    compare();                                         /* no pool at all */
    s_d3d.off_rt = (OffRT*)calloc(16, sizeof(OffRT));
    free(old);                                         /* the new pool is a different array */
    s_d3d.off_rt_cap = 16;
    if (notify) off_rt_changed();
}
int main(void)
{
    s_d3d.off_rt = (OffRT*)calloc(16, sizeof(OffRT));
    s_d3d.off_rt_cap = 16;
    off_rt_changed();
    unsigned dup = 0, swapped = 0;
    int most = 0;
    for (int step = 0; step < 40000; ++step) {
        OffRT* r = &s_d3d.off_rt[rnd() % s_d3d.off_rt_cap];
        switch (rnd() % 14) {
        case 0: case 1: case 2: case 3:                /* create into a slot (empty or evicted) */
            r->res = &live; off_rt_changed();
            r->off = some_off(); off_rt_changed();
            break;
        case 4: case 5:                                /* evict / release on resize */
            r->res = NULL; off_rt_changed();
            break;
        case 6:                                        /* failed create: res stays NULL, old off kept */
            r->res = NULL; off_rt_changed();
            break;
        case 7: {                                      /* two live slots hold one offset */
            OffRT* o = &s_d3d.off_rt[rnd() % s_d3d.off_rt_cap];
            if (o->res && o != r) { r->res = &live; off_rt_changed(); r->off = o->off; off_rt_changed(); ++dup; }
            break; }
        case 8: grow(rnd() & 1); break;
        case 9: if (rnd() % 8 == 0) reinit(rnd() & 1); break;
        case 10:                                       /* the index cannot get memory for a new size */
            fail_alloc = 1 + rnd() % 2; grow(rnd() & 1); compare(); fail_alloc = 0;
            break;
        case 11: {                                     /* another array of the same capacity, unannounced */
            OffRT* p = (OffRT*)calloc((size_t)s_d3d.off_rt_cap, sizeof(OffRT));
            if (!p) exit(2);
            for (int i = 0; i < s_d3d.off_rt_cap; i++) if (rnd() & 1) p[i] = s_d3d.off_rt[i];
            free(s_d3d.off_rt); s_d3d.off_rt = p; ++swapped;
            break; }
        case 12:                                       /* the same array, a smaller capacity, unannounced */
            if (s_d3d.off_rt_cap > 16) s_d3d.off_rt_cap /= 2;
            break;
        default: break;                                /* lookups only */
        }
        if (s_d3d.off_rt_cap > most) most = s_d3d.off_rt_cap;
        compare();
    }
    if (fails) return 1;
    printf("index: %u lookups, %u shared offsets, %u refused allocations, %u array swaps, pool up to %d\n",
           queries, dup, refused, swapped, most);
    return dup > 100 && refused > 50 && swapped > 100 && most == 512 ? 0 : 1;
}
'''


def index_source(backend):
    start = backend.index('static struct {\n    const OffRT* pool;')
    end = backend.index(function(backend, 'static int off_rt_find(u32 off)\n{'))
    text = backend[start:end] + function(backend, 'static int off_rt_find(u32 off)\n{')
    return INDEX.replace('@INDEX@', text)


INDEX_MUTANTS = [
    ('index-ignores-changes', 's_off_rt_index.stale || s_off_rt_index.pool', '0 || s_off_rt_index.pool'),
    ('index-ignores-pool', 's_off_rt_index.pool != s_d3d.off_rt ||', '0 ||'),
    ('index-ignores-capacity', 's_off_rt_index.cap != s_d3d.off_rt_cap) && !off_rt_index_build',
     '0) && !off_rt_index_build'),
    ('index-highest-slot-wins', 'if (!s_off_rt_index.key[h]) { s_off_rt_index.key[h] = off;', '{ s_off_rt_index.key[h] = off;'),
    ('index-keeps-dead-slots', 'if (!s_d3d.off_rt[i].res || !off) continue;', 'if (!off) continue;'),
    ('index-never-resizes', 'if (size - 1u != s_off_rt_index.mask || !s_off_rt_index.key) {', 'if (!s_off_rt_index.key) {'),
    ('index-no-scan-on-refusal', 'if (s_d3d.off_rt[i].res && s_d3d.off_rt[i].off == off) return i;\n        return -1;',
     'if (0) return i;\n        return -1;'),
    ('index-probe-wrong-sequence', 'h = (h + 1u) & s_off_rt_index.mask)\n        if',
     'h = (h + 1u) & (s_off_rt_index.mask >> 1))\n        if'),
]

# ------------------------------------------------------------------- 3. wiring
WRITE = re.compile(r'(?:off_rt\[[^\]]*\]\.|\br->)(?:res|off)\s*=(?!=)|\(void\*\*\)&r->res')


def wiring(backend, others):
    get = function(backend, 'static int off_rt_get(u32 off, u32 w, u32 h, u32 rsx_fmt)')
    lines = get.split('\n')
    writes = 0
    for i, line in enumerate(lines):
        if WRITE.search(line):
            writes += 1
            follow = '\n'.join(lines[i:i + 3])
            if 'off_rt_changed();' not in follow:
                return f'off_rt_get writes a slot without off_rt_changed(): {line.strip()}'
    if writes < 4:
        return f'off_rt_get: expected the res/off writes, found {writes}'
    rest = backend.replace(get, '')
    for name, text in [('rsx_d3d12_backend.c', rest)] + others:
        if re.search(r'off_rt\[[^\]]*\]\.(?:res|off)\s*=(?!=)', text):
            return f'{name} writes an off_rt slot outside off_rt_get'
        for var in set(re.findall(r'OffRT\s*\*\s*(\w+)', text)):
            if re.search(r'\b' + var + r'->(?:res|off)\s*=(?!=)', text):
                return f'{name} writes {var}->res/off outside off_rt_get'
    grow = function(backend, 'static int off_rt_grow(void)')
    if 'off_rt_changed();' not in grow:
        return 'off_rt_grow does not call off_rt_changed()'
    return None


WIRING_MUTANTS = [
    ('wiring-evict-unannounced', 's_d3d.off_rt[slot].res = NULL;\n                off_rt_changed();\n',
     's_d3d.off_rt[slot].res = NULL;\n'),
    ('wiring-off-unannounced', 'r->dxgi = (u32)want_fmt;\n    off_rt_changed();\n', 'r->dxgi = (u32)want_fmt;\n'),
]


def main():
    backend = read(BACKEND)
    tables = read(TABLES)
    others = [(p.name, read(p)) for p in sorted(VIDEO.glob('*')) if p.suffix in ('.c', '.h', '.inc') and p != BACKEND]
    problem = wiring(backend, others)
    if problem:
        raise SystemExit('FAIL wiring: ' + problem)
    for name, old, new in WIRING_MUTANTS:
        if not wiring(mutate(backend, old, new), others):
            raise SystemExit(f'FAIL mutant {name} survived')
        print(f'mutant {name} caught')

    cc = compiler()
    with tempfile.TemporaryDirectory(prefix='rsx-lookups-') as td:
        d = Path(td)
        for part, source, text, mutants in [('sampler', sampler_source, tables, SAMPLER_MUTANTS),
                                            ('index', index_source, backend, INDEX_MUTANTS)]:
            exe = d / (part + ('.exe' if WINDOWS else ''))
            build(cc, source(text), exe, d)
            r = run(exe)
            if r.returncode:
                raise SystemExit(f'FAIL {part}:\n{r.stdout}{r.stderr}')
            print(r.stdout.strip())
            for name, old, new in mutants:
                m = d / (name.replace('-', '_') + ('.exe' if WINDOWS else ''))
                build(cc, source(mutate(text, old, new)), m, d)
                r = run(m)
                if r.returncode == 0:
                    raise SystemExit(f'FAIL mutant {name} survived:\n{r.stdout}')
                print(f'mutant {name} caught')
    print('PASS: sampler-table hash and render-target index answer as the original searches; wiring and mutants')


if __name__ == '__main__':
    main()
