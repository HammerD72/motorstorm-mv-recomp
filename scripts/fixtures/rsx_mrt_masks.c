#include "rsx_commands.h"
#include "rsx_mrt_masks.h"
#include <stdio.h>
#include <stdlib.h>
/* The metadata test must never execute a guest-memory semaphore write. */
void vm_write32(uint32_t a, uint32_t v) { (void)a; (void)v; abort(); }
uint32_t ppu_hle_inject_base;
void ps3_ms(const char* key) { (void)key; abort(); }
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL %d: %s\n",__LINE__,#x); return 1; } } while(0)
int main(void) {
 rsx_state s; rsx_state_init(&s);
 CHECK(s.color_mask_mrt==0);
 CHECK(s.blend_enable_mrt==0 && rsx_mrt_blend_enables(s.blend_enable,s.blend_enable_mrt)==0);
 s.blend_dirty=0;
 CHECK(rsx_process_method(&s,NV4097_SET_BLEND_ENABLE_MRT,0xE)==0);
 CHECK(s.blend_enable_mrt==0xE && s.blend_dirty);
 CHECK(rsx_mrt_blend_enables(0,s.blend_enable_mrt)==14);
 CHECK(rsx_mrt_blend_enables(1,s.blend_enable_mrt)==15);
 CHECK(rsx_process_method(&s,NV4097_SET_BLEND_ENABLE_MRT,0)==0);
 for(unsigned e=0;e<16;e++) {
   unsigned k=rsx_mrt_blend_key_bits(e);
   CHECK(k==((e&1)|((e&14)<<26)));
   CHECK(rsx_mrt_blend_key_enables(k|0x07FFFFFE)==e);
 }
 CHECK(rsx_mrt_blend_key_bits(2)==0x08000000);
 CHECK(rsx_mrt_blend_key_bits(8)==0x20000000);

 CHECK(rsx_mrt_color_masks(s.color_mask,s.color_mask_mrt)==15);
 s.color_mask_dirty=0;
 CHECK(rsx_process_method(&s,NV4097_SET_COLOR_MASK_MRT,0xFFF0)==0);
 CHECK(s.color_mask_mrt==0xFFF0 && s.color_mask_dirty);
 CHECK(rsx_mrt_color_masks(s.color_mask,s.color_mask_mrt)==0xFFFF);
 CHECK(rsx_process_method(&s,NV4097_SET_COLOR_MASK_MRT,0)==0);
 CHECK(rsx_mrt_color_masks(s.color_mask,s.color_mask_mrt)==15);
 const unsigned raw[]={1,2,4,8},d3d[]={8,1,2,4};
 for(unsigned r=1;r<4;r++) for(unsigned c=0;c<4;c++)
   CHECK(rsx_mrt_color_masks(0,raw[c]<<(r*4))==(d3d[c]<<(r*4)));
 CHECK(rsx_mrt_color_masks(0x01000000,0x8420)==0x4218);
 puts("PASS: actual method decode/reset, zero/full MRT masks, ARGB channel order on all secondary targets");return 0;
}
