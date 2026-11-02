#include "rsx_mrt_observer.h"
#include <stdio.h>
#include <string.h>
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); return 1; } } while (0)
int main(void)
{
    RsxMrtObserver o = {0};
    CHECK(!o.subchannel[0].seen); /* Zero-initialized words are not observations. */
    CHECK(rsx_mrt_observe(&o, 0, 0x36c, 0, -1, 0x400));
    CHECK(o.subchannel[0].seen == 2);
    CHECK(o.subchannel[0].word[1].value == 0 && o.subchannel[0].word[1].result == -1);
    CHECK(rsx_mrt_observe(&o, 1, 0x36c, 2, 0, 0x800));
    CHECK(o.subchannel[1].word[1].value == 2 && o.subchannel[0].word[1].value == 0);
    CHECK(rsx_mrt_observe(&o, 0, 0x36c, 4, -1, 0x900));
    CHECK(o.subchannel[0].word[1].get == 0x900 && o.subchannel[0].word[1].value == 4);
    CHECK(rsx_mrt_observe(&o, 0, 0x310, 1, 0, 0x904));
    CHECK(rsx_mrt_observe(&o, 0, 0x324, 0x1010101, 0, 0x908));
    CHECK(rsx_mrt_observe(&o, 0, 0x370, 0xfff0, -1, 0x90c));
    CHECK(o.subchannel[0].seen == 15 && o.subchannel[1].seen == 2);
    RsxMrtObserver saved = o;
    CHECK(!rsx_mrt_observe(&o, 8, 0x36c, 0, 0, 0));
    CHECK(!rsx_mrt_observe(&o, 0, 0x314, 0, 0, 0));
    CHECK(!memcmp(&o, &saved, sizeof o));
    puts("PASS: explicit zero, rejected writes, subchannel isolation, last-write identity and unseen state");
    return 0;
}
