/* Per-draw guest sampler tables for the VP root signature (root parameter 3).
 * Included after s_d3d/D3D12DrawRecord. Tables are immutable once written;
 * heaps grow by another 128-table heap and are released only at the next
 * render_frame, after its full wait_for_gpu() drain. Pattern follows
 * ps3recomp's rsx_address_tables.h; filtering is decoded as well. */
#include "rsx_d3d12_guest_sampler.h"

typedef struct GuestSamplerHeap {
    struct GuestSamplerHeap* next;
    ID3D12DescriptorHeap* heap;
    unsigned count;
    D3D12_SAMPLER_DESC tables[128][16];
} GuestSamplerHeap;
static GuestSamplerHeap* s_sampler_heaps;
static ID3D12DescriptorHeap* s_sampler_bound;
static unsigned s_sampler_guest_units, s_sampler_tables_made;

static void guest_sampler_fail(const char* message)
{
    fprintf(stderr, "[RSXSAMPLER] %s\n", message); abort();
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
/* Caller guarantees the GPU is idle (render_frame's leading wait_for_gpu). */
static void guest_sampler_retire(void)
{
    while (s_sampler_heaps) {
        GuestSamplerHeap* h = s_sampler_heaps;
        s_sampler_heaps = h->next;
        if (h->heap) h->heap->lpVtbl->Release(h->heap);
        free(h);
    }
    s_sampler_bound = NULL;
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

    GuestSamplerHeap* h = s_sampler_heaps;
    unsigned slot = 0;
    for (; h; h = h->next) {
        for (slot = 0; slot < h->count; ++slot)
            if (!memcmp(desc, h->tables[slot], sizeof(desc))) break;
        if (slot < h->count) break;
    }
    UINT inc = s_d3d.device->lpVtbl->GetDescriptorHandleIncrementSize(
        s_d3d.device, D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER);
    if (!h) {
        h = s_sampler_heaps;
        if (!h || h->count == 128) {
            h = (GuestSamplerHeap*)calloc(1, sizeof(*h));
            if (!h) guest_sampler_fail("table allocation failed");
            D3D12_DESCRIPTOR_HEAP_DESC hd = {0};
            hd.Type = D3D12_DESCRIPTOR_HEAP_TYPE_SAMPLER;
            hd.NumDescriptors = 2048;   /* D3D12 shader-visible sampler heap limit */
            hd.Flags = D3D12_DESCRIPTOR_HEAP_FLAG_SHADER_VISIBLE;
            HRESULT hr = s_d3d.device->lpVtbl->CreateDescriptorHeap(s_d3d.device, &hd,
                &IID_ID3D12DescriptorHeap, (void**)&h->heap);
            if (FAILED(hr) || !h->heap) guest_sampler_fail("sampler heap allocation failed");
            h->next = s_sampler_heaps; s_sampler_heaps = h;
        }
        slot = h->count++;
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
