/* Held-texture validation by guest write watch (opt-in: RSX_TEX_WATCH=1).
 *
 * With texture retention on, a texture held from an earlier batch is checked
 * against its complete XXH3 fingerprint every time a new batch binds it, so
 * unchanged textures are hashed again and again. With the watch on, the
 * source span is write-protected (runtime/memory/vm_watch.h) when it is hashed
 * or uploaded, and a later bind skips the hash while vm_clean_since() says no
 * page of the span has been written since. Any write, an announced OS write
 * or a runtime protection change makes it hash again, as before.
 *
 * Ordering: every hash re-arms the watch BEFORE it reads the bytes, and an
 * upload arms it before conversion reads them. A write before the arming is
 * in the bytes read; one after it is recorded.
 *
 * RSX_TEX_WATCH_VERIFY=1 hashes anyway whenever the watch says clean, treats a
 * changed hash as stale and reports it ([TEXWATCH] MISSED); the count must stay
 * zero. A fixture defines RSX_TEXTURE_WATCH_FAKE and provides vm_watch_enable,
 * vm_watch and vm_clean_since itself. */
#ifndef RSX_TEXTURE_WATCH_H
#define RSX_TEXTURE_WATCH_H
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include "rsx_texture_fingerprint.h"
#ifndef RSX_TEXTURE_WATCH_FAKE
#include "../../runtime/memory/vm_watch.h"
#endif

typedef struct {
    unsigned long long clean;     /* validated hits answered without a hash */
    unsigned long long hashed;    /* validated hits hashed (and re-armed) */
    unsigned long long unwatched; /* ...whose span the watch refused */
    unsigned long long uploads;   /* uploads armed */
    unsigned long long verified;  /* RSX_TEX_WATCH_VERIFY hashes of clean answers */
    unsigned long long missed;    /* ...whose hash had changed (must stay 0) */
} rsx_texture_watch_counts;
static rsx_texture_watch_counts s_tex_watch_counts;
static int s_tex_watch = -1, s_tex_watch_verify;

/* Read once. On only when RSX_TEX_WATCH=1, texture retention is on (the
 * complete fingerprint is what the watch stands in for) and the guest write
 * watch is active (PS3RECOMP_VM_WRITE_WATCH=1). */
static int texture_watch_on(int retention)
{
    if (s_tex_watch < 0) {
        const char* e = getenv("RSX_TEX_WATCH");
        const int asked = e && e[0] == '1' && !e[1];
        s_tex_watch = asked && retention && vm_watch_enable();
        e = getenv("RSX_TEX_WATCH_VERIFY");
        s_tex_watch_verify = s_tex_watch && e && e[0] == '1' && !e[1];
        if (asked)
            fprintf(stderr, "[TEXWATCH] %s\n", s_tex_watch ? (s_tex_watch_verify ? "on, verify" : "on")
                    : "off: needs RSX_TEXTURE_RETENTION=1 and PS3RECOMP_VM_WRITE_WATCH=1");
    }
    return s_tex_watch;
}

/* The guest bytes texture_source_csum reads for this texture under retention:
 * [off, off + span). 0 when the layout is unknown (then nothing is watched). */
static uint32_t texture_watch_span(uint32_t off, uint32_t w, uint32_t h, uint32_t fmt, int cube,
                                   uint32_t mips, uint32_t depth, uint32_t levels, uint32_t src_bytes)
{
    if (depth > 1u) return src_bytes;
    rsx_texture_source_view view;
    const uint64_t avail = (UINT64_C(1) << 32) - off;
    if (!rsx_texture_source_view_init(w, h, fmt, cube, mips, avail, &view) ||
        (levels > 1u && !rsx_texture_source_view_extend(&view, src_bytes, avail)) ||
        view.span_bytes > 0xFFFFFFFFu) return 0;
    return (uint32_t)view.span_bytes;
}
#endif
