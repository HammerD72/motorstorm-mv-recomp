/* Game-free fixture for prompt flip completion in the vendored cellGcmSys.c.
 * argv[1] = the mode the process was started in: off | prompt | take | both
 * (GCM_FLIP_PROMPT / GCM_FLIP_AT_TAKE are read once, so each mode is its
 * own process). Prints PASS or "FAIL <check>" and exits nonzero on failure. */
#include GCM_SOURCE
#include <pthread.h>
#include <sys/mman.h>
#include <time.h>

uint8_t* vm_base;
uint32_t ppu_vm_size;
int g_resv_store_active;
uint32_t g_ww_lo, g_ww_hi;
volatile int g_spu_coh_armed;
void ppu_resv_break_store(uint64_t ea) { (void)ea; }
void ps3_ww_report_inline(uint32_t a, uint64_t v, int w) { (void)a; (void)v; (void)w; }
int spu_coh_is_reserved(uint32_t a) { (void)a; return 0; }
void spu_coh_notify_write(uint32_t a) { (void)a; }
void spu_lockline_lock(void) {}
void spu_lockline_unlock(void) {}
uint32_t sysmem_spu_alias_fixup(uint32_t ea) { return ea; }
volatile unsigned char vm_read_sync_pages[1u << 20];
void vm_read_sync_dispatch(uint32_t a, uint32_t n) { (void)a; (void)n; }
void rsx_fps_stats_on_flip(void) {}
u32 g_rsx_last_reference;
void rsx_d3d12_composite_from_offscreen(u32 raw_off) { (void)raw_off; }
int rsx_draw_engine_enabled(void) { return 0; }
void rsx_draw_engine_method(u32 method, u32 arg) { (void)method; (void)arg; }
int rsx_live_draw_enabled(void) { return 0; }
void rsx_live_draw_method(u32 method, u32 arg) { (void)method; (void)arg; }
void rsx_live_draw_note_resolve(u32 a, u32 b, u32 c, u32 d) { (void)a; (void)b; (void)c; (void)d; }
int rsx_live_draw_blit(u32 a, u32 b, u32 c, u32 d, u32 e, u32 f, u32 w, u32 h)
{ (void)a; (void)b; (void)c; (void)d; (void)e; (void)f; (void)w; (void)h; return 0; }
int rsx_live_draw_resolve_blit(u32 a, u32 b, u32 c, u32 d, u32 e, u32 f, u32 g, u32 h,
                               u32 x, u32 y, u32 w, u32 hh)
{ (void)a; (void)b; (void)c; (void)d; (void)e; (void)f; (void)g; (void)h; (void)x; (void)y; (void)w; (void)hh; return 0; }
void rsx_state_init(rsx_state* state) { (void)state; }
int rsx_process_method(rsx_state* state, u32 method, u32 data) { (void)state; (void)method; (void)data; return 0; }
void rsx_raise_user_cmd(u32 arg) { (void)arg; }
ps3_guest_caller_fn g_ps3_guest_caller;
PPU_THREAD_LOCAL ppu_context* g_active_ctx;
void ppu_dump_guest_stack(ppu_context* c, const char* t) { (void)c; (void)t; }
void ppu_dump_bctrl_ring(uint32_t id, const char* t) { (void)id; (void)t; }

#define CHECK(x, name) do { if (!(x)) { printf("FAIL %s\n", name); return 1; } } while (0)
static unsigned flip_calls;
static pthread_t flip_thread;
static void callback(uint32_t opd, uint64_t a, uint64_t b, uint64_t c,
                     uint64_t d, uint64_t e, uint64_t f, uint64_t h, uint64_t i)
{
    (void)a; (void)b; (void)c; (void)d; (void)e; (void)f; (void)h; (void)i;
    if (opd == 0x500) { flip_calls++; flip_thread = pthread_self(); }
}
static double ms_now(void)
{
    struct timespec t; clock_gettime(CLOCK_MONOTONIC, &t);
    return t.tv_sec * 1e3 + t.tv_nsec / 1e6;
}
/* A flip the drain reached and the frame clock took for present (which, with
 * GCM_FLIP_AT_TAKE, may complete it). */
static void drained_and_taken(void)
{
    gcm_flip_drained();
    s_flip_pending = 1;
    (void)cellGcm_take_flip_pending_synced();
}
static int pending_flip(void) { return (GCM_PENDING_TAKE() & 2) != 0; }

int main(int argc, char** argv)
{
    if (argc < 2) return 2;
    const int prompt = !strcmp(argv[1], "prompt") || !strcmp(argv[1], "both");
    const int take = !strcmp(argv[1], "take") || !strcmp(argv[1], "both");
    vm_base = mmap(NULL, 1ull << 32, PROT_READ | PROT_WRITE, MAP_PRIVATE | MAP_ANONYMOUS | MAP_NORESERVE, -1, 0);
    CHECK(vm_base != MAP_FAILED, "vm reservation");
    g_ps3_guest_caller = callback;
    s_flip_handler_opd = 0x500;

    /* A: a beat publishes the completion; nobody else pumps. */
    cellGcmResetFlipStatus();
    cellGcmTickFlip();
    double t0 = ms_now();
    cellGcmSetWaitFlip();
    double waited = ms_now() - t0;
    if (prompt) {
        CHECK(flip_calls == 1 && pthread_equal(flip_thread, pthread_self()), "waiter delivers the flip itself");
        CHECK(waited < 20.0, "prompt waiter returns at once");
    } else {
        CHECK(flip_calls == 0, "off: waiter leaves delivery to HLE boundaries");
        CHECK(waited >= 50.0, "off: unchanged bounded wait");
    }
    (void)GCM_PENDING_TAKE();
    s_flip_status = CELL_GCM_FLIP_STATUS_DONE;

    /* B: completion when the frame clock takes the frame (GCM_FLIP_AT_TAKE).
     * s_flip_last_complete_ns = 0 means the last completion was long ago. */
    /* B1: the taken frame carries the current wait; a period has passed. */
    s_flip_last_complete_ns = 0;
    cellGcmResetFlipStatus(); drained_and_taken();
    CHECK(pending_flip() == take, "taking the frame completes the current flip");
    s_flip_status = CELL_GCM_FLIP_STATUS_DONE;

    /* B2: completed less than one vblank period ago: the beat stays in charge. */
    s_flip_last_complete_ns = get_timestamp_ns();
    cellGcmResetFlipStatus(); drained_and_taken();
    CHECK(!pending_flip(), "vblank cap kept");

    /* B3: the guest opened a newer wait before the older frame was taken. */
    s_flip_last_complete_ns = 0;
    cellGcmResetFlipStatus(); gcm_flip_drained();
    cellGcmResetFlipStatus();
    s_flip_pending = 1; (void)cellGcm_take_flip_pending_synced();
    CHECK(!pending_flip(), "older frame never completes a newer wait");
    /* ...and once that newer flip is drained and taken, it does. */
    drained_and_taken();
    CHECK(pending_flip() == take, "newer frame completes its own wait");

    /* B4: nothing is waiting. */
    s_flip_status = CELL_GCM_FLIP_STATUS_DONE;
    s_flip_last_complete_ns = 0;
    drained_and_taken();
    CHECK(!pending_flip(), "no completion without a waiting flip");

    /* B5: a direct (non-FIFO) flip request is its own drained wait. */
    s_display_buffer_set[0] = 1;
    s_flip_handler_opd = 0;
    CHECK(gcm_flip_request(0, 0) == CELL_OK, "direct flip accepted");
    CHECK(cellGcm_take_flip_pending_synced() == 1, "direct flip taken for present");
    CHECK(pending_flip() == take, "direct flip completes when taken");

    /* B6: a FIFO flip request waits for its marker. */
    s_flip_status = CELL_GCM_FLIP_STATUS_DONE;
    s_flip_last_complete_ns = 0;
    CHECK(gcm_flip_request(0, 1) == CELL_OK, "FIFO flip accepted");
    s_flip_pending = 1; (void)cellGcm_take_flip_pending_synced();   /* an earlier frame */
    CHECK(!pending_flip(), "FIFO flip not complete before its marker");
    drained_and_taken();
    CHECK(pending_flip() == take, "FIFO flip completes once its marker is taken");

    /* D: catch-up beats arrive while a direct flip waits to be taken (a flip
     * requested during a slow present). With GCM_FLIP_AT_TAKE they leave it
     * to the take, so the title cannot start its next frame before this one
     * is taken; off, the beat completes it as before. */
    s_flip_status = CELL_GCM_FLIP_STATUS_DONE;
    (void)GCM_PENDING_TAKE();
    s_flip_last_complete_ns = 0;
    CHECK(gcm_flip_request(0, 0) == CELL_OK, "direct flip accepted (D)");
    cellGcmTickFlip(); cellGcmTickFlip(); cellGcmTickFlip();
    CHECK(pending_flip() == !take, "beats leave an untaken flip to the take");
    if (take) {
        s_flip_beat_deferred_ns = get_timestamp_ns() - 1500000000ull;   /* first deferred 1.5 s ago */
        CHECK(cellGcm_take_flip_pending_synced() == 1 && pending_flip(), "take completes the flip the beats left");
        s_flip_status = CELL_GCM_FLIP_STATUS_DONE;
        s_flip_last_complete_ns = 0;
        CHECK(gcm_flip_request(0, 0) == CELL_OK, "direct flip accepted (D2)");
        cellGcmTickFlip();
        CHECK(!pending_flip(), "next flip's deferral starts at its own first beat");
        s_flip_beat_deferred_ns = get_timestamp_ns() - 2000000000ull;
        cellGcmTickFlip();
        CHECK(pending_flip(), "beat completes a flip left untaken past the fallback");
    }
    s_flip_pending = 0;
    (void)GCM_PENDING_TAKE();

    /* C: the real walker over an authored ring -- frame 1's flip with frame 2
     * already queued behind it. With either switch, frame 1 is presented on
     * its own; off, the old get == put gate is kept. */
    s_flip_pending = 0; s_flip_hold = 0;
    s_gcm_syscall_mode = 1;
    s_ea_address_table[0] = 0x100;                 /* IO page 0 -> EA 0x10000000 */
    const u32 ring[] = { 0, GCM_FLIP_MARKER, 0, 0, GCM_FLIP_MARKER, 0 };
    for (u32 i = 0; i < 6; i++) vm_write32(0x10000000u + 4 * i, ring[i]);
    s_fifo_getoff = 0;
    vm_write32(GCM_CONTROL_GUEST_ADDR + 0, 16);     /* the title is inside frame 2 */
    cellGcm_rsx_process_fifo();
    CHECK(s_fifo_getoff == 8 && s_flip_pending, "walker stops at frame 1's flip");
    if (prompt || take) {
        cellGcm_rsx_process_fifo();
        CHECK(s_fifo_getoff == 8, "drain holds at an untaken flip");
        CHECK(cellGcm_take_flip_pending_synced() == 1, "frame 1 presents with frame 2 queued");
        cellGcm_rsx_process_fifo();
        CHECK(s_fifo_getoff == 16 && !s_flip_pending, "drain resumes into frame 2 only after the take");
    } else {
        CHECK(cellGcm_take_flip_pending_synced() == 0, "off: present still waits for get == put");
        cellGcm_rsx_process_fifo();
        CHECK(s_fifo_getoff == 16, "off: drain does not hold");
    }
    /* A stale hold (flip not accepted) never lets a later flip skip the gate. */
    s_flip_hold = 1; s_flip_pending = 0;
    cellGcm_rsx_process_fifo();
    CHECK(!s_flip_hold, "stale hold cleared");
    printf("PASS mode=%s\n", argv[1]);
    return 0;
}
