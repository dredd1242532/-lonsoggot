"""Behavioral combat observer checks on synthetic memory, without a game or GPU.

Executes production native hooks, codec and admission, plus the actual lift-patch output. Tests pre-clamp
overkill/protection, native setup/renewal/off, retries, bounded worker bookkeeping and reliable observations.
--patch-source uses the same behavioral fixture with a previous lift patch; missing hooks must fail at runtime.
--mutation removes a named guard only in the disposable compiled fixture.
"""
import argparse
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

RUN_ALL_ARGS = ["--cc", "gcc"]
ROOT = Path(__file__).resolve().parents[1]

A64 = r'''#ifndef A64_H
#define A64_H
#include <stdint.h>
typedef struct { uint64_t x[32]; unsigned char vreg[32][16]; int z,n,carry,v; } Ctx;
#define A64_NOESC
#endif
'''

FIXTURE = r'''
#include <limits.h>
#include <math.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "coop.h"
#include "oot_coop_hits.h"
static int failed;
#define CHECK(x) do { if (!(x)) { fprintf(stderr,"FAIL line %d: %s\n",__LINE__,#x); failed++; } } while(0)
typedef int SRWLOCK;
#define SRWLOCK_INIT 0
static int locked;
static void AcquireSRWLockExclusive(SRWLOCK *p) { CHECK(!*p); *p=1; locked++; }
static int TryAcquireSRWLockExclusive(SRWLOCK *p) { if(*p)return 0; AcquireSRWLockExclusive(p);return 1; }
static void ReleaseSRWLockExclusive(SRWLOCK *p) { CHECK(*p);*p=0;locked--; }
static unsigned char memory[0x20000];
#define BASE UINT64_C(0x100000)
#define LIFE (BASE+0x1000)
#define OWNER (BASE+0x9000)
#define VALUE (BASE+0xa000)
#define CELL (VALUE+8)
static void *hos_guest_range(uint64_t a,uint64_t n) { return a>=BASE && a-BASE<=sizeof memory && n<=sizeof memory-(a-BASE) ? memory+(size_t)(a-BASE):NULL; }
static int hos_mem_region(uint64_t a,uint64_t n) { return hos_guest_range(a,n)!=NULL; }
#define GP(a) hos_guest_range((a),1)
#define A64_PTR(a) GP(a)
static unsigned marks;
#define A64_MARK(a,n) do { (void)(a);(void)(n);marks++; } while(0)
static uint64_t recomp_load64(Ctx *c,uint64_t a) { (void)c;uint64_t x=0;void*p=hos_guest_range(a,8);CHECK(p);if(p)memcpy(&x,p,8);return x; }
static uint64_t recomp_load32(Ctx *c,uint64_t a) { (void)c;uint32_t x=0;void*p=hos_guest_range(a,4);CHECK(p);if(p)memcpy(&x,p,4);return x; }
static void store64(uint64_t a,uint64_t v) {memcpy(hos_guest_range(a,8),&v,8);}
static void store32(uint64_t a,uint32_t v) {memcpy(hos_guest_range(a,4),&v,4);}
static int trace_on, protect, playing=1, classify_player=1;
static uint32_t accepted=1u<<2;
static uint64_t now=1000;
static char hos_main_build_id[48]="e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348";
static int env_on(const char *name) {return !strcmp(name,"HOS_COOP_HITS_TRACE") && trace_on;}
uint64_t coop_now_ms(void) {return now;}
int coop_member_accepted(int id) {return id>=1 && id<=COOP_MAX_MEMBERS && !!(accepted&(1u<<id));}
int hos_oot_hearts_enabled(void) {return protect;}
int hos_oot_is_player(Ctx*c,uint64_t a) {(void)c;return classify_player && a==LIFE;}
static unsigned local_logs,remote_logs,attack_logs,drop_logs;
static void hos_log(const char *fmt,...) {
    CHECK(!locked);
    if(strstr(fmt,"native %s"))local_logs++;
    if(strstr(fmt,"member %d observed"))remote_logs++;
    if(strstr(fmt,"coop: attack:"))attack_logs++;
    if(strstr(fmt,"trace incomplete"))drop_logs++;
}
static CoopView view;
static CoopEventFn message_fn;
static CoopViewFn view_fn;
static unsigned registrations,sends;
static uint8_t sent[256][OOT_HIT_BYTES];
static int sent_to[256];
int coop_on_event(uint8_t kind,CoopEventFn fn,void*user) {(void)user;CHECK(kind==COOP_K_HIT);message_fn=fn;registrations++;return 1;}
int coop_on_view(CoopViewFn fn,void*user) {(void)user;view_fn=fn;registrations++;return 1;}
void coop_view_get(CoopView *v) {*v=view;}
int coop_send_event(int id,uint8_t kind,const void *p,size_t n) {CHECK(!locked && kind==COOP_K_HIT && n==OOT_HIT_BYTES && sends<256);if(sends<256){sent_to[sends]=id;memcpy(sent[sends++],p,n);}return COOP_EVENT_QUEUED;}
#include "hits_body.c"
#include "patched_life.c"
#include "patched_attacks.c"
static unsigned before_calls,commit_calls;
static uint64_t first_token;
static int veto,race;
static uint32_t policy_before(const OotCoopLifeChange *ch,void*user) {
    CHECK(user==(void*)1 && ch->owner==OWNER && ch->component==LIFE && ch->cell==CELL);
    before_calls++;
    if(!first_token)first_token=ch->invocation;else CHECK(first_token==ch->invocation);
    if(race){race=0;__atomic_store_n((uint32_t*)GP(CELL),ch->before-1,__ATOMIC_SEQ_CST);}
    return veto ? ch->before : ch->requested;
}
static void policy_commit(const OotCoopLifeChange *ch,void*user) {CHECK(user==(void*)1 && ch->invocation==first_token);commit_calls++;}
static const OotCoopLifePolicy policy={policy_before,policy_commit,(void*)1};
static void frame(void) {hos_oot_hits_frame("SyntheticArena",playing);}
static void deliver(int from,const uint8_t *p,size_t n) {message_fn(from,H.sessions[from],999,p,n,NULL);}
static CoopRules rules;
static void explicit_rule(int value) {
    rules=(CoopRules){.epoch=1};rules.features.count=1;rules.features.bytes[0]=OOT_RULE_HIT_EVENTS;
    hit_put16(rules.features.bytes+1,(uint16_t)value);
}
static void fresh(void) {
    CHECK(!locked);
    memset(&H,0,sizeof H);memset(memory,0,sizeof memory);
    hit_policy=NULL;hit_invocation=hit_busy_drops=0;hos_oot_hits_active=hos_oot_attack_observe=0;
    trace_on=protect=veto=race=0;playing=classify_player=1;accepted=1u<<2;now=1000;
    local_logs=remote_logs=attack_logs=drop_logs=registrations=sends=before_calls=commit_calls=marks=0;first_token=0;message_fn=NULL;view_fn=NULL;
    snprintf(hos_main_build_id,sizeof hos_main_build_id,"%s",HITS_BUILD);
    view=(CoopView){.state=COOP_JOINED,.version=1,.self=1,.session=101,.n=2};
    snprintf(view.code,sizeof view.code,"synthetic-room");
    view.member[0]=(CoopPeer){.id=1,.session=101};view.member[1]=(CoopPeer){.id=2,.session=202};
    store64(LIFE+HIT_LIFE_OWNER,OWNER);store64(LIFE+HIT_LIFE_VALUE,VALUE);store32(OWNER+HIT_ACTOR_NUMBER,42);
    explicit_rule(1);
}
static Ctx context(unsigned path) {
    Ctx c={0};c.x[0]=path==0;c.x[19]=LIFE;c.x[13]=CELL;c.x[9]=path?(uint32_t)-2:2;c.x[10]=10;
    store32(CELL,10);return c;
}
static void joined(void) {hos_oot_hits_start();hos_oot_hits_room(&rules,1);frame();}
static void wire(void) {
    OotCoopHit h={.phase=OOT_HIT_OBSERVATION,.target_kind=OOT_HIT_PLAYER,.target_member=2,.origin_session=202,.event_id=7,
        .area=17,.epoch=1,.target_id=202,.before=12,.after=10,.amount_q8=512,.tick=77},out={0};
    uint8_t p[OOT_HIT_BYTES+1],bad[sizeof p];
    CHECK(oot_coop_hit_pack(p,sizeof p,&h)==112 && p[0]==1 && hit_get64(p+16)==7);
    CHECK(oot_coop_hit_parse(p,112,&out) && out.before==12 && out.after==10 && out.event_id==7);
    for(size_t i=0;i<112;i++)CHECK(!oot_coop_hit_parse(p,i,&out));
    CHECK(!oot_coop_hit_parse(p,113,&out) && !oot_coop_hit_pack(p,111,&h));
    CHECK(!oot_coop_hit_parse(NULL,112,&out) && !oot_coop_hit_parse(p,112,NULL));
    const unsigned changed[]={0,1,2,4,6,96,98,100,104,108};
    for(unsigned i=0;i<sizeof changed/sizeof *changed;i++){memcpy(bad,p,112);bad[changed[i]]^=4;CHECK(!oot_coop_hit_parse(bad,112,&out));}
    memcpy(bad,p,112);hit_put32(bad+72,0x7fc00000);CHECK(!oot_coop_hit_parse(bad,112,&out));
    memcpy(bad,p,112);hit_put64(bad+16,0);CHECK(!oot_coop_hit_parse(bad,112,&out));
    h.target_id=203;CHECK(!oot_coop_hit_pack(p,sizeof p,&h));h.target_id=202;
    h.amount_q8=1;CHECK(!oot_coop_hit_pack(p,sizeof p,&h));h.amount_q8=512;
    h.after=h.before;CHECK(!oot_coop_hit_pack(p,sizeof p,&h));h.after=10;
    h.target_kind=OOT_HIT_LOCAL;h.target_id=(uint64_t)UINT32_MAX+2;CHECK(!oot_coop_hit_pack(p,sizeof p,&h));
    OotCoopHitSeen seen={0};
    CHECK(!oot_coop_hit_once(&seen,0));CHECK(oot_coop_hit_once(&seen,5));CHECK(oot_coop_hit_once(&seen,7));
    CHECK(oot_coop_hit_once(&seen,6));CHECK(!oot_coop_hit_once(&seen,6));CHECK(oot_coop_hit_once(&seen,200));
    CHECK(!oot_coop_hit_once(&seen,100));CHECK(oot_coop_hit_once(&seen,UINT64_MAX));
    CHECK(oot_coop_hit_once(&seen,UINT64_MAX-1));CHECK(!oot_coop_hit_once(&seen,UINT64_MAX));
}
static void native(void) {
    for(unsigned path=0;path<2;path++) {
        fresh();trace_on=1;joined();CHECK(registrations==2 && hos_oot_hits_active);
        Ctx c=context(path);f_804bd8c0(&c);
        CHECK(recomp_load32(&c,CELL)==8 && !local_logs && H.native_count==1);
        CHECK(H.native[0].delta==-2 && H.native[0].requested==8);
        frame();CHECK(local_logs==1 && sends==1 && sent_to[0]==2);
        OotCoopHit h={0};CHECK(oot_coop_hit_parse(sent[0],112,&h));
        CHECK(h.before==10 && h.after==8 && h.target_id==101 && (h.event_id&1)==path);
        now+=80;frame();now+=170;frame();CHECK(sends==1); /* EVENT owns retries, not this layer */
    }
    fresh();trace_on=1;joined();Ctx c=context(0);c.x[9]=10;c.x[10]=3;store32(CELL,3);f_804bd8c0(&c);
    CHECK(H.native_count==1 && H.native[0].delta==-10 && H.native[0].before==3 && H.native[0].after==0);
    frame();CHECK(sends==1);
    fresh();trace_on=1;joined();protect=1;c=context(0);f_804bd8c0(&c);
    CHECK(H.native_count==1 && H.native[0].delta==-2 && H.native[0].requested==8 && H.native[0].after==10);
    frame();CHECK(recomp_load32(&c,CELL)==10 && !sends && local_logs==1);
    fresh();trace_on=1;joined();CHECK(hos_oot_hits_bind(&policy));hos_oot_hits_policy_on(1);race=1;c=context(0);f_804bd8c0(&c);
    CHECK(before_calls==2 && commit_calls==1 && hit_invocation==1 && H.native_count==1 && recomp_load32(&c,CELL)==7);
    frame();CHECK(sends==1);OotCoopHit h={0};CHECK(oot_coop_hit_parse(sent[0],112,&h) && h.before==9 && h.after==7);
    fresh();joined();CHECK(hos_oot_hits_bind(&policy));hos_oot_hits_policy_on(1);veto=1;c=context(0);f_804bd8c0(&c);
    CHECK(before_calls==1 && commit_calls==1 && recomp_load32(&c,CELL)==10);frame();CHECK(!sends);
    fresh();trace_on=1;joined();c=context(0);store64(LIFE+HIT_LIFE_OWNER,0);f_804bd8c0(&c);frame();CHECK(!sends && !local_logs);
    fresh();trace_on=1;joined();c=context(0);c.x[11]=9;c.x[12]=8;
    hos_oot_hits_committed(&c,1,0,8);CHECK(!H.native_count && !sends); /* failed compare/exchange */
    hit_invocation=UINT64_MAX>>1;CHECK(!hos_oot_hits_begin() && hit_invocation==UINT64_MAX>>1);
}
static void permissions(void) {
    fresh();hos_oot_hits_start();rules=(CoopRules){.epoch=1};
    hos_oot_hits_room(&rules,1);CHECK(!H.share && !hos_oot_hits_active);Ctx c=context(0);f_804bd8c0(&c);frame();
    CHECK(!sends && !hit_invocation && recomp_load32(&c,CELL)==8);
    trace_on=1;H.trace=1;hos_oot_hits_room(&rules,1);c=context(0);f_804bd8c0(&c);frame();CHECK(!sends && local_logs==1);
    explicit_rule(1);hos_oot_hits_room(&rules,0);CHECK(!H.share);c=context(0);f_804bd8c0(&c);frame();CHECK(!sends);
    hos_oot_hits_room(&rules,1);frame();accepted=0;c=context(0);f_804bd8c0(&c);frame();CHECK(!sends);
    accepted=1u<<2;playing=0;frame();c=context(0);f_804bd8c0(&c);frame();CHECK(!sends);
    playing=1;frame();c=context(0);f_804bd8c0(&c);now+=1001;frame();CHECK(!sends && H.dropped);
    fresh();snprintf(hos_main_build_id,sizeof hos_main_build_id,"unsupported");trace_on=1;hos_oot_hits_start();
    CHECK(!registrations && !hos_oot_hits_active && !hos_oot_attack_observe);
}
static void receive(void) {
    fresh();joined();Ctx c=context(0);f_804bd8c0(&c);frame();CHECK(sends==1);
    OotCoopHit h={0};CHECK(oot_coop_hit_parse(sent[0],112,&h));h.origin_session=h.target_id=202;h.target_member=2;
    uint8_t p[112]={0};CHECK(oot_coop_hit_pack(p,sizeof p,&h)==112);
    deliver(2,p,112);deliver(2,p,112);CHECK(remote_logs==1 && H.received==1);
    now+=100000;view.version++;view.member[1].away=1;view_fn(&view,NULL);deliver(2,p,112);CHECK(remote_logs==1);
    view.version++;view.n=1;view_fn(&view,NULL);deliver(2,p,112);CHECK(remote_logs==1);
    view.version++;view.n=2;view.member[1].away=0;view_fn(&view,NULL);deliver(2,p,112);CHECK(remote_logs==1);
    accepted=0;h.event_id++;oot_coop_hit_pack(p,sizeof p,&h);deliver(2,p,112);CHECK(remote_logs==1);
    accepted=1u<<2;deliver(2,p,112);CHECK(remote_logs==2);
    view.version++;view.member[1].session=303;view_fn(&view,NULL);deliver(2,p,112);CHECK(remote_logs==2);
    h.origin_session=h.target_id=303;oot_coop_hit_pack(p,sizeof p,&h);deliver(2,p,112);CHECK(remote_logs==3);
    rules=(CoopRules){.epoch=1};hos_oot_hits_room(&rules,1);h.event_id++;oot_coop_hit_pack(p,sizeof p,&h);deliver(2,p,112);CHECK(remote_logs==3);
    CHECK(recomp_load32(&c,CELL)==8); /* reception never writes native life */
}
static void capacity(void) {
    fresh();trace_on=1;joined();
    for(unsigned i=0;i<HIT_PENDING+1;i++){Ctx c=context(0);f_804bd8c0(&c);}
    unsigned pending=0;for(unsigned i=0;i<HIT_PENDING;i++)pending+=H.pending[i].recipients!=0;
    CHECK(pending==HIT_PENDING && H.dropped==1 && H.native_count==HIT_PENDING && H.native_dropped==1);
    for(unsigned i=0;i<12;i++)frame();
    CHECK(sends==HIT_RATE/(OOT_HIT_BYTES+54) && drop_logs==1);
    fresh();joined();Ctx c=context(0);f_804bd8c0(&c);
    view.version++;view.member[1].session=303;view_fn(&view,NULL);frame();CHECK(!sends);
    fresh();trace_on=1;joined();c=context(0);
    AcquireSRWLockExclusive(&H.lock);f_804bd8c0(&c);ReleaseSRWLockExclusive(&H.lock);
    CHECK(hit_busy_drops==1 && !H.native_count && !sends);frame();CHECK(drop_logs==1);
}

static void attacks(void) {
    const uint64_t sensor=BASE+0xb000,components=BASE+0xc000,record=BASE+0xd000;
    fresh();trace_on=1;joined();
    store64(sensor+24,OWNER);store64(OWNER+624,components);store64(components+952,sensor);
    store64(sensor+128,record);store32(record+320,7);store32(record+324,0x40000000);
    Ctx c={0};c.x[19]=sensor;c.x[25]=record;
    unsigned char before[sizeof memory];memcpy(before,memory,sizeof memory);
    f_82ec53d0(&c);
    CHECK(H.native_count==1 && H.native[0].kind==NATIVE_SETUP && H.native[0].primary==101 &&
          H.native[0].secondary==102 && H.native[0].owner==42 && H.native[0].power==7 &&
          H.native[0].multiplier==2.0f && H.native[0].path==0);
    CHECK(recomp_load32(&c,record+400)==101 && recomp_load32(&c,record+408)==102);
    store32(BASE+0xd000+400,0);store32(BASE+0xd000+408,0);
    CHECK(!memcmp(before,memory,sizeof memory)); /* only the synthetic guest, not observer, wrote IDs */
    c.x[0]=record+48;f_82ec6680(&c);
    CHECK(H.native_count==2 && H.native[1].kind==NATIVE_RENEW && H.native[1].secondary==104);
    c.x[19]=sensor;c.x[21]=5;f_808f56f0(&c);
    CHECK(H.native_count==3 && H.native[2].kind==NATIVE_OFF && H.native[2].layer==5);
    frame();CHECK(attack_logs==3 && !sends);
    store64(components+952,0);c.x[19]=sensor;c.x[25]=record;f_82ec53d0(&c);CHECK(!H.native_count);
    store64(components+952,sensor);c.x[25]=record+16;f_82ec53d0(&c);CHECK(!H.native_count);
    c.x[25]=record;store32(record+324,0x7fc00000);f_82ec53d0(&c);CHECK(!H.native_count);
    H.trace=0;hit_active_locked();CHECK(!hos_oot_attack_observe);
    c.x[0]=record+48;f_82ec6680(&c);CHECK(!H.native_count);
    puts("PASS: native setup/renew/off, power inputs, ownership, finite values and off-path");
}
/* A ported caller has decoded the old D target/damage line. It does not construct OotCoopHit, use kind 8,
   or start the observation layer. Class 0 here explicitly means its negotiated legacy/unspecified class. */
static int decoded_d(OotCoopHitGate *g,const OotCoopHitContext *v,const OotCoopHitReceipt *r,
                     int from,int target,int quarters,uint64_t received,unsigned *requests) {
    if(target!=v->self || quarters<=0 ||
       !oot_coop_hit_admit(g,v,r,from,0,(uint32_t)quarters,received))return 0;
    (*requests)++; /* only a request handed to local gameplay; this fixture does not subtract native hearts */
    return 1;
}
static void caller_claims(void) {
    CoopRules signed_rules={.epoch=1}; /* a synthetic PvP permission, not diagnostic rule 30 */
    signed_rules.features.count=1;signed_rules.features.bytes[0]=80;hit_put16(signed_rules.features.bytes+1,1);
    uint64_t sessions[COOP_MAX_MEMBERS+1]={0};sessions[1]=101;sessions[2]=202;sessions[3]=303;
    OotCoopHitContext v={
        .rules=&signed_rules,.sessions=sessions,.accepted_members=1u<<2,.allowed_classes=(1u<<0)|(1u<<3),
        .max_damage=8,.max_bytes=32,.now_ms=1000,.peer_opt_in_session=202,.max_age_ms=250,
        .per_second=32,.burst=32,.self=1,.rule_id=80,.room_accepted=1,.playing=1,.local_opt_in=1,.peer_opt_in=1
    };
    OotCoopHitReceipt r={202,1,6}; /* session callback metadata for decoded \"D 1 2\\n\" */
    OotCoopHitGate g={0};
    unsigned requests=0;
    CHECK(decoded_d(&g,&v,&r,2,1,2,1000,&requests) && requests==1);
    CHECK(!decoded_d(&g,&v,&r,2,1,2,1000,&requests) && requests==1);
    r.event_id++;v.in_event=1;
    CHECK(!decoded_d(&g,&v,&r,2,1,2,1000,&requests));
    v.in_event=0;v.safe_after_ms=2500;r.event_id++;
    CHECK(!decoded_d(&g,&v,&r,2,1,2,1000,&requests));
    v.now_ms=2600;
    CHECK(!decoded_d(&g,&v,&r,2,1,2,2600,&requests)); /* rejection cannot wake up after the event */
    r.event_id++;CHECK(decoded_d(&g,&v,&r,2,1,2,2600,&requests) && requests==2);
    r.event_id++;CHECK(oot_coop_hit_admit(&g,&v,&r,2,3,5,2600)); /* another class, no observation codec */

    v.now_ms=3000;v.safe_after_ms=0;r.event_id=100;
#define DENY(setup) do { \
    OotCoopHitGate bg={0};OotCoopHitContext bv=v;OotCoopHitReceipt br=r;CoopRules rr=signed_rules;bv.rules=&rr; \
    int from=2;unsigned cls=0;uint32_t damage=2;uint64_t at=bv.now_ms; \
    setup;CHECK(!oot_coop_hit_admit(&bg,&bv,&br,from,cls,damage,at)); \
} while(0)
    DENY(from=0);DENY(from=9);DENY(from=1);DENY(bv.accepted_members=0);
    DENY(br.session=303);DENY(br.session=0);DENY(br.event_id=0);
    DENY(rr.features.count=0);DENY(rr.epoch=0);DENY(rr.features.bytes[1]=0);DENY(rr.features.bytes[1]=2);
    DENY(bv.room_accepted=0);DENY(bv.playing=0);DENY(bv.local_opt_in=0);DENY(bv.peer_opt_in=0);
    DENY(bv.peer_opt_in_session=303);DENY(bv.local_opt_in=2);DENY(bv.in_event=1);
    DENY(bv.safe_after_ms=bv.now_ms+1);DENY(at=bv.now_ms+1);DENY(at=bv.now_ms-251);
    DENY(cls=2);DENY(cls=32);DENY(damage=0);DENY(damage=9);DENY(br.bytes=0);DENY(br.bytes=33);
    DENY(bv.per_second=0);DENY(bv.per_second=33);DENY(bv.burst=0);DENY(bv.burst=33);
    DENY(bv.max_bytes=COOP_MAX_MESSAGE+1);DENY(bv.max_age_ms=0);DENY(bv.max_age_ms=10001);DENY(bv.sessions=NULL);
#undef DENY
    memset(&g,0,sizeof g);r.event_id=1;v.per_second=2;v.burst=2;
    CHECK(oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms-250)); /* inclusive age bound */
    r.event_id++;CHECK(oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms));
    r.event_id++;CHECK(!oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms));
    v.now_ms+=500;CHECK(!oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms)); /* rate-denied event stays denied */
    r.event_id++;CHECK(oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms));
    v.now_ms--;r.event_id++;CHECK(!oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms)); /* local clock must not rewind */
    v.now_ms=UINT64_MAX;r.event_id++;CHECK(oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms)); /* refill cannot overflow */
    sessions[2]=404;v.peer_opt_in_session=404;r.session=404;r.event_id=1;
    CHECK(oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms)); /* current new session has its own replay/rate state */
    r.session=202;r.event_id=UINT64_MAX;CHECK(!oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms));
    r.session=404;r.event_id=2;CHECK(oot_coop_hit_admit(&g,&v,&r,2,0,2,v.now_ms)); /* old session did not poison it */
    CHECK(requests==2);
    puts("PASS: decoded D-style caller uses consent, session opt-in, event recovery, class/damage/size/age/rate and replay admission");
}
int main(void) {caller_claims();wire();native();permissions();receive();capacity();attacks();CHECK(!locked);if(failed)return 1;puts("PASS: committed/pre-clamp life, protection, consent, EVENT, replay and bounded observations");return 0;}
'''


def load_patch(path):
    sys.path.insert(0, str(ROOT / "nx2"))
    spec = importlib.util.spec_from_file_location("hits_patch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC") or shutil.which("gcc") or "gcc")
    parser.add_argument("--patch-source", type=Path, default=ROOT / "nx2/oot_patches.py")
    parser.add_argument("--mutation", choices=("cas", "pre-clamp", "attack", "owner", "build", "off"))
    args = parser.parse_args()
    patch = load_patch(args.patch_source)
    source = (ROOT / "host/oot_coop_hits.c").read_text(encoding="utf-8")
    marker = "/* ---- wire and application-level replay protection"
    assert source.count(marker) == 1
    mutations = {
        "cas": ("if ((uint32_t)c->x[11] != (uint32_t)c->x[10] ||", "if ("),
        "pre-clamp": ("path ? (int64_t)(int32_t)c->x[9] : -(int64_t)(int32_t)c->x[9]",
                      "(int64_t)(uint32_t)c->x[12] - before"),
        "attack": ("hit_trace_locked(&sample);", "(void)sample;"),
        "owner": ("recomp_load64(c, components + 952) != sensor", "0"),
        "build": ('!strcmp(hos_main_build_id, HITS_BUILD)', "1"),
        "off": ("H.known && H.trace,", "H.known,"),
    }
    if args.mutation:
        old, new = mutations[args.mutation]
        if old not in source:
            raise SystemExit("mutation anchor missing")
        source = source.replace(old, new)
    # This is a synthetic function, not lifted game code. The exact CAS is the repository's existing patch anchor.
    native = '#include "a64.h"\n' + patch.LIFE_HEAD
    for path in range(2):
        if not path:
            native += '    if (c->x[0]) {\n'
        native += (f'    retry{path}:;\n    c->x[11]=c->x[10];\n'
                   f'    int64_t wanted{path}=(int64_t)c->x[10] + '
                   f'{"(int32_t)c->x[9]" if path else "-(int64_t)(int32_t)c->x[9]"};\n'
                   f'    c->x[12]=(uint32_t)(wanted{path}<0?0:wanted{path}>100?100:wanted{path});\n' + patch.LIFE_CAS +
                   f'\n    if (c->x[11]!=c->x[10]) {{ c->x[10]=c->x[11]; goto retry{path}; }}\n    return;\n')
        if not path:
            native += '    }\n'
    native += '}\n'
    patched = patch.patch_life(native)
    assert patched is not None
    # These tiny stand-ins are authored fixtures, not lifted bodies. Only the patch's function/label ABI is used.
    attacks = ('#include "a64.h"\n'
               'void f_82ec53d0(Ctx *__restrict A64_NOESC c) {\n'
               'store32(c->x[25]+400,101);store32(c->x[25]+408,102);\n'
               'goto L_82ec5980;\nL_82ec5980:;\nreturn;\n}\n'
               'void f_82ec6680(Ctx *__restrict A64_NOESC c) {\n'
               'store32(c->x[0]+352,103);store32(c->x[0]+360,104);\n'
               'goto L_82ec66f0;\nL_82ec66f0:;\nreturn;\n}\n'
               'void f_808f56f0(Ctx *__restrict A64_NOESC c) {\n'
               'goto L_808f575c;\nL_808f575c:;\nreturn;\n}\n')
    if hasattr(patch, "patch_attacks"):
        attacks, _ = patch.patch_attacks(attacks)
    with tempfile.TemporaryDirectory(prefix="hookshot-hits-") as temp:
        temp = Path(temp)
        (temp / "a64.h").write_text(A64, encoding="utf-8")
        (temp / "hits_body.c").write_text(source[source.index(marker):], encoding="utf-8")
        (temp / "patched_life.c").write_text(patched, encoding="utf-8")
        (temp / "patched_attacks.c").write_text(attacks, encoding="utf-8")
        (temp / "test.c").write_text(FIXTURE, encoding="utf-8")
        exe = temp / "hits-test.exe"
        cmd = [args.cc, "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
               "-I" + str(temp), "-I" + str(ROOT / "host"), str(temp / "test.c"), str(ROOT / "host/coop_proto.c"),
               str(ROOT / "third_party/monocypher/monocypher.c"), "-lm", "-o", str(exe)]
        if os.name != "nt":
            cmd += ["-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode:
            print(result.stdout + result.stderr)
            return result.returncode
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
        print(result.stdout + result.stderr, end="")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
