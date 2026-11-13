/* Keyboard-only actions. Controller A and its buffered roll predicate stay on the game's path.
   Called by instruction-checked lift hooks, not executable offsets or injected machine code. */
#include "hos.h"
#include <windows.h>
#include <string.h>
#include "pc_controls.h"
static MkwVkPcControls controls;
MkwVkPcControls *hos_pc_controls(void) { return &controls; }
int hos_binding(int set,int button,int slot);
uint32_t hos_pad_raw(void);
int rx_option(int id,int otherwise);
void hos_mouse_game_menu(void);
int hos_mouse_game_pointer(int*,int*,int*,int*,uint32_t*,int*);
static int key_down(int key) { return key>0 && key<256 && (GetAsyncKeyState(key)&0x8000)!=0; }
static int pad_action(void) {
    const uint32_t buttons=hos_pad_raw();
    for(int slot=0;slot<2;++slot) {
        const int pad=hos_binding(HOS_BIND_PAD,HOS_SW_A,slot);
        if(pad>=0 && pad<HOS_PAD_N && (buttons&(1u<<pad)))return 1;
    }
    return 0;
}
uint64_t hos_pc_input(Ctx *c,uint64_t input,uint64_t held) {
    if(!input || recomp_load64(c,input)!=0x84b6dfc0ULL)return held;
    if(!rx_option(HOS_OPT_PC_CONTROLS,0)) { controls.controller=1;controls.toggle_latch=0;return held; }
    const uint64_t trigger=recomp_load64(c,input+64);
    uint64_t bits=recomp_load64(c,trigger)&~(1ULL<<63);
    if(pad_action()) {
        controls.controller=1;controls.toggle_previous=controls.toggle_latch=controls.roll_previous=0;
        if(bits&16)bits|=1ULL<<63;
    } else {
        controls.controller=0;held&=~16ULL;
        DWORD pid=0;GetWindowThreadProcessId(GetForegroundWindow(),&pid);
        if(rx_option(1,0) || pid!=GetCurrentProcessId()) {
            controls.toggle_previous=controls.toggle_latch=controls.roll_previous=0;
        } else {
            const int down=key_down(controls.toggle_key);
            if(down && !controls.toggle_previous)__atomic_fetch_xor(&controls.toggle_latch,1,__ATOMIC_SEQ_CST);
            controls.toggle_previous=down;
            if(controls.toggle_latch || key_down(controls.hold_key))held|=16;
            const int roll=key_down(controls.roll_key);
            if(roll && !controls.roll_previous)bits|=1ULL<<63;
            controls.roll_previous=roll;
        }
    }
    recomp_store64(c,trigger,bits);
    return held;
}
uint32_t hos_pc_roll(Ctx *c,uint64_t input,uint32_t original,int buffered) {
    if(!rx_option(HOS_OPT_PC_CONTROLS,0) || controls.controller)return original;
    return (original&~16u) | (!buffered && (recomp_load64(c,recomp_load64(c,input+64))>>63) ? 16u : 0u);
}
uint64_t hos_pc_game_call(void *context,uint64_t entry,const uint64_t args[5]) {
    Ctx *c=(Ctx*)context;A64Fn fn=a64_lookup(entry);
    if(!c || !fn || !args)return 0;
    const Ctx saved=*c;
    for(int i=0;i<5;++i)c->x[i]=args[i];
    c->x[30]=0;memset(c->vreg[1],0,8);
    fn(c);const uint64_t result=c->x[0];*c=saved;return result;
}
int hos_pc_mouse(int *x,int *y,int *w,int *h,uint32_t *pressed,int *scroll) {
    hos_mouse_game_menu();return hos_mouse_game_pointer(x,y,w,h,pressed,scroll);
}
