/* Authored renderer/GPU model; the production async read core and refresh are injected. */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "rsx_surface_readback.h"
typedef uint32_t u32;typedef uint64_t u64;
typedef int SRWLOCK;
typedef struct ID3D12Resource ID3D12Resource;
#define E_FAIL (-1)
#define E_INVALIDARG (-2)
static int lock_depth;
static void InitializeSRWLock(SRWLOCK *p){*p=0;}
static void AcquireSRWLockExclusive(SRWLOCK *p){if(*p){fprintf(stderr,"FAIL: recursive async lock\n");exit(1);}*p=1;lock_depth++;}
static void ReleaseSRWLockExclusive(SRWLOCK *p){if(!*p){fprintf(stderr,"FAIL: unbalanced async lock\n");exit(1);}*p=0;lock_depth--;}
static void cpu_surface_fail(const char *operation,int result){fprintf(stderr,"FAIL-STOP: %s %d\n",operation,result);exit(2);}
typedef struct {
    void *res;u32 off,w,h,dxgi;int st;
    u32 cpu_address,cpu_pitch,cpu_format;u64 gpu_generation,cpu_generation;
    ID3D12Resource *cpu_readback;
} OffRT;
static struct {OffRT off_rt[4];} s_d3d;
#define CPU_SURFACE_CAPACITY 4
static int s_cpu_surface_recording;
uint8_t *vm_base;
/* GPU model: surface contents already in guest byte order, one per slot. */
static unsigned char gpu_bytes[4][64];
static unsigned char snapshot[4][64];   /* per async entry: contents of the issued copy */
static int gpu_done;
static unsigned sync_reads,issues,issued_surfaces,waits,packs;
static void cpu_surface_request(u32 address,u32 bytes,void *opaque){
    (void)opaque;sync_reads++;
    for(int i=0;i<4;i++){OffRT *r=&s_d3d.off_rt[i];
        if(r->res && address>=r->cpu_address && address+bytes<=r->cpu_address+64)memcpy(vm_base+address,gpu_bytes[i]+(address-r->cpu_address),bytes);}
}
/* PRODUCTION_CORE */
static int s_cpu_surface_async_pending;
static int cpu_surface_async_copy_done(void){return gpu_done;}
static void cpu_surface_async_copy_wait(void){waits++;gpu_done=1;}
static void cpu_surface_async_issue(CpuSurfaceAsync **entries,OffRT **surfaces,unsigned count){
    if(s_cpu_surface_async_pending && !gpu_done){fprintf(stderr,"FAIL: reissued over an unfinished copy\n");exit(1);}
    issues++;gpu_done=0;
    for(unsigned i=0;i<count;i++){
        unsigned e=(unsigned)(entries[i]-s_cpu_surface_async);
        memcpy(snapshot[e],gpu_bytes[surfaces[i]-s_d3d.off_rt],64);issued_surfaces++;
    }
}
static void cpu_surface_async_pack(CpuSurfaceAsync *e){
    if(!gpu_done){fprintf(stderr,"FAIL: packed a copy the GPU has not finished\n");exit(1);}
    packs++;memcpy(e->back,snapshot[e-s_cpu_surface_async],64);
}
/* PRODUCTION_GLUE */
#define REQUIRE(x,message) do{if(!(x)){fprintf(stderr,"FAIL: %s\n",message);return 1;}}while(0)
static void paint(int slot,unsigned char v,u64 generation){memset(gpu_bytes[slot],v,64);gpu_bytes[slot][0]=0;s_d3d.off_rt[slot].gpu_generation=generation;}
static int read16(u32 address,unsigned char *out){
    unsigned before=sync_reads;memset(vm_base+address,0xEE,2);
    cpu_surface_request_async(address,2,NULL);out[0]=vm_base[address];out[1]=vm_base[address+1];
    return sync_reads==before;   /* 1: served asynchronously */
}
int main(int argc,char **argv){
    static uint8_t memory[0x10000];vm_base=memory;
    if(argc==2 && !strcmp(argv[1],"bad-config")){cpu_surface_async_configure("2A30480,zz");return 0;}
    if(argc==2 && !strcmp(argv[1],"too-many")){cpu_surface_async_configure("1,2,3,4,5");return 0;}
    cpu_surface_async_configure("");REQUIRE(s_cpu_surface_async_count==0,"empty list enables nothing");
    cpu_surface_async_configure(NULL);REQUIRE(s_cpu_surface_async_count==0,"unset enables nothing");
    cpu_surface_async_configure("02A30480,1F00");
    REQUIRE(s_cpu_surface_async_count==2 && s_cpu_surface_async[0].off==0x02A30480 && s_cpu_surface_async[1].off==0x1F00,"two hex offsets parsed");
    if(argc==2 && !strcmp(argv[1],"recording")){s_cpu_surface_recording=1;cpu_surface_async_refresh();return 0;}
    unsigned char v[2];
    /* Nothing tracked yet: coherent path. */
    REQUIRE(!read16(0x1008,v),"untracked surface read stays coherent");
    /* 4x2 RGBA16F target, pitch 32, guest 0x1000; generation 1 holds 0x11. */
    OffRT *r=&s_d3d.off_rt[0];r->res=r;r->off=0x02A30480;r->w=4;r->h=2;r->cpu_address=0x1000;r->cpu_pitch=32;r->cpu_format=11;
    memset(memory+0x1000,0x55,64);paint(0,0x11,1);
    cpu_surface_async_refresh();
    REQUIRE(issues==1 && issued_surfaces==1,"first batch queues a copy of the newly tracked surface");
    REQUIRE(!read16(0x1008,v) && v[0]==0x11,"read before any published copy stays coherent");
    paint(0,0x22,2);
    cpu_surface_async_refresh();
    REQUIRE(issues==1 && packs==0,"unfinished copy is neither published nor reissued");
    REQUIRE(!read16(0x1008,v) && v[0]==0x22,"still coherent until a copy is published");
    gpu_done=1;cpu_surface_async_refresh();
    REQUIRE(packs==1 && issues==2,"finished copy published, newer generation queued");
    REQUIRE(read16(0x1008,v) && v[0]==0x11 && v[1]==0x11,"read served from the last completed copy without waiting");
    REQUIRE(read16(0x1000,v) && v[0]==0 && v[1]==0x11,"first bytes of the surface served from their own offset");
    REQUIRE(read16(0x103E,v) && v[0]==0x11,"last two bytes of the surface served");
    REQUIRE(!read16(0x103F,v),"read reaching past the surface stays coherent");
    REQUIRE(!read16(0x0FFF,v),"read starting before the surface stays coherent");
    REQUIRE(!read16(0x2000,v),"read of another surface stays coherent");
    gpu_done=1;paint(0,0x33,3);cpu_surface_async_refresh();
    REQUIRE(packs==2 && issues==3,"reads since the last copy request a refresh");
    REQUIRE(read16(0x1008,v) && v[0]==0x22,"newer completed copy served");
    /* No reads after the next copy is queued: no further copies. */
    gpu_done=1;paint(0,0x44,4);cpu_surface_async_refresh();
    REQUIRE(packs==3 && issues==4,"copy queued for the read above");
    gpu_done=1;paint(0,0x55,5);cpu_surface_async_refresh();
    REQUIRE(packs==4 && issues==4,"no copy without a read since the last one");
    REQUIRE(read16(0x1008,v) && v[0]==0x44,"published copy still served");
    gpu_done=1;cpu_surface_async_refresh();
    REQUIRE(issues==5,"read after an idle batch requests a copy");
    gpu_done=1;cpu_surface_async_refresh();
    REQUIRE(packs==5,"copy published");
    REQUIRE(read16(0x1008,v) && v[0]==0x55,"latest completed generation served");
    gpu_done=1;cpu_surface_async_refresh();
    REQUIRE(issues==5,"unchanged generation is not copied again");
    /* The second listed surface is tracked independently. */
    OffRT *q=&s_d3d.off_rt[2];q->res=q;q->off=0x1F00;q->w=4;q->h=2;q->cpu_address=0x3000;q->cpu_pitch=32;q->cpu_format=11;
    paint(2,0x66,1);gpu_done=1;cpu_surface_async_refresh();
    REQUIRE(issues==6 && issued_surfaces==6,"second surface copied alone");
    gpu_done=1;cpu_surface_async_refresh();
    REQUIRE(read16(0x3010,v) && v[0]==0x66 && read16(0x1008,v) && v[0]==0x55,"each surface serves its own copy");
    /* Retirement drops the copies; a later surface at the offset starts over. */
    REQUIRE(read16(0x1008,v),"read requests a copy of the next generation");
    gpu_done=1;paint(0,0x77,6);cpu_surface_async_refresh();
    REQUIRE(issues==7 && !gpu_done,"copy of generation 6 in flight");
    unsigned waited=waits;cpu_surface_async_retire(&s_d3d.off_rt[3]);
    REQUIRE(waits==waited,"retiring an untracked surface leaves the copies alone");
    cpu_surface_async_retire(r);
    REQUIRE(waits==waited+1,"retire waits for the copy in flight");
    r->res=NULL;
    REQUIRE(!read16(0x1008,v),"retired surface no longer served");
    OffRT *n=&s_d3d.off_rt[1];n->res=n;n->off=0x02A30480;n->w=4;n->h=2;n->cpu_address=0x1000;n->cpu_pitch=32;n->cpu_format=11;
    paint(1,0x88,1);gpu_done=1;cpu_surface_async_refresh();
    REQUIRE(!read16(0x1008,v) && v[0]==0x88,"recreated surface stays coherent until its own copy is published");
    gpu_done=1;cpu_surface_async_refresh();
    REQUIRE(read16(0x1008,v) && v[0]==0x88,"recreated surface served from its own copy");
    /* The offset's surface found in another slot without a retirement: start over. */
    n->res=NULL;OffRT *m=&s_d3d.off_rt[3];*m=*n;m->res=m;paint(3,0x99,1);
    gpu_done=1;cpu_surface_async_refresh();
    REQUIRE(!read16(0x1008,v) && v[0]==0x99,"surface in a new slot stays coherent until its own copy is published");
    REQUIRE(!lock_depth,"locks balanced");
    puts("PASS: async CPU surface reads, publication, refresh demand and retirement");return 0;
}
