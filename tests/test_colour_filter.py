"""The uploaded correction matrices keep grey neutral, Off identity, and differ from simulation."""
import argparse
from pathlib import Path
import subprocess
import tempfile
RUN_ALL_ARGS = ["--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]
def main():
    p=argparse.ArgumentParser();p.add_argument('--cxx',default='clang++');a=p.parse_args()
    header = (ROOT / 'gpu/vk/vk_menu.h').read_text()
    menu = (ROOT / 'gpu/vk/vk_menu.cpp').read_text()
    frame = (ROOT / 'gpu/vk/vk_framegen.cpp').read_text()
    shader = (ROOT / 'gpu/vk/shaders/fg_scale.comp').read_text()
    api = (ROOT / 'gpu/vk/vk_api.h').read_text()
    replay = (ROOT / 'gpu/vk/vk_replay.cpp').read_text()
    assert 'int colour_filter = 0;' in header
    assert 'HOS_OPT_COLOUR_FILTER = 56' in api
    assert 'if (id == HOS_OPT_COLOUR_FILTER) return opts.colour_filter;' in replay
    # The default direct-present route must supply a sampled view for correction alone.
    assert '(opts.colour_filter && opts.hdr != 2)' in replay.split('direct_view = texture_view(', 1)[0].rsplit('if (tex && !dev.offscreen', 1)[1]
    assert '{"Off", "Protanopia", "Deuteranopia", "Tritanopia"}' in menu
    assert '{TAB_GRAPHICS, "Colour filter", &opts.colour_filter, 0, 3, 1, tx_colour_filter,' in menu
    assert 'opts.hdr == 2 ? 0 : opts.colour_filter' in frame
    assert '&& !colour) return VK_NULL_HANDLE;' in frame
    assert 'static_assert(sizeof pc == 56)' in frame
    assert 'float colour[9]; } pc;' in shader
    assert 'vec4(accessible(c), 1.0)' in shader
    assert shader.index('c = sharpen(') < shader.index('vec4(accessible(c), 1.0)')
    code=r'''
#include "gpu/vk/colour_filter.h"
#include <cassert>
#include <cmath>
int main() {
 for(int mode=-1;mode<=4;mode++) {
  float m[9]; colour_filter_matrix(mode,m);
  for(int r=0;r<3;r++) {
   for(int v=0;v<=100;v++) {
    float grey=v/100.0f;
    assert(std::fabs(grey*(m[r*3]+m[r*3+1]+m[r*3+2])-grey)<1e-6f);
   }
   if(mode < 1 || mode > 3) for(int c=0;c<3;c++) assert(m[r*3+c]==(r==c?1:0));
  }
  // Correction adds missing information to other channels; it does not remove it as simulation does.
  if(mode==1 || mode==2) assert(m[0]==1 && m[1]==0 && m[2]==0 && m[3]>0);
  if(mode==3) assert(m[8]==1 && m[2]>0 && m[5]>0);
 }
}'''
    with tempfile.TemporaryDirectory() as d:
        c=Path(d)/'colour.cpp';c.write_text(code);exe=Path(d)/'colour'
        subprocess.run([a.cxx,'-Wall','-Wextra','-Werror','-I'+str(ROOT),str(c),'-o',str(exe)],check=True)
        subprocess.run([str(exe)],check=True)
    print('PASS: correction, grey axis, Off identity')
if __name__=='__main__':main()
