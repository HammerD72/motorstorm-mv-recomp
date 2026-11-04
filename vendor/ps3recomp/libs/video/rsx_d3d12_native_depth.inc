/* Native D24 texture reads (#137). Included after the sampler tables.
 * Opt-in: RSX_DEPTH_TEXTURE=1. A unit the record's program reads, whose guest
 * format is D24_D8 and whose address is a live depth attachment of this batch,
 * binds that attachment through an R24_UNORM_X8 view and is compiled with a
 * _rsx_depthN helper (rsx_depth_sample.h). Everything else keeps the existing
 * path. Views are resolved after every attachment of the batch is prepared,
 * so the resources they name stay fixed until checked retirement. */
static void srv_write(u32 heap_slot, ID3D12Resource* res, DXGI_FORMAT fmt, UINT mapping);

static int native_depth_on(void)
{
    static int on = -1;
    if (on < 0) { const char* e = getenv("RSX_DEPTH_TEXTURE"); on = e && e[0] == '1' && !e[1]; }
    return on;
}

/* RSX_DEPTH_DEBUG=lit|shadow|stored|reference replaces the helper result to
 * isolate a wrong comparison input in a native run. Diagnostics only. */
static u32 native_depth_debug(void)
{
    static int mode = -1;
    if (mode < 0) {
        const char* e = getenv("RSX_DEPTH_DEBUG");
        static const char* names[] = {"", "lit", "shadow", "stored", "reference"};
        mode = 0;
        for (int i = 1; e && i < 5; ++i) if (!strcmp(e, names[i])) mode = i;
        if (mode) fprintf(stderr, "[RSXDEPTH] debug mode %s\n", names[mode]);
    }
    return (u32)mode;
}

static void native_depth_clear(D3D12DrawRecord* dr)
{
    memset(dr->depth_view, 0, sizeof(dr->depth_view));
    memset(dr->depth_sampled_slot, 0, sizeof(dr->depth_sampled_slot));
    dr->depth_sample_mask = 0;
}

/* One line per (address, format, reason), bounded; the first program seen is named. */
static void native_depth_notice(const D3D12DrawRecord* dr, unsigned u, const char* reason)
{
    static struct { u32 address, fmt; const char* reason; } seen[256];
    static unsigned n;
    for (unsigned i = 0; i < n; ++i)
        if (seen[i].address == dr->tex[u].raw && seen[i].fmt == dr->tex[u].fmt && seen[i].reason == reason) return;
    if (n == 256) return;
    seen[n].address = dr->tex[u].raw; seen[n].fmt = dr->tex[u].fmt; seen[n++].reason = reason;
    fprintf(stderr, "[RSXDEPTH] fp=%08X unit=%u address=%08X %ux%u fmt=%02X zfunc=%u %s\n",
            dr->fp_addr, u, dr->tex[u].raw, dr->tex[u].w, dr->tex[u].h, dr->tex[u].fmt,
            (dr->sampler[u].address >> 28) & 15u, reason);
}

static u32 native_depth_units(const D3D12DrawRecord* dr)
{
    extern uint8_t* vm_base;
    extern u32 cellGcmResolveLocated(int local, u32 offset);
    if (!vm_base || !dr->fp_addr) return 0;
    u32 off = cellGcmResolveLocated((dr->fp_addr & 3u) == 1, dr->fp_addr & ~3u);
    if (off == 0xFFFFFFFFu) return 0;
    const u8* uc = vp_fp_snap_bytes(dr->fp_snap, off);
    return rsx_fp_texture_units(uc ? uc : vm_base + off, 4096);
}

/* written[slot]: an earlier record of this batch renders that attachment, so
 * it is initialized before this record reads it. */
static void native_depth_prepare(D3D12DrawRecord* dr, u8 written[RSX_D3D12_DEPTH_CAPACITY + 1])
{
    native_depth_clear(dr);
    if (native_depth_on() && dr->is_vp && !dr->is_clear && dr->fp_addr) {
        u32 read = native_depth_units(dr);
        for (unsigned u = 0; u < 16; ++u) {
            if (!(read & (1u << u)) || !dr->tex[u].set) continue;
            const u32 base = dr->tex[u].fmt & 0x9Fu;
            if (base == 0x91u || base == 0x92u || base == 0x93u) {
                native_depth_notice(dr, u, "other_depth_format_skip");   /* D24 float / D16 */
                continue;
            }
            if (!rsx_depth_sample_format(dr->tex[u].fmt)) continue;
            unsigned slot = 0;
            for (unsigned i = 0; i < s_d3d.depth_cache.count; ++i)
                if (s_d3d.depth_cache.surfaces[i].resource &&
                    s_d3d.depth_cache.surfaces[i].offset == dr->tex[u].raw) { slot = i + 1; break; }
            if (!slot) { native_depth_notice(dr, u, "no_depth_attachment"); continue; }
            const rsx_d3d12_depth_surface* source = &s_d3d.depth_cache.surfaces[slot - 1];
            if (slot == dr->depth_slot) { native_depth_notice(dr, u, "feedback_skip"); continue; }
            if (!source->initialized && !written[slot]) { native_depth_notice(dr, u, "unwritten_skip"); continue; }
            const rsx_guest_sampler_regs* r = &dr->sampler[u];
            int ok = rsx_depth_sample_view_init(&dr->depth_view[u], dr->tex[u].fmt, dr->tex[u].w,
                dr->tex[u].h, dr->tex[u].mips, dr->tex[u].cube, dr->tex[u].ctrl1, r->address,
                r->filter, r->control0, source->width, source->height);
            if (ok < 1) {
                memset(&dr->depth_view[u], 0, sizeof(dr->depth_view[u]));
                native_depth_notice(dr, u, ok < 0 ? "unsupported_view_skip" : "disabled_unit_skip");
                continue;
            }
            dr->depth_view[u].debug = native_depth_debug();
            dr->depth_sampled_slot[u] = (u8)slot;
            dr->depth_sample_mask |= 1u << u;
            native_depth_notice(dr, u, dr->depth_view[u].compare ? "native_compare" : "native_depth");
        }
    }
    if (dr->is_vp && dr->depth_slot) written[dr->depth_slot] = 1;
}

static int native_depth_bind(const D3D12DrawRecord* dr, unsigned u, u32 descriptor)
{
    if (!(dr->depth_sample_mask & (1u << u))) return 0;
    srv_write(descriptor, s_d3d.depth_cache.surfaces[dr->depth_sampled_slot[u] - 1].resource,
              DXGI_FORMAT_R24_UNORM_X8_TYPELESS, D3D12_DEFAULT_SHADER_4_COMPONENT_MAPPING);
    return 1;
}

/* Attachments rest in DEPTH_WRITE. A record that reads one makes it a pixel
 * shader resource for that draw only. The record never binds it as its DSV
 * (feedback reads are skipped in prepare). */
static void native_depth_transition(const D3D12DrawRecord* dr, int begin)
{
    u8 done[RSX_D3D12_DEPTH_CAPACITY + 1] = {0};
    D3D12_RESOURCE_BARRIER barriers[16];
    UINT n = 0;
    for (unsigned u = 0; u < 16; ++u) {
        unsigned slot = dr->depth_sampled_slot[u];
        if (!(dr->depth_sample_mask & (1u << u)) || done[slot]) continue;
        done[slot] = 1;
        D3D12_RESOURCE_BARRIER* b = &barriers[n++];
        memset(b, 0, sizeof(*b));
        b->Type = D3D12_RESOURCE_BARRIER_TYPE_TRANSITION;
        b->Transition.pResource = s_d3d.depth_cache.surfaces[slot - 1].resource;
        b->Transition.Subresource = D3D12_RESOURCE_BARRIER_ALL_SUBRESOURCES;
        b->Transition.StateBefore = begin ? D3D12_RESOURCE_STATE_DEPTH_WRITE
                                          : D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE;
        b->Transition.StateAfter = begin ? D3D12_RESOURCE_STATE_PIXEL_SHADER_RESOURCE
                                         : D3D12_RESOURCE_STATE_DEPTH_WRITE;
    }
    if (n) s_d3d.cmd_list->lpVtbl->ResourceBarrier(s_d3d.cmd_list, n, barriers);
}
static void native_depth_begin(const D3D12DrawRecord* dr) { native_depth_transition(dr, 1); }
static void native_depth_end(const D3D12DrawRecord* dr) { native_depth_transition(dr, 0); }
