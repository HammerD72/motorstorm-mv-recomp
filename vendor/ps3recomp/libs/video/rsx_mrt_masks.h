/* NV4097 primary byte lanes and secondary ARGB nibbles -> D3D RGBA nibbles. */
#ifndef PS3RECOMP_RSX_MRT_MASKS_H
#define PS3RECOMP_RSX_MRT_MASKS_H
#include <stdint.h>
static uint32_t rsx_mrt_color_masks(uint32_t primary, uint32_t mrt)
{
    uint32_t out = ((primary & 0x00010000) ? 1u : 0u)
                 | ((primary & 0x00000100) ? 2u : 0u)
                 | ((primary & 0x00000001) ? 4u : 0u)
                 | ((primary & 0x01000000) ? 8u : 0u);
    for (unsigned r=1;r<4;r++) {
        uint32_t argb=(mrt>>(r*4))&15u;
        out |= (((argb&14u)>>1)|((argb&1u)<<3)) << (r*4);
    }
    return out;
}
#endif
