"""Held-texture validation by guest write watch (RSX_TEX_WATCH): game-free checks.

1. Runtime. Compiles the vendored runtime/memory/vm_watch.c with the production
   vm_commit/vm_protect (runtime/memory/vm.h) and, on Windows, the production
   demand-commit handler from src/main.cpp, installed in the production order
   (handler first, then vm_watch_enable, so the watch runs first). Checks: a
   read keeps a span clean and a write dirties it; an announced OS write
   dirties only its pages; vm_commit releases watched pages and a read-only
   vm_protect pins them; a concurrent writer is never missed (20000 rounds);
   a raw OS read into a watched page fails without an announcement and works
   with one. Windows also checks that a demand commit of another page in a
   watched 64 KB block does not leave the watched page silently writable, and
   that a write to one watched page leaves its block neighbour clean.
   Mutants must fail: vm_commit without the release, the demand-commit handler
   without the release, and (Windows) the watch installed before the handler.
2. Consumer. Compiles the production held-texture lookup of
   vp_upload_tex_slot_impl with a controllable fake watch: an unwritten span
   is not hashed, a written one is re-armed before it is hashed, a changed one
   is stale, a refused watch hashes every time, the watch is off without
   RSX_TEX_WATCH or without retention, and verify mode turns a missed write
   into a stale texture. The span passed is the fingerprint's (a cube's six
   strided faces). Six mutants must fail.
3. Wiring. The build compiles vm_watch.c; main() enables the watch after
   alloc_guest_vm(); the demand-commit handler, vm_commit and vm_protect release
   watched pages before they change protection; the PPU write guard pins its
   page; the upload arms the span before conversion and stores the epoch with
   the fingerprint; and every OS read into guest memory announces itself.

No GPU, no game data, no performance claim. Run: python scripts/check_rsx_texture_watch.py
"""
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / 'vendor/ps3recomp'
MEMORY = VENDOR / 'runtime/memory'
VIDEO = VENDOR / 'libs/video'
WINDOWS = os.name == 'nt'


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


def build(cc, sources, exe, includes, directory):
    if WINDOWS:
        cmd = [cc, '/nologo', '/TC', '/O2', '/UNDEBUG', '/D_CRT_SECURE_NO_WARNINGS']
        cmd += ['/I' + str(i) for i in includes] + [str(s) for s in sources]
        cmd += ['/Fo' + str(directory) + os.sep, '/Fe' + str(exe)]
    else:
        cmd = [cc, '-std=gnu11', '-O2', '-UNDEBUG', '-pthread'] + ['-I' + str(i) for i in includes]
        cmd += [str(s) for s in sources] + ['-o', str(exe)]
    r = subprocess.run(cmd, cwd=directory, capture_output=True, text=True, timeout=180)
    if r.returncode:
        raise SystemExit(f'compile failed: {" ".join(cmd)}\n{r.stdout}\n{r.stderr}')


def run(exe, env_extra=None, args=()):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('RSX_', 'TEX_', 'PS3RECOMP_VM_'))}
    env.update(env_extra or {})
    return subprocess.run([str(exe), *args], capture_output=True, text=True, timeout=120, env=env)


# ---------------------------------------------------------------- 1. runtime
RUNTIME = r'''
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "vm.h"
#include "vm_watch.h"
#ifdef _WIN32
  #include <windows.h>
#else
  #include <errno.h>
  #include <fcntl.h>
  #include <pthread.h>
  #include <sched.h>
  #include <sys/mman.h>
  #include <unistd.h>
#endif
uint8_t* vm_base;
uint32_t ppu_vm_size, ppu_hle_inject_base;
static int fails;
#define CHECK(c) do { if (!(c)) { printf("FAIL line %d: %s\n", __LINE__, #c); fails++; } } while (0)
#ifdef _WIN32
@VM_SIZE@
@COMMIT_VEH@
#endif

static volatile int stop;
static const uint32_t hot = 0x00400000u;
#ifdef _WIN32
static DWORD WINAPI writer(void* a)
#else
static void* writer(void* a)
#endif
{
    (void)a; unsigned i = 0;
    while (!stop) {
        ++i; *(volatile uint32_t*)(vm_base + hot + ((i * 64u) & 0x3FFFu)) = i;
#ifdef _WIN32
        if (!(i & 1023u)) Sleep(0);
#else
        if (!(i & 1023u)) sched_yield();
#endif
    }
    return 0;
}

/* A raw OS read (no CRT buffer) of 8 KB into guest memory at ea. 1 = read. */
static int os_read(uint32_t ea, int announce)
{
    char path[512]; static char data[8192]; memset(data, 0x5A, sizeof data);
#ifdef _WIN32
    GetTempPathA(sizeof path - 32, path); strcat(path, "vmwatch-check.bin");
    HANDLE f = CreateFileA(path, GENERIC_WRITE, 0, NULL, CREATE_ALWAYS, 0, NULL);
    DWORD n = 0; WriteFile(f, data, sizeof data, &n, NULL); CloseHandle(f);
    f = CreateFileA(path, GENERIC_READ, 0, NULL, OPEN_EXISTING, FILE_FLAG_NO_BUFFERING, NULL);
    if (announce) vm_note_write(ea, sizeof data);
    BOOL ok = ReadFile(f, vm_base + ea, sizeof data, &n, NULL);
    CloseHandle(f); DeleteFileA(path);
    return ok && n == sizeof data;
#else
    snprintf(path, sizeof path, "/tmp/vmwatch-check-%d.bin", (int)getpid());
    int fd = open(path, O_CREAT | O_TRUNC | O_WRONLY, 0600);
    if (write(fd, data, sizeof data) != (ssize_t)sizeof data) return -1;
    close(fd); fd = open(path, O_RDONLY);
    if (announce) vm_note_write(ea, sizeof data);
    ssize_t n = read(fd, vm_base + ea, sizeof data);
    close(fd); unlink(path);
    return n == (ssize_t)sizeof data;
#endif
}

int main(void)
{
#ifdef _WIN32
#ifndef WATCH_FIRST
    AddVectoredExceptionHandler(1, vm_commit_veh);   /* alloc_guest_vm() */
#endif
    vm_base = (uint8_t*)VirtualAlloc(NULL, VM_SIZE, MEM_RESERVE, PAGE_READWRITE);
    if (!vm_watch_enable()) { puts("FAIL: watch did not enable"); return 2; }   /* main(), after alloc_guest_vm() */
#ifdef WATCH_FIRST
    AddVectoredExceptionHandler(1, vm_commit_veh);
#endif
#else
    vm_base = (uint8_t*)mmap(NULL, 0x100000000ull, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    if (vm_base == MAP_FAILED || !vm_watch_enable()) { puts("FAIL: no guest space or watch"); return 2; }
#endif
    const uint32_t a = 0x00100000u;
    memset(vm_base + a, 1, 0x10000);                          /* commit the block */
    uint64_t e = vm_watch(a, 0x8000);
    CHECK(e && vm_clean_since(a, 0x8000, e));
    volatile uint8_t r = vm_base[a + 4]; (void)r;
    CHECK(vm_clean_since(a, 0x8000, e));                      /* reads keep it clean */
    vm_base[a + 0x7FFF] = 2;
    CHECK(!vm_clean_since(a, 0x8000, e) && vm_base[a + 0x7FFF] == 2);
    CHECK(vm_clean_since(a, 0x1000, e));                      /* only the written page */

    e = vm_watch(a, 0x4000);
    vm_note_write(a + 0x1000, 16);                            /* an announced OS write */
    CHECK(!vm_clean_since(a, 0x4000, e) && vm_clean_since(a, 0x1000, e) && vm_clean_since(a + 0x2000, 0x2000, e));

    /* vm_commit (runtime allocation over a watched range) releases it: the
     * write after it must not go unrecorded. */
    e = vm_watch(a, 0x2000);
    CHECK(vm_commit(a, 0x2000) == 0);
    vm_base[a + 8] = 3;
    CHECK(!vm_clean_since(a, 0x2000, e));
    /* A read-only vm_protect pins: the watch refuses it until made writable. */
    CHECK(vm_protect(a + 0x3000, 0x1000, 1, 0, 0) == 0);
    CHECK(vm_watch(a + 0x3000, 0x1000) == 0);
    CHECK(vm_protect(a + 0x3000, 0x1000, 1, 1, 0) == 0);
    CHECK(vm_watch(a + 0x3000, 0x1000) != 0);

    /* OS writes do not fault: unannounced, the read fails on a watched page. */
    e = vm_watch(a + 0x8000, 0x2000);
    CHECK(os_read(a + 0x8000, 0) == 0);
    CHECK(os_read(a + 0x8000, 1) == 1 && vm_base[a + 0x8000] == 0x5A);
    CHECK(!vm_clean_since(a + 0x8000, 0x2000, e));

#ifdef _WIN32
    /* A demand commit of another page of a watched 64 KB block (vm_commit_veh
     * commits whole blocks read-write) must not leave the watched page
     * writable without a record. */
    const uint32_t b = 0x00200000u;
    memset(vm_base + b, 1, 0x10000);
    e = vm_watch(b, 0x1000);
    VirtualFree(vm_base + b + 0xF000, 0x1000, MEM_DECOMMIT);
    vm_base[b + 0xF000] = 4;                                  /* demand commit */
    vm_base[b + 0x10] = 5;
    CHECK(!vm_clean_since(b, 0x1000, e));
    /* Precision: the watch runs before the demand-commit handler, so a write
     * to one watched page leaves its block neighbour clean. */
    e = vm_watch(b, 0x2000);
    vm_base[b + 0x1000] = 6;
    CHECK(vm_clean_since(b, 0x1000, e) && !vm_clean_since(b + 0x1000, 0x1000, e));
#endif

    /* A concurrent writer is never missed: whenever the watch says clean, the
     * bytes read after arming are still the bytes. */
    memset(vm_base + hot, 0, 0x4000);
    static uint8_t snap[0x4000];
    unsigned clean = 0, dirty = 0, missed = 0;
#ifdef _WIN32
    HANDLE th = CreateThread(NULL, 0, writer, NULL, 0, NULL);
#else
    pthread_t th; pthread_create(&th, NULL, writer, NULL);
#endif
    for (int k = 0; k < 20000; k++) {
        const uint64_t h = vm_watch(hot, 0x4000);
        memcpy(snap, vm_base + hot, sizeof snap);
        for (volatile int s = 0; s < (k & 255) * 20; s++) {}
        const int changed = memcmp(snap, vm_base + hot, sizeof snap) != 0;
        if (!vm_clean_since(hot, 0x4000, h)) { dirty++; continue; }
        clean++; missed += changed;
    }
    stop = 1;
#ifdef _WIN32
    WaitForSingleObject(th, INFINITE);
#else
    pthread_join(th, NULL);
#endif
    CHECK(missed == 0 && dirty > 1000);
    vm_watch_stats_t st; vm_watch_stats(&st);
    printf("clean=%u dirty=%u missed=%u faults=%llu notes=%llu forgotten=%llu refused=%llu\n", clean, dirty, missed,
           (unsigned long long)st.faults, (unsigned long long)st.notes, (unsigned long long)st.forgotten,
           (unsigned long long)st.refused);
    puts(fails ? "FAILED" : "PASS");
    return fails ? 3 : 0;
}
'''


def runtime_part(cc, directory):
    main_cpp = (ROOT / 'src/main.cpp').read_text(encoding='utf-8')
    veh = function(main_cpp, 'static LONG WINAPI vm_commit_veh(')
    vm_size = re.search(r'#define VM_SIZE[^\n]*', main_cpp)[0]
    vm_h = (MEMORY / 'vm.h').read_text(encoding='utf-8')
    variants = {'production': (veh, vm_h, False)}
    variants['vm_commit keeps watched pages'] = (veh, vm_h.replace('    vm_watch_forget(addr, size, 1);\n', '', 1), False)
    if WINDOWS:
        variants['demand commit keeps watched pages'] = (veh.replace('vm_watch_forget_host(page, 0x10000, 1);', ''), vm_h, False)
        variants['watch installed before the demand-commit handler'] = (veh, vm_h, True)
    for name, (v, h, first) in variants.items():
        assert v != veh or h != vm_h or first or name == 'production', name
        d = directory / re.sub(r'\W+', '-', name)
        # vm.h includes ../../include/ps3emu/...: keep the vendored tree's layout.
        mem = d / 'runtime' / 'memory'
        mem.mkdir(parents=True)
        shutil.copytree(VENDOR / 'include', d / 'include')
        (mem / 'vm.h').write_text(h, encoding='utf-8')
        for f in ('vm_watch.c', 'vm_watch.h', 'vm_watch_hooks.h'):
            shutil.copy2(MEMORY / f, mem / f)
        src = RUNTIME.replace('@VM_SIZE@', vm_size).replace('@COMMIT_VEH@', v)
        if first:
            src = '#define WATCH_FIRST 1\n' + src
        (mem / 'test.c').write_text(src, encoding='utf-8')
        exe = d / ('test.exe' if WINDOWS else 'test')
        build(cc, [mem / 'test.c', mem / 'vm_watch.c'], exe, [mem, d / 'include'], d)
        r = run(exe, {'PS3RECOMP_VM_WRITE_WATCH': '1'})
        out = (r.stdout + r.stderr).strip()
        if name == 'production':
            if r.returncode:
                raise SystemExit(f'FAIL runtime:\n{out}')
            print('ok   runtime:', next(l for l in out.splitlines() if l.startswith('clean=')))
        else:
            if r.returncode == 0:
                raise SystemExit(f'FAIL mutant not exposed: {name}\n{out}')
            print(f'ok   mutant exposed: {name}')
    return len(variants) - 1


# --------------------------------------------------------------- 2. consumer
CONSUMER = r'''
#include "rsx_texture_profile.h"
#include "rsx_texture_fingerprint.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef uint32_t u32; typedef uint64_t u64; typedef uint8_t u8;
typedef struct {void*res;u32 key,w,h,fmt,off,mips,depth,levels;u64 csum,last_use,watch_epoch;int cube,used;} VPTexSlot;
static struct {unsigned vp_tex_capacity;VPTexSlot vp_tex[4];u64 vp_tex_use_serial;} s_d3d;
static u8 source_bytes[4096];
static u8* vm_base = source_bytes;
static rsx_texture_profile measured;
static unsigned hashes(void) { return (unsigned)measured.checksum_calls; }
/* Fake guest write watch: an epoch counter; a span is clean while no
 * guest_write came after its arming. */
#define RSX_TEXTURE_WATCH_FAKE
static u64 g_epoch, g_written; static unsigned g_arms, g_refuse; static u32 g_ea, g_len; static int g_race_at = -1;
static int vm_watch_enable(void) { return 1; }
/* g_race_at: a guest write lands while the span is being armed, i.e. after
 * any read that came before the arming and before the epoch it returns. */
static uint64_t vm_watch(uint32_t ea, uint32_t len) {
    g_arms++; g_ea = ea; g_len = len;
    if (g_race_at >= 0) { g_written = ++g_epoch; source_bytes[g_race_at] ^= 1; g_race_at = -1; }
    return g_refuse ? 0 : ++g_epoch;
}
static int vm_clean_since(uint32_t ea, uint32_t len, uint64_t epoch) { g_ea = ea; g_len = len; return g_written <= epoch; }
static void guest_write(u32 at, u8 v) { g_written = ++g_epoch; source_bytes[at] = v; }
#include "rsx_texture_watch.h"
@CHECKSUM@
@PROFILE_CHECKSUM@
@RETENTION@
@SOURCE_CHECKSUM@
@HINT@
static int production_lookup(u32 off,u32 w,u32 h,u32 fmt,int cube,rsx_texture_profile_call*profile){
    u32 key_off=off,depth=1,levels=1,mips=1,src_bytes=w*h*4*depth;
@LOOKUP@
    return slot>=0?slot:-100;
}
typedef struct {double t;} test_clock;
static double clock_read(void* p){test_clock*c=p;return c->t+=1;}
static test_clock fake;
static int lookup(u32 off, int cube) {
    rsx_texture_profile_call call;
    rsx_texture_profile_begin(&call,&measured,clock_read,&fake);
    int s = production_lookup(off,2,2,0x85,cube,&call);
    rsx_texture_profile_finish(&call,s);
    return s;
}
#define CHECK(x) do{if(!(x)){fprintf(stderr,"watch contract failed line %d: %s\n",__LINE__,#x);return 3;}}while(0)
static void hold(u32 off, int cube) {
    memset(&s_d3d,0,sizeof s_d3d); s_d3d.vp_tex_capacity=2;
    s_d3d.vp_tex[1]=(VPTexSlot){.res=(void*)1,.key=off,.w=2,.h=2,.fmt=0x85,.off=off,.mips=1,.cube=cube};
    s_d3d.vp_tex[1].csum=texture_source_csum(NULL,source_bytes+off,16,2,2,0x85,cube,1,1,1);
}
static u32 view_span(int cube) {
    rsx_texture_source_view v; rsx_texture_source_view_init(2,2,0x85,cube,1,UINT64_C(1)<<20,&v); return (u32)v.span_bytes;
}
int main(int argc, char** argv) {
    const char* mode = argc > 1 ? argv[1] : "watch";
    for (int i = 0; i < (int)sizeof source_bytes; i++) source_bytes[i] = (u8)(i * 7);
    if (!strcmp(mode, "off")) {               /* no RSX_TEX_WATCH, or no retention */
        hold(64,0); unsigned h0 = hashes();
        CHECK(lookup(64,0)==1); s_d3d.vp_tex[1].used=0; CHECK(lookup(64,0)==1);
        CHECK(hashes()==h0+2 && g_arms==0 && s_d3d.vp_tex[1].watch_epoch==0);
        puts("PASS off"); return 0;
    }
    if (!strcmp(mode, "verify")) {
        hold(64,0); CHECK(lookup(64,0)==1 && s_d3d.vp_tex[1].watch_epoch);
        s_d3d.vp_tex[1].used=0; unsigned h0=hashes();
        CHECK(lookup(64,0)==1 && hashes()==h0+1 && s_tex_watch_counts.verified==1 && s_tex_watch_counts.clean==1);
        source_bytes[65]^=1;                  /* a write the watch did not see */
        s_d3d.vp_tex[1].used=0; unsigned stale=(unsigned)measured.outcomes[RSX_TEX_STALE];
        CHECK(lookup(64,0)==1 && measured.outcomes[RSX_TEX_STALE]==stale+1);
        CHECK(s_tex_watch_counts.missed==1 && s_d3d.vp_tex[1].watch_epoch==0);
        puts("PASS verify"); return 0;
    }
    /* watch */
    CHECK(texture_watch_span(64,2,2,0x85,0,1,1,1,16)==view_span(0) && view_span(0)==16);
    CHECK(texture_watch_span(1024,2,2,0x85,1,1,1,1,16)==view_span(1) && view_span(1)>600);
    CHECK(texture_watch_span(64,2,2,0x85,0,1,4,1,64)==64);           /* volume: the whole base level */
    for (int cube = 0; cube < 2; cube++) {
        const u32 off = cube ? 1024 : 64;
        hold(off,cube); g_arms=0; unsigned h0=hashes();
        /* first validation: armed and hashed */
        CHECK(lookup(off,cube)==1 && hashes()==h0+1 && g_arms==1);
        CHECK(s_d3d.vp_tex[1].watch_epoch && s_d3d.vp_tex[1].used==1 && g_ea==off && g_len==view_span(cube));
        /* unwritten: no hash */
        s_d3d.vp_tex[1].used=0;
        CHECK(lookup(off,cube)==1 && hashes()==h0+1 && g_arms==1 && s_d3d.vp_tex[1].used==1 && g_len==view_span(cube));
        /* written, same bytes: re-armed before the hash, kept */
        s_d3d.vp_tex[1].used=0; guest_write(off+3, source_bytes[off+3]);
        unsigned stale=(unsigned)measured.outcomes[RSX_TEX_STALE];
        CHECK(lookup(off,cube)==1 && hashes()==h0+2 && g_arms==2);
        CHECK(measured.outcomes[RSX_TEX_STALE]==stale && s_d3d.vp_tex[1].used==1);
        s_d3d.vp_tex[1].used=0;
        CHECK(lookup(off,cube)==1 && hashes()==h0+2);                  /* clean again */
        /* written, changed: stale */
        s_d3d.vp_tex[1].used=0; guest_write(off+3, (u8)(source_bytes[off+3]^1));
        CHECK(lookup(off,cube)==1 && hashes()==h0+3 && measured.outcomes[RSX_TEX_STALE]==stale+1);
    }
    /* Arming comes before the hash: a write racing the arming is either in the
     * bytes hashed (stale now) or recorded (hashed next time), never lost. */
    for (int k = 0; k < 2; k++) {
        hold(64,0); unsigned stale=(unsigned)measured.outcomes[RSX_TEX_STALE];
        if (k) { CHECK(lookup(64,0)==1); s_d3d.vp_tex[1].used=0; guest_write(70, source_bytes[70]); }
        g_race_at = 67; unsigned h0 = hashes();
        CHECK(lookup(64,0)==1 && hashes()==h0+1 && measured.outcomes[RSX_TEX_STALE]==stale+1);
    }
    /* a refused watch hashes every time */
    hold(64,0); g_refuse=1; unsigned h0=hashes();
    CHECK(lookup(64,0)==1); s_d3d.vp_tex[1].used=0; CHECK(lookup(64,0)==1);
    CHECK(hashes()==h0+2 && s_d3d.vp_tex[1].watch_epoch==0 && s_tex_watch_counts.unwatched>=2);
    puts("PASS watch"); return 0;
}
'''


def consumer_part(cc, directory, backend):
    body = function(backend, 'static int vp_upload_tex_slot_impl(')
    start = body.index('    #define TEX_CSUM')
    end = '    rsx_texture_profile_lookup_end(profile, s_d3d.vp_tex_capacity, RSX_TEX_MISS);'
    lookup = body[start:body.index(end) + len(end)]

    def plain(signature):
        s = backend.index(signature)
        return backend[s:backend.index('\n}', s) + 2]
    hint_start = backend.index('#define VP_TEX_HINTS')
    hint_end = backend.index('\n}\n', backend.index('static int vp_tex_hint_on(void)')) + 3
    base = (CONSUMER.replace('@CHECKSUM@', plain('static u32 tex_csum('))
            .replace('@PROFILE_CHECKSUM@', plain('static u32 texture_profile_csum('))
            .replace('@RETENTION@', plain('static int texture_retention_on(void)\n{'))
            .replace('@SOURCE_CHECKSUM@', plain('static u64 texture_source_csum('))
            .replace('@HINT@', backend[hint_start:hint_end]))
    rearm = '                c->watch_epoch = wspan ? vm_watch(c->off, wspan) : 0;\n'
    hashed = '            u64 cs = TEX_CSUM(vm_base + c->off, src_bytes);\n'
    late = ('            if (texture_watch_on(retention)) c->watch_epoch = vm_watch(c->off, '
            'texture_watch_span(c->off, w, h, fmt, cube, mips, depth, levels, src_bytes));\n')
    mutants = {
        'never skips a clean texture': [('vm_clean_since(c->off, wspan, c->watch_epoch)', '0')],
        'clean answer ignores the epoch': [('c->watch_epoch && wspan && vm_clean_since(c->off, wspan, c->watch_epoch)', 'wspan')],
        'no re-arm on a hash': [(rearm, '')],
        'arms after hashing': [(rearm, ''), (hashed, hashed + late)],
        'watches the level-0 bytes only': [('texture_watch_span(c->off, w, h, fmt, cube, mips, depth, levels, src_bytes)', 'src_bytes')],
        'verify keeps a changed texture': [('same = TEX_CSUM(vm_base + c->off, src_bytes) == c->csum;', 'same = 1;')],
    }
    modes = [('watch', {'RSX_TEXTURE_RETENTION': '1', 'RSX_TEX_WATCH': '1'}),
             ('verify', {'RSX_TEXTURE_RETENTION': '1', 'RSX_TEX_WATCH': '1', 'RSX_TEX_WATCH_VERIFY': '1'}),
             ('off', {'RSX_TEXTURE_RETENTION': '1'}),
             ('off', {'RSX_TEXTURE_RETENTION': '0', 'RSX_TEX_WATCH': '1'})]

    def compile_run(name, text):
        d = directory / re.sub(r'\W+', '-', 'consumer ' + name)
        d.mkdir()
        (d / 'test.c').write_text(base.replace('@LOOKUP@', text), encoding='utf-8')
        exe = d / ('test.exe' if WINDOWS else 'test')
        build(cc, [d / 'test.c', VIDEO / 'rsx_texture_layout.c'], exe, [VIDEO, VENDOR / 'include'], d)
        return [(m, run(exe, env, (m,))) for m, env in modes]

    for m, r in compile_run('production', lookup):
        if r.returncode or 'PASS' not in r.stdout:
            raise SystemExit(f'FAIL consumer ({m}):\n{r.stdout}{r.stderr}')
    print('ok   consumer: watch, verify, off (no switch), off (no retention)')
    for name, edits in mutants.items():
        text = lookup
        for old, new in edits:
            assert text.count(old) == 1, (name, old)
            text = text.replace(old, new)
        if all(r.returncode == 0 for _, r in compile_run(name, text)):
            raise SystemExit(f'FAIL mutant not exposed: {name}')
        print(f'ok   mutant exposed: {name}')
    return len(mutants)


# ----------------------------------------------------------------- 3. wiring
ANNOUNCED = {
    'runtime/ppu/ppu_fs.cpp': ['size_t n = fread(vm_base + buf, 1, (size_t)nbytes, g_files[fd]);',
                               'n = fread(vm_base + buf, 1, (size_t)size, g_files[fd]);'],
    'runtime/syscalls/sys_fs.c': ['size_t nread = fread(buf, 1, (size_t)size, f->fp);'],
    'libs/filesystem/cellFs.c': ['bytes_read = (u64)fread(gptr(buf), 1, (size_t)nbytes, s_files[fd].host_fp);'],
    'libs/system/cellSaveData.c': ['size_t got = fread(set->fileBuf, 1, read_size, fp);'],
    'libs/system/sysPrxForUser.c': ['fread(buf, 1, (size_t)size, f);'],
    'libs/network/cellHttp.c': ['int n = recv(t->sock, (char*)buf + filled, (int)want, 0);'],
    'libs/network/sysNet.c': ['int n = recv(s_sockets[s].host_fd, GUEST_PTR(EA(buf), char*), (int)len, host_recv_flags(flags));',
                              'int n = recvfrom(s_sockets[s].host_fd, GUEST_PTR(EA(buf), char*), (int)len,'],
    'libs/network/cellSsl.c': ['read(fd, buf, size);'],
}


def wiring(backend):
    cmake = (ROOT / 'CMakeLists.txt').read_text(encoding='utf-8')
    assert '"${PS3RECOMP_DIR}/runtime/memory/vm_watch.c"' in cmake, 'vm_watch.c is not built'
    main_cpp = (ROOT / 'src/main.cpp').read_text(encoding='utf-8')
    veh = function(main_cpp, 'static LONG WINAPI vm_commit_veh(')
    assert veh.index('vm_watch_forget_host(page, 0x10000, 1);') < veh.index('VirtualAlloc(page, 0x10000, MEM_COMMIT')
    alloc = function(main_cpp, 'static bool alloc_guest_vm(void)')
    assert alloc.index('AddVectoredExceptionHandler(1, vm_commit_veh);') < alloc.index('VirtualAlloc(NULL, VM_SIZE')
    entry = function(main_cpp, 'int main(int argc, char** argv)')
    assert entry.count('vm_watch_enable();') == 1 and entry.index('alloc_guest_vm()') < entry.index('vm_watch_enable();')
    assert main_cpp.count('vm_watch_enable(') == 1
    vm_h = (MEMORY / 'vm.h').read_text(encoding='utf-8')
    commit = function(vm_h, 'static inline int32_t vm_commit(')
    assert commit.index('vm_watch_forget(addr, size, 1);') < commit.index('VirtualAlloc(vm_base + addr')
    protect = function(vm_h, 'static inline int32_t vm_protect(')
    assert protect.index('vm_watch_forget(addr, size, read && write);') < protect.index('VirtualProtect(vm_base + addr')
    loader = (VENDOR / 'runtime/ppu/ppu_loader.cpp').read_text(encoding='utf-8')
    guard = function(loader, 'extern "C" void ppu_guard_page(uint32_t guest_ea)')
    assert guard.index('vm_watch_forget_host((void*)s_guard_page, 0x1000, 0);') < guard.index('VirtualProtect((void*)s_guard_page, 0x1000, PAGE_READONLY')
    for rel, calls in ANNOUNCED.items():
        text = (VENDOR / rel).read_text(encoding='utf-8')
        lines = text.split('\n')
        for call in calls:
            at = [i for i, l in enumerate(lines) if call in l]
            assert len(at) == 1, (rel, call, len(at))
            before = '\n'.join(lines[max(0, at[0] - 3):at[0]])
            assert 'vm_note_write(' in before or 'vm_note_host_write(' in before, f'{rel}: unannounced OS write: {call}'
    # No other fread straight into guest memory.
    for path in list((VENDOR / 'runtime').rglob('*.c*')) + list((VENDOR / 'libs').rglob('*.c')):
        for i, l in enumerate(path.read_text(encoding='utf-8', errors='replace').split('\n')):
            if re.search(r'\bfread\s*\(\s*(vm_base\s*\+|gptr\s*\(|vm_to_host\s*\()', l):
                rel = path.relative_to(VENDOR).as_posix()
                assert rel in ANNOUNCED and any(c in l for c in ANNOUNCED[rel]), f'{rel}:{i + 1}: unlisted fread into guest memory'
    upload = function(backend, 'static int vp_upload_tex_slot_impl(')
    arm = upload.index('watch_armed = wspan ? vm_watch(off, wspan) : 0;')
    assert upload.index('const u32 _off0 = off;') < arm < upload.index('for (u32 _f = 0; _f < _nfaces; _f++)')
    stamp = upload.index('t->watch_epoch = watch_armed;')
    assert upload.index('t->csum = TEX_CSUM(vm_base + off, src_bytes);') < stamp
    end = backend.index('} VPTexSlot;')
    assert 'u64 watch_epoch;' in backend[backend.rindex('typedef struct {', 0, end):end]
    residency = (VIDEO / 'rsx_d3d12_texture_residency.h').read_text(encoding='utf-8')
    assert 'memset(t, 0, sizeof(*t));' in function(residency, 'static void texture_slot_release(VPTexSlot* t)')
    print(f'ok   wiring: build, handler order, releases, guard pin, upload arming, {sum(map(len, ANNOUNCED.values()))} announced OS writes')


def main():
    backend = (VIDEO / 'rsx_d3d12_backend.c').read_text(encoding='utf-8')
    wiring(backend)
    cc = compiler()
    with tempfile.TemporaryDirectory(prefix='tex-watch-') as directory:
        d = Path(directory)
        n_runtime = runtime_part(cc, d)
        n_consumer = consumer_part(cc, d, backend)
    print(f'PASS: guest write watch and held-texture validation; {n_runtime} runtime and {n_consumer} consumer mutants exposed')


if __name__ == '__main__':
    main()
