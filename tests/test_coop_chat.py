"""S06 chat: production UTF-8/feed/runtime, signed progress observations, and the menu's real text/focus/input entry.
No game, GPU or network. --mutations compiles deliberately broken copies; every one must fail behaviorally.
"""
import argparse
from pathlib import Path
import re
import subprocess
import tempfile

RUN_ALL_ARGS = ["--cc", "{cc}", "--cxx", "{cxx}"]
ROOT = Path(__file__).resolve().parents[1]


def block(text, marker):
    start = text.index(marker)
    end = text.index("{", start) + 1
    depth = 1
    while depth:
        depth += (text[end] == "{") - (text[end] == "}")
        end += 1
    return text[start:end] + (";" if text[end:end + 1] == ";" else "")


def between(text, first, last):
    start = text.index(first)
    return text[start:text.index(last, start)]


MENU = r'''#define _CRT_SECURE_NO_WARNINGS
#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include "gpu/vk/vk_api.h"
#include "host/coop_chat_text.h"
enum { VK_BACK=8, VK_RETURN=13, VK_ESCAPE=27, VK_END=35, VK_HOME=36, VK_LEFT=37, VK_RIGHT=39, VK_DELETE=46 };
static int64_t now_ns=100000000;
static int64_t prof_now() { return now_ns; }
static int key_held;
static int GetAsyncKeyState(int k) { return k == key_held ? 0x8000 : 0; }
static int text_focus, text_calls, sent, permitted=1;
static MkwVkCoopFeed live;
static int take_text(int focus, uint32_t*, int) { text_focus=focus; text_calls++; return 0; }
static int feed(MkwVkCoopFeed* out) { *out=live; return permitted; }
static int command(int c, const char* text, uint32_t) { assert(c==HOS_COOP_CHAT_SEND); if (!permitted) return 0; assert(!strcmp(text,"wasd")); sent++; return 1; }
static MkwVkHost storage{};
static MkwVkHost* host=&storage;
static std::atomic<bool> open_now{false};
static void* pending;
@@FIELD@@
@@OWNER@@
@@FUNCTIONS@@
static int chat_option;
static unsigned long long hos_oc_note_bits;
static int rx_option(int id,int) { assert(id==HOS_OPT_CHAT_FOCUS); return chat_option; }
static void scripted_input(uint64_t* b, int32_t s[4]) { *b=0xffff; s[0]=32767; }
static int hos_control_input(uint64_t* b, int32_t s[4]) { *b|=1; s[1]=32767; return 0; }
@@INPUT@@
int main() {
    storage.take_text=take_text; storage.coop_feed=feed; storage.coop_chat=command;
    live.visible=live.can_chat=1; live.generation=4;
    chat_open(); assert(editing==&chat_field && menu_chat_focus() && menu_is_open() && text_focus==HOS_TEXT_ON);
    for (char c : "wasd") if (c) text_edit(chat_field,(uint32_t)c);
    chat_option=menu_chat_focus(); uint64_t buttons=99; int32_t sticks[4]={1,2,3,4}; hos_oc_note_bits=7;
    chat_input_entry(&buttons,sticks); assert(!buttons && !sticks[0] && !sticks[1] && !sticks[2] && !sticks[3] && !hos_oc_note_bits);
    assert(text_edit(chat_field,HOS_TEXT_KEY|VK_RETURN)==kTextDone); edit_end(true);
    assert(sent==1 && !editing && menu_chat_focus() && text_focus==HOS_TEXT_OFF && !chat_field.len);
    key_held='W'; now_ns+=100000000; chat_focus_tick(true,0); assert(menu_chat_focus());
    key_held=VK_RETURN; chat_focus_tick(true,0); assert(menu_chat_focus());
    key_held=0; chat_focus_tick(true,1); assert(menu_chat_focus());
    chat_focus_tick(true,0); assert(!menu_chat_focus() && !menu_is_open());
    chat_option=0; chat_input_entry(&buttons,sticks); assert(buttons && sticks[0] && sticks[1]);
    chat_open(); text_edit(chat_field,'x'); assert(text_edit(chat_field,HOS_TEXT_KEY|VK_ESCAPE)==kTextCancel); edit_end(false);
    assert(sent==1 && !editing && !chat_field.len);
    now_ns+=100000000; chat_focus_tick(true,0);
    chat_open(); live.generation++; chat_focus_tick(true,0); assert(!editing && menu_chat_focus());
    now_ns+=100000000; chat_focus_tick(true,0);
    chat_open(); chat_focus_tick(false,0); assert(!editing); /* focus loss cancels, not sends */
    now_ns+=100000000; chat_focus_tick(true,0); permitted=0; live.can_chat=0; chat_open(); assert(!editing);
    TextField utf{{},0,0,192,nullptr,true};
    text_edit(utf,0x00e9); text_edit(utf,0x4e2d); text_edit(utf,0x1f600); assert(utf.len==9);
    text_edit(utf,HOS_TEXT_KEY|VK_LEFT); assert(utf.caret==5);
    text_edit(utf,HOS_TEXT_KEY|VK_BACK); assert(utf.len==6 && utf.caret==2);
    text_edit(utf,HOS_TEXT_KEY|VK_DELETE); assert(utf.len==2 && utf.caret==2);
    text_edit(utf,0x202e); text_edit(utf,'\n'); assert(utf.len==2);
    for(int i=0;i<100;i++) text_edit(utf,0x4e2d); assert(utf.len==191);
    text_edit(utf,0x00e9); assert(utf.len==191); text_edit(utf,'a'); assert(utf.len==192);
    puts("PASS chat-input: real editor/focus ownership, UTF-8 caret/deletion, Enter/Esc, held-key/pad fence, focus loss, generation cancellation, typing cannot move Link, off input restored");
}
'''


def menu_source(root):
    menu = (root / "gpu/vk/vk_menu.cpp").read_text()
    hid = (root / "host/nn_hid.c").read_text()
    functions = [block(menu, marker) for marker in ("void edit_start(", "void edit_end(", "const char* chat_send(", "void chat_open(", "bool chat_focus_tick(", "bool menu_chat_focus(", "bool menu_is_open(")]
    fields = '\n'.join([block(menu, "struct TextField {"), between(menu, "enum { kTextGoOn", "\n"), block(menu,"int text_edit("),block(menu,"void text_set(")])
    owner = between(menu,"MkwVkCoopFeed chat_feed{};","void edit_start(")
    entry = between(hid,"static void read_input(","    oc_notes();")
    entry = entry.replace("read_input", "chat_input_entry") + "}\n"
    # Actual routing, not a second test-only widget or an input guard after an early return.
    assert hid.index("if (rx_option(HOS_OPT_CHAT_FOCUS, 0))") < hid.index("scripted_input(buttons, stick);",hid.index("static void read_input("))
    tick = block(menu,"void menu_tick() {")
    assert tick.index("if (editing && (open_now.load() || editing == &chat_field))") < tick.index("} else if (open_now.load() && page == 5)")
    assert '!menu_chat_focus() && opts.menu_shortcut' in tick
    assert 'if (connected && rx_option(HOS_OPT_CHAT_FOCUS, 0))' in hid
    assert 'id == HOS_OPT_CHAT_FOCUS' in (root/"gpu/vk/vk_replay.cpp").read_text()
    assert 'coop_send_event(0, COOP_K_CHAT' in (root/"host/coop_chat_runtime.c").read_text()
    assert 'hos_log' not in (root/"host/coop_chat_runtime.c").read_text()
    assert "{HOS_KEY_CHAT, \"Chat\"}" in menu and "{'T'}" in hid
    return MENU.replace("@@FIELD@@", fields).replace("@@OWNER@@",owner).replace("@@FUNCTIONS@@",'\n'.join(functions)).replace("@@INPUT@@",entry)


def run(command, expect_fail=False):
    result = subprocess.run(command, capture_output=True, text=True, timeout=120)
    if expect_fail:
        if result.returncode == 0:
            raise AssertionError("broken copy survived: " + ' '.join(command))
    else:
        if result.returncode:
            raise RuntimeError(' '.join(command)+'\n'+result.stdout+result.stderr)
        print(result.stdout, end='')


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cc', default='gcc'); p.add_argument('--cxx',default='g++')
    p.add_argument('--source-root',type=Path,default=ROOT); p.add_argument('--mutations',action='store_true')
    p.add_argument('--part', choices=['all','core','progress','menu','mutations'], default='all')
    a=p.parse_args(); root=a.source_root.resolve(); host=root/'host'
    with tempfile.TemporaryDirectory(prefix='longshot-chat-') as td:
        tmp=Path(td)
        flags=['-D_GNU_SOURCE','-D_CRT_SECURE_NO_WARNINGS','-Wall','-Wextra','-Werror','-O1','-I'+str(host),'-I'+str(root)]
        mono=root/'third_party/monocypher/monocypher.c'
        core=[a.cc,'-std=c11',*flags,str(ROOT/'tests/coop_chat_test.c'),str(host/'coop_chat_runtime.c'),str(host/'coop_state.c'),str(host/'coop_proto.c'),str(mono)]
        if a.part in ('all','core'):
            run(core+['-o',str(tmp/'core')]); run([str(tmp/'core')])
        progress=[a.cc,'-std=c11',*flags,str(ROOT/'tests/coop_chat_progress_test.c'),str(host/'coop_proto.c'),str(mono)]
        if a.part in ('all','progress'):
            run(progress+['-o',str(tmp/'progress')]); run([str(tmp/'progress')])
        source=menu_source(root); (tmp/'menu.cpp').write_text(source)
        menu_cmd=[a.cxx,'-std=c++20',*flags,str(tmp/'menu.cpp'),'-o',str(tmp/'menu')]
        if a.part in ('all','menu'):
            run(menu_cmd); run([str(tmp/'menu')])
        if a.mutations or a.part == 'mutations':
            # The harness changes production definitions in an isolated include directory, not assertions.
            text=(host/'coop_chat.c').read_text()
            mutations={
                'mute':('if (p->muted || now < p->next_ms)', 'if (now < p->next_ms)'),
                'replay':('if (!sequence || sequence <= p->received) return 0;', 'if (!sequence) return 0;'),
                'rate':('if (p->muted || now < p->next_ms)', 'if (p->muted)'),
                'acceptance':('if (!s->enabled) return 0;', 'if (0) return 0;'),
                'history':('if (s->count < COOP_CHAT_HISTORY) s->count++;','s->count++;'),
                'scene':('if (!safe) return 1;', 'if (0) return 1;'),
            }
            for name,(before,after) in mutations.items():
                assert text.count(before)==1, name
                # Fixture includes coop_chat.c; keep its production relative includes valid.
                mutant=tmp/'coop_chat.c'
                mutant.write_text(text.replace(before,after).replace('../gpu/vk/oot_cheat_items.h',str(root/'gpu/vk/oot_cheat_items.h')))
                run(core[:2]+['-I'+str(tmp)]+core[2:]+['-o',str(tmp/'broken')])
                run([str(tmp/'broken')],expect_fail=True)
                print('CAUGHT mutation:',name)
            for name,before,after in (
                ('focus','chat_capture.store(true, std::memory_order_release);','chat_capture.store(false, std::memory_order_release);'),
                ('input','if (rx_option(HOS_OPT_CHAT_FOCUS, 0)) { hos_oc_note_bits = 0; return; }','(void)rx_option(HOS_OPT_CHAT_FOCUS, 0);'),
                ('release','if (!held && prof_now() >= chat_release_at)','if ((void)held, prof_now() >= chat_release_at)'),
            ):
                assert source.count(before)==1,name
                (tmp/'menu.cpp').write_text(source.replace(before,after))
                run(menu_cmd); run([str(tmp/'menu')],expect_fail=True)
                print('CAUGHT mutation:',name)
    print('PASS: S06 chat production fixtures')


if __name__=='__main__':
    main()
