/* Per-draw guest sampler tables for the VP root signature (root parameter 3).
 * Included after s_d3d/D3D12DrawRecord. Tables are immutable while any
 * recorded command can reference them: a persistent base heap caches tables
 * across frames, overflow heaps are added only when a frame needs more than
 * 128 distinct tables, and both are recycled only at the next render_frame,
 * after its full wait_for_gpu() drain. Pattern follows
 * ps3recomp's rsx_address_tables.h; filtering is decoded as well. */
#include "rsx_d3d12_guest_sampler.h"

#define GUEST_SAMPLER_TABLES 128   /* 128 tables x 16 = the 2048-sampler heap limit */
typedef struct GuestSamplerHeap {
    struct GuestSamplerHeap* next;
    ID3D12DescriptorHeap* heap;
    unsigned count;
    unsigned long long hash[GUEST_SAMPLER_TABLES];
    D3D12_SAMPLER_DESC tables[GUEST_SAMPLER_TABLES][16];
} GuestSamplerHeap;
/* The base heap persists across frames so repeated sampler states keep their
 * table and the bound heap rarely changes. Overflow heaps exist only for the
 * frame that filled the base heap. */
static GuestSamplerHeap* s_sampler_base;
static GuestSamplerHeap* s_sampler_overflow;
static ID3D12DescriptorHeap* s_sampler_bound;
static unsigned s_sampler_guest_units, s_sampler_tables_made;

static void guest_sampler_fail(const char* message)
{
    fprintf(stderr, "[RSXSAMPLER] %s\n", message); abort();
}
static GuestSamplerHeap* guest_sampler_heap_new(void)
{
    GuestSamplerHeap* h = (GuestSamplerHeap*)calloc(1, sizeof(*h));
    if (!h) guest_sampler_fail("table allocation failed");
    D3D12_DESCRIPTOR_HEAP_DESC hd = {0};
    hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER;
    hd.NumDescriptors = GUEST_SAMPLER_TABLES * 16;
    hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
    HRESULT hr = s_d3d.device->lpVtbl->CreateDescriptorHeap(s_d3d.device, &hd,
        &IID_ID3D12DescriptorHeap, (void**)&h->heap);
    if (FAILED(hr) || !h->heap) {
        fprintf(stderr, "[RSXSAMPLER] CreateDescriptorHeap 0x%08lX removed=0x%08lX tables=%u\n",
                (unsigned long)hr,
                (unsigned long)s_d3d.device->lpVtbl->GetDeviceRemovedReason(s_d3d.device),
                s_sampler_tables_made);
        guest_sampler_fail("sampler heap allocation failed");
    }
    return h;
}
static unsigned long long guest_sampler_hash(const D3D12_SAMPLER_DESC* desc)
{
    const unsigned char* b = (const unsigned char*)desc;
    unsigned long long h = 1469598103934665603ull;
    for (size_t i = 0; i < 16 * sizeof(*desc); ++i) { h ^= b[i]; h *= 1099511628211ull; }
    return h;
}

/* Record time: the draw keeps the sampler registers that were live when the
 * guest issued it, like blend_key/ds_key. */
static void guest_sampler_record(D3D12DrawRecord* dr)
{
    const rsx_state* st = s_d3d.current_rsx_state;
    for (unsigned u = 0; u < 16; ++u) {
        rsx_guest_sampler_regs* r = &dr->sampler[u];
        if (!st) { memset(r, 0, sizeof(*r)); continue; }
        r->address  = st->textures[u].address;
        r->filter   = st->textures[u].filter;
        r->control0 = st->textures[u].control0;
        r->border   = st->textures[u].border_color;
    }
}
/* Draw concatenation must not merge across a sampler change. */
static int guest_sampler_matches(const D3D12DrawRecord* dr)
{
    const rsx_state* st = s_d3d.current_rsx_state;
    if (!st) return 0;
    for (unsigned u = 0; u < 16; ++u) {
        const rsx_guest_sampler_regs* r = &dr->sampler[u];
        if (r->address != st->textures[u].address || r->filter != st->textures[u].filter ||
            r->control0 != st->textures[u].control0 || r->border != st->textures[u].border_color)
            return 0;
    }
    return 1;
}
/* Caller guarantees the GPU is idle (render_frame's leading wait_for_gpu), so
 * no recorded command still references any table. */
static void guest_sampler_retire(void)
{
    int overflowed = s_sampler_overflow != NULL;
    while (s_sampler_overflow) {
        GuestSamplerHeap* h = s_sampler_overflow;
        s_sampler_overflow = h->next;
        if (h->heap) h->heap->lpVtbl->Release(h->heap);
        free(h);
    }
    if (s_sampler_base && (overflowed || s_sampler_base->count == GUEST_SAMPLER_TABLES))
        s_sampler_base->count = 0;   /* rewrite from slot 0; nothing in flight */
    s_sampler_bound = NULL;
}
static GuestSamplerHeap* guest_sampler_find(GuestSamplerHeap* h, unsigned long long hash,
                                            const D3D12_SAMPLER_DESC* desc, unsigned* slot)
{
    for (; h; h = h->next)
        for (unsigned i = 0; i < h->count; ++i)
            if (h->hash[i] == hash && !memcmp(desc, h->tables[i], 16 * sizeof(*desc))) {
                *slot = i; return h;
            }
    return NULL;
}
/* Bind this draw's table to root parameter 3. guest=0 for the fallback
 * atlas/colour pipelines. Returns 1 when the shader-visible heaps changed,
 * which invalidates the caller's bound SRV table (root parameter 1). */
static int guest_sampler_bind(const D3D12DrawRecord* dr, int guest)
{
    D3D12_SAMPLER_DESC desc[16];
    int set[16];
    for (unsigned u = 0; u < 16; ++u) set[u] = dr->tex[u].set;
    s_sampler_guest_units += rsx_guest_sampler_table(dr->sampler, set, guest, desc);

    if (!s_sampler_base) s_sampler_base = guest_sampler_heap_new();
    unsigned long long hash = guest_sampler_hash(desc);
    unsigned slot = 0;
    GuestSamplerHeap* h = guest_sampler_find(s_sampler_base, hash, desc, &slot);
    if (!h) h = guest_sampler_find(s_sampler_overflow, hash, desc, &slot);
    UINT inc = s_d3d.device->lpVtbl->GetDescriptorHandleIncrementSize(
        s_d3d.device, D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER);
    if (!h) {
        h = s_sampler_base;
        if (h->count == GUEST_SAMPLER_TABLES) {
            h = s_sampler_overflow;
            if (!h || h->count == GUEST_SAMPLER_TABLES) {
                static unsigned logged;
                if (logged++ < 8)
                    fprintf(stderr, "[RSXSAMPLER] overflow heap: frame needs more than %u tables\n",
                            GUEST_SAMPLER_TABLES);
                h = guest_sampler_heap_new();
                h->next = s_sampler_overflow; s_sampler_overflow = h;
            }
        }
        slot = h->count++;
        h->hash[slot] = hash;
        memcpy(h->tables[slot], desc, sizeof(desc));
        D3D12_CPU_DESCRIPTOR_HANDLE cpu;
        h->heap->lpVtbl->GetCPUDescriptorHandleForHeapStart(h->heap, &cpu);
        cpu.ptr += (SIZE_T)slot * 16 * inc;
        for (unsigned u = 0; u < 16; ++u) {
            s_d3d.device->lpVtbl->CreateSampler(s_d3d.device, &desc[u], cpu);
            cpu.ptr += inc;
        }
        s_sampler_tables_made++;
    }
    int changed = s_sampler_bound != h->heap;
    if (changed) {
        ID3D12DescriptorHeap* heaps[] = {s_d3d.srv_heap, h->heap};
        s_d3d.cmd_list->lpVtbl->SetDescriptorHeaps(s_d3d.cmd_list, 2, heaps);
        s_sampler_bound = h->heap;
    }
    D3D12_GPU_DESCRIPTOR_HANDLE gpu;
    h->heap->lpVtbl->GetGPUDescriptorHandleForHeapStart(h->heap, &gpu);
    gpu.ptr += (UINT64)slot * 16 * inc;
    s_d3d.cmd_list->lpVtbl->SetGraphicsRootDescriptorTable(s_d3d.cmd_list, 3, gpu);
    return changed;
}
