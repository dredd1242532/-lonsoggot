"""Exercise the actual keyboard action producer and all-or-nothing lift hooks without a game."""
import argparse
from pathlib import Path
import subprocess,sys,tempfile
RUN_ALL_ARGS=["--cc","{cxx}"]
ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser();parser.add_argument("--cc",default="clang");a=parser.parse_args()
    sys.path.insert(0,str(ROOT/"nx2"));import oot_pc_controls as hooks
    class Image:
        def __init__(self,bad=False):self.bad=bad
        def word(self,pc):return dict((p,w) for p,w,_ in hooks.SITES)[pc]^(self.bad and pc==hooks.SITES[1][0])
    class Lifter:
        def __init__(self):self.after={hooks.SITES[0][0]:"existing();"}
    good=Lifter();assert hooks.install(good,Image())==3 and good.after[hooks.SITES[0][0]].startswith("existing();")
    bad=Lifter();before=dict(bad.after);assert hooks.install(bad,Image(True))==0 and bad.after==before
    source=(ROOT/"host/pc_controls.c").read_text();body=source[source.index("static MkwVkPcControls controls;"):source.index("uint64_t hos_pc_game_call(")]
    fixture=r"""
#include <assert.h>
#include <stdint.h>
#include <string.h>
#include "gpu/vk/vk_api.h"
typedef unsigned long DWORD;
typedef int LONG;
typedef struct { unsigned char mem[4096]; } Ctx;
static int keys[256],enabled=1,menu=0,focused=1;static uint32_t pad;
uint64_t recomp_load64(Ctx*c,uint64_t at){uint64_t v;memcpy(&v,c->mem+at,8);return v;}
void recomp_store64(Ctx*c,uint64_t at,uint64_t v){memcpy(c->mem+at,&v,8);}
int GetAsyncKeyState(int k){return keys[k]?0x8000:0;}
void* GetForegroundWindow(void){return (void*)(uintptr_t)focused;}
unsigned GetWindowThreadProcessId(void*w,DWORD*p){*p=w?123:0;return 1;}
DWORD GetCurrentProcessId(void){return 123;}
int hos_binding(int set,int button,int slot){(void)set;(void)button;return slot?3:1;}
uint32_t hos_pad_raw(void){return pad;}
int rx_option(int id,int other){(void)other;return id==HOS_OPT_PC_CONTROLS?enabled:menu;}
"""+body+r"""
int main(void){
 Ctx c={0};const uint64_t input=256,trigger=512;
 recomp_store64(&c,input,0x84b6dfc0);recomp_store64(&c,input+64,trigger);
 controls.hold_key=16;controls.toggle_key=84;controls.roll_key=5;
 assert(!(hos_pc_input(&c,input,16)&16)); /* E still triggers A, but never holds sprint. */
 keys[16]=1;assert(hos_pc_input(&c,input,0)&16);keys[16]=0;assert(!(hos_pc_input(&c,input,0)&16));
 keys[84]=1;assert(hos_pc_input(&c,input,0)&16);assert(hos_pc_input(&c,input,0)&16);
 keys[84]=0;assert(hos_pc_input(&c,input,0)&16);keys[84]=1;assert(!(hos_pc_input(&c,input,0)&16));
 keys[84]=0;hos_pc_input(&c,input,0);keys[84]=1;hos_pc_input(&c,input,0);keys[84]=0;focused=0;
 assert(!(hos_pc_input(&c,input,0)&16));focused=1;assert(!(hos_pc_input(&c,input,0)&16));
 keys[84]=1;hos_pc_input(&c,input,0);keys[84]=0;menu=1;assert(!(hos_pc_input(&c,input,0)&16));menu=0;
 recomp_store64(&c,trigger,16);assert(!(hos_pc_roll(&c,input,16,0)&16));assert(!(hos_pc_roll(&c,input,16,1)&16));
 keys[5]=1;hos_pc_input(&c,input,0);assert(recomp_load64(&c,trigger)>>63);assert(hos_pc_roll(&c,input,0,0)&16);
 hos_pc_input(&c,input,0);assert(!(recomp_load64(&c,trigger)>>63));keys[5]=0;hos_pc_input(&c,input,0);
 for(int slot=0;slot<2;++slot){pad=1u<<(slot?3:1);recomp_store64(&c,trigger,16);assert(hos_pc_input(&c,input,16)&16);assert(hos_pc_roll(&c,input,16,1)==16);}
 pad=0;enabled=0;assert(hos_pc_input(&c,input,16)==16);assert(hos_pc_roll(&c,input,16,1)==16);
 return 0;
}
"""
    with tempfile.TemporaryDirectory() as tmp:
        p=Path(tmp)/"test.c";p.write_text(fixture);exe=Path(tmp)/"test.exe"
        subprocess.run([a.cc,"-x","c","-std=c11","-Wall","-Wextra","-Werror","-I"+str(ROOT),str(p),"-o",str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    print("PC controls: producer, roll buffer, hold/toggle, focus/menu cancellation, both controller slots and stock fallback passed")
if __name__=="__main__":main()
