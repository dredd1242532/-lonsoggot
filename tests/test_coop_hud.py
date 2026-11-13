"""S05 friend metadata, privacy, bounded codec and projection, using production functions.
No game or GPU. --mutation deliberately breaks one guard; the fixture must reject it.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ['--cc', 'gcc']

def function(text, marker):
    start=text.index(marker); at=text.index('{',start)+1; depth=1
    while depth:
        depth+=(text[at]=='{')-(text[at]=='}'); at+=1
    return text[start:at]

C=r'''
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "coop_proto.h"
#include "coop_state.h"
#include "vk_api.h"
static CoopPos sample;
static uint64_t age;
static int available=1, snapshot_calls;
int hos_oot_coop_players_snapshot(int id, CoopPos *out, uint64_t *out_age) {
    snapshot_calls++; assert(id==2); *out=sample; *out_age=age; return available;
}
int hos_oot_destination(int index,const char **child,const char **adult) {
    static const char *const c[]={"Forest_Child","Dungeon","OldCastle"};
    static const char *const a[]={"Forest_Adult","Dungeon","NewCastle"};
    if(index<0||index>=3)return 0;
    *child=c[index]; *adult=a[index]; return 1;
}
@@HELPERS@@
int main(void) {
    CoopRules rules={.epoch=1};
    assert(COOP_PRESENCE_SUPPORTED==95);
    assert(coop_feature_set(&rules,COOP_RULE_PRESENCE,2)); // adult bodies are supported
    assert(!coop_feature_set(&rules,COOP_RULE_PRESENCE,32));
    assert(!coop_feature_set(&rules,COOP_RULE_PRESENCE,128));
    assert(coop_feature_set(&rules,COOP_RULE_PRESENCE,COOP_PRESENCE_SUPPORTED));
    assert(!coop_feature_set(&rules,COOP_RULE_NAMES,2));
    sample=(CoopPos){.flags=COOP_POS_IN_WORLD,.health_q=9,.max_health_q=12,.area="Forest_Child"};
    MkwVkCoopMember m={.id=2,.accepted=1};
    friend_information(&m,&rules);
    assert(m.place==1 && m.health_q==9 && m.max_health_q==12);
    for(int mask=0;mask<=COOP_PRESENCE_SUPPORTED;mask++) {
        if(mask&~COOP_PRESENCE_SUPPORTED)continue;
        assert(coop_feature_set(&rules,COOP_RULE_PRESENCE,mask));
        m=(MkwVkCoopMember){.id=2,.accepted=1}; friend_information(&m,&rules);
        assert((m.place!=0)==!!(mask&COOP_PRESENCE_PLACE));
        assert((m.max_health_q!=0)==!!(mask&COOP_PRESENCE_HEALTH));
    }
    coop_feature_set(&rules,COOP_RULE_PRESENCE,COOP_PRESENCE_SUPPORTED);
    for(int state=0;state<6;state++) {
        m=(MkwVkCoopMember){.id=2,.accepted=1};
        age=state==0?3000:0; available=state!=1;
        if(state==2)m.accepted=0;
        if(state==3)m.self=1;
        if(state==4)m.away=1;
        sample.flags=state==5?0:COOP_POS_IN_WORLD;
        friend_information(&m,&rules); assert(!m.place && !m.max_health_q);
    }
    sample.flags=COOP_POS_IN_WORLD; available=1;
    sample.health_q=0; m=(MkwVkCoopMember){.id=2,.accepted=1}; friend_information(&m,&rules);
    assert(m.health_q==0 && m.max_health_q==12);
    sample.max_health_q=0; m=(MkwVkCoopMember){.id=2,.accepted=1}; friend_information(&m,&rules); assert(!m.max_health_q);
    assert(friend_place("Forest_Adult")==1 && friend_place("Dungeon")==2);
    assert(friend_place("NotInVocabulary")==-1 && friend_place("NewCastle")==-1);
    rules.features.count=0; m=(MkwVkCoopMember){.id=2,.accepted=1}; snapshot_calls=0;
    friend_information(&m,&rules); assert(!m.place && !m.max_health_q && !snapshot_calls);

    uint8_t b[COOP_MAX_MESSAGE]; CoopPos out;
    sample.health_q=9; sample.max_health_q=12;
    size_t n=coop_pos_pack(b,sizeof b,&sample); assert(n && coop_pos_parse(b,n,&out));
    assert(out.health_q==9 && out.max_health_q==12);
    assert(!coop_pos_parse(b,n-1,&out) && !coop_pos_parse(b,n-4,&out));
    b[0]=2; assert(!coop_pos_parse(b,n-4,&out)); b[0]=COOP_POS_VERSION;
    b[n-1]=1; assert(!coop_pos_parse(b,n,&out));
    sample.health_q=13; assert(!coop_pos_pack(b,sizeof b,&sample));
    sample.health_q=0; sample.max_health_q=161; assert(!coop_pos_pack(b,sizeof b,&sample));
    sample.max_health_q=12; n=coop_pos_pack(b,sizeof b,&sample); assert(n && coop_pos_parse(b,n,&out) && out.max_health_q==12 && !out.health_q);
    puts("PASS HUD metadata: fresh/stale/unknown/dead, each privacy bit and absent rule, unaccepted/self/away, known/neutral place, bounded POS3 and POS2 refusal");
}
'''
CPP=r'''
#include <cassert>
#include <cstdio>
#include <limits>
#include "coop_overlay_math.h"
using namespace mkwvk::coop_overlay_math;
int main() {
    const float m[16]={1,0,0,0, 0,1,0,0, 0,0,-1,-.2f, 0,0,-1,0};
    float v[4]={20,40,1280,720},crop[4]={0,0,1,1}; Marker o{};
    for(float width:{1280.f,1680.f}) {
        v[2]=width;
        float p[3]={0,0,-2};
        assert(project(m,p,v,crop,240,18,12,false,o) && !o.arrow);
        assert(o.x==20+width/2 && o.y==400);
        for(int behind=0;behind<2;behind++) for(int axis=0;axis<2;axis++) for(int sign:{-1,1}) {
            p[0]=p[1]=0; p[2]=behind?2:-2; p[axis]=sign*20.f;
            assert(project(m,p,v,crop,240,18,12,true,o) && o.arrow);
            assert(axis==0 ? o.dx*sign>0 : o.dy*sign<0);
            assert(o.x>=v[0]+12 && o.x<=v[0]+v[2]-12 && o.y>=v[1]+12 && o.y<=v[1]+v[3]-12);
            assert(o.text_x>=v[0]+12 && o.text_x+240<=v[0]+v[2]-12);
            assert(o.text_y>=v[1]+12 && o.text_y+18<=v[1]+v[3]-12);
            assert(!project(m,p,v,crop,240,18,12,false,o));
        }
        p[0]=p[1]=0; p[2]=2; assert(project(m,p,v,crop,240,18,12,true,o) && o.arrow && o.dy>0);
        p[2]=-2; p[0]=1.999f; p[1]=1.999f;
        assert(project(m,p,v,crop,240,18,12,false,o) && !o.arrow);
        assert(o.text_x+240<=v[0]+v[2]-12 && o.text_y>=v[1]+12);
        assert(!project(m,p,v,crop,width,18,12,true,o));
    }
    float p[3]={0,0,-2}; crop[0]=.25f; crop[2]=.5f;
    assert(project(m,p,v,crop,240,18,12,false,o) && o.x==v[0]+v[2]/2);
    p[0]=1.8f; assert(project(m,p,v,crop,240,18,12,true,o) && o.arrow);
    p[0]=std::numeric_limits<float>::quiet_NaN(); assert(!project(m,p,v,crop,240,18,12,true,o));
    p[0]=0; v[2]=0; assert(!project(m,p,v,crop,240,18,12,true,o));
    puts("PASS HUD projection: front/behind, four edges at 16:9 and 21:9, straight behind, text bounds, crop, nonfinite/empty refusal");
}
'''

def main():
    ap=argparse.ArgumentParser(description=__doc__); ap.add_argument('--cc',default=os.environ.get('CC','gcc'))
    ap.add_argument('--mutation',choices=['stale','health','place','accept','behind','bounds'])
    a=ap.parse_args(); menu=(ROOT/'host/coop_menu.c').read_text()
    helpers=function(menu,'static int friend_place(')+'\n'+function(menu,'static void friend_information(')
    changes={'stale':('age >= 3000','age > 3000'), 'health':('if (permissions & COOP_PRESENCE_HEALTH)','if (1)'),
             'place':('(permissions & COOP_PRESENCE_PLACE)','1'), 'accept':('!m->accepted || ','')}
    if a.mutation in changes: helpers=helpers.replace(*changes[a.mutation])
    math=(ROOT/'gpu/vk/coop_overlay_math.h').read_text()
    if a.mutation=='behind': math=math.replace('double dx=c[0]*viewport[2]/crop[2], dy=-c[1]*viewport[3]/crop[3];','double dx=c[0]/w*viewport[2]/crop[2], dy=-c[1]/w*viewport[3]/crop[3];')
    if a.mutation=='bounds': math=math.replace('text_w+2*margin>viewport[2]','false')
    with tempfile.TemporaryDirectory(prefix='longshot-hud-') as tmp:
        d=Path(tmp); (d/'check.c').write_text(C.replace('@@HELPERS@@',helpers)); (d/'check.cpp').write_text(CPP); (d/'coop_overlay_math.h').write_text(math)
        common=['-Wall','-Wextra','-Werror','-D_CRT_SECURE_NO_WARNINGS']
        subprocess.run([a.cc,'-std=gnu11',*common,'-I'+str(ROOT/'host'),'-I'+str(ROOT/'gpu/vk'),str(d/'check.c'),str(ROOT/'host/coop_state.c'),str(ROOT/'host/coop_proto.c'),str(ROOT/'third_party/monocypher/monocypher.c'),'-lm','-o',str(d/'c')],check=True)
        subprocess.run([str(d/'c')],check=True)
        cxx='clang++' if 'clang' in a.cc else 'g++'
        subprocess.run([cxx,'-std=c++17',*common,'-I'+str(d),str(d/'check.cpp'),'-o',str(d/'cpp')],check=True)
        subprocess.run([str(d/'cpp')],check=True)

if __name__=='__main__': main()
