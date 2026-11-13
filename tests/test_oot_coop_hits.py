"""Behavioral combat observer checks on synthetic memory, without a game or GPU.

Executes production native hooks, codec and admission, plus the actual lift-patch output. Tests pre-clamp
overkill/protection, native setup/renewal/off, retries, independent loss/activation rings and reliable observations.
--patch-source uses the same behavioral fixture with a previous lift patch; missing hooks must fail at runtime.
--mutation removes a named guard only in the disposable compiled fixture; it must compile then fail behaviorally.
--replay-log reoffers retained v1 native records without drains, a stricter backlog than their original timing.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import re
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
#ifndef _WIN32
#include <pthread.h>
#endif
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
static unsigned guest_reads, clock_reads, member_reads;
static void *hos_guest_range(uint64_t a,uint64_t n) { guest_reads++;return a>=BASE && a-BASE<=sizeof memory && n<=sizeof memory-(a-BASE) ? memory+(size_t)(a-BASE):NULL; }
static int hos_mem_region(uint64_t a,uint64_t n) { return hos_guest_range(a,n)!=NULL; }
#define GP(a) hos_guest_range((a),1)
#define A64_PTR(a) GP(a)
static unsigned marks;
#define A64_MARK(a,n) do { (void)(a);(void)(n);marks++; } while(0)
static uint64_t recomp_load64(Ctx *c,uint64_t a) { (void)c;uint64_t x=0;void*p=hos_guest_range(a,8);CHECK(p);if(p)memcpy(&x,p,8);return x; }
static uint64_t recomp_load32(Ctx *c,uint64_t a) { (void)c;uint32_t x=0;void*p=hos_guest_range(a,4);CHECK(p);if(p)memcpy(&x,p,4);return x; }
static void store64(uint64_t a,uint64_t v) {memcpy(hos_guest_range(a,8),&v,8);}
static void store32(uint64_t a,uint32_t v) {memcpy(hos_guest_range(a,4),&v,4);}
static int trace_on, protect, playing=1, classify_player=1, damage_setting=1;
int rx_option(int id, int otherwise) { (void)id; (void)otherwise; return damage_setting; }
static uint32_t accepted=1u<<2;
static uint64_t now=1000;
static char hos_main_build_id[48]="e0f3ccfaf50065ae4ea01eb5aeb13b1a45da3348";
static int env_on(const char *name) {return !strcmp(name,"HOS_COOP_HITS_TRACE") && trace_on;}
uint64_t coop_now_ms(void) {clock_reads++;return now;}
int coop_member_present(int id) {member_reads++;return id>=1 && id<=COOP_MAX_MEMBERS && !!(accepted&(1u<<id));}
int hos_oot_hearts_enabled(void) {return protect;}
int hos_oot_is_player(Ctx*c,uint64_t a) {(void)c;return classify_player && a==LIFE;}
static unsigned local_logs,remote_logs,attack_logs,activation_logs,drop_logs;
static char loss_report[512],activation_report[512],setup_report[512],player_report[512],last_loss[512];
static unsigned capture_records, seen_loss[20000], seen_activation[20000];
static void hos_log(const char *fmt,...) {
    CHECK(!locked);
    char line[1024];va_list ap;va_start(ap,fmt);vsnprintf(line,sizeof line,fmt,ap);va_end(ap);
    if(strstr(fmt,"native %s")) {
        local_logs++;snprintf(last_loss,sizeof last_loss,"%.511s",line);
        if(capture_records) {unsigned long long id=0;const char *p=strstr(line,"(invocation ");
            CHECK(p && sscanf(p,"(invocation %llu",&id)==1 && id<20000);
            if(id<20000)seen_loss[id]++;
        }
    }
    if(strstr(fmt,"member %d observed"))remote_logs++;
    if(strstr(fmt,"coop: attack:"))attack_logs++;
    if(strstr(fmt,"coop: attack: activation")) {
        activation_logs++;
        if(capture_records) {unsigned long long id=0;const char *p=strstr(line,"(sample ");
            CHECK(p && sscanf(p,"(sample %llu",&id)==1 && id<20000);
            if(id<20000)seen_activation[id]++;
        }
    }
    if(strstr(line,"trace-v2 losses "))snprintf(loss_report,sizeof loss_report,"%.511s",line);
    if(strstr(line,"trace-v2 activations "))snprintf(activation_report,sizeof activation_report,"%.511s",line);
    if(strstr(line,"trace-v2 setup-details "))snprintf(setup_report,sizeof setup_report,"%.511s",line);
    if(strstr(line,"trace-v2 player "))snprintf(player_report,sizeof player_report,"%.511s",line);
    if(strstr(line,"complete no") || strstr(line,"EXHAUSTED"))drop_logs++;
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
    CHECK(ch->requested == hos_oot_damage(ch->before, (uint32_t)((int64_t)ch->before + ch->delta)));
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
    memset(&H,0,sizeof H);memset(&T,0,sizeof T);memset(memory,0,sizeof memory);
    hit_policy=NULL;hit_invocation=0;hos_oot_hits_active=hos_oot_attack_observe=0;
    trace_on=protect=veto=race=0;playing=classify_player=1;accepted=1u<<2;now=1000;
    damage_setting=1;
    local_logs=remote_logs=attack_logs=activation_logs=drop_logs=registrations=sends=before_calls=commit_calls=marks=0;first_token=0;message_fn=NULL;view_fn=NULL;
    capture_records=0;memset(seen_loss,0,sizeof seen_loss);memset(seen_activation,0,sizeof seen_activation);
    loss_report[0]=activation_report[0]=setup_report[0]=player_report[0]=last_loss[0]=0;
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
        CHECK(recomp_load32(&c,CELL)==8 && !local_logs && T.loss.write==1);
        CHECK(T.losses[0].sample.delta==-2 && T.losses[0].sample.requested==8 && T.losses[0].sample.at_ms==now);
        frame();CHECK(local_logs==1 && sends==1 && sent_to[0]==2);
        OotCoopHit h={0};CHECK(oot_coop_hit_parse(sent[0],112,&h));
        CHECK(h.before==10 && h.after==8 && h.target_id==101 && (h.event_id&1)==path);
        now+=80;frame();now+=170;frame();CHECK(sends==1); /* EVENT owns retries, not this layer */
    }
    /* Native observation keeps the original request; the optional life policy sees
       Damage taken's scaled value, and only the committed result is sent. */
    const uint32_t scaled[] = {9, 8, 6, 2};
    for (unsigned path=0;path<2;path++) for (int setting=0;setting<4;setting++) {
        fresh();trace_on=1;joined();damage_setting=setting;
        CHECK(hos_oot_hits_bind(&policy));hos_oot_hits_policy_on(1);
        Ctx scaled_context=context(path);f_804bd8c0(&scaled_context);
        CHECK(recomp_load32(&scaled_context,CELL)==scaled[setting] && before_calls==1 && commit_calls==1);
        CHECK(T.loss.write==1 && T.losses[0].sample.delta==-2 && T.losses[0].sample.requested==8 &&
              T.losses[0].sample.after==scaled[setting]);
        frame();CHECK(sends==1);
        OotCoopHit scaled_hit={0};CHECK(oot_coop_hit_parse(sent[0],112,&scaled_hit));
        CHECK(scaled_hit.before==10 && scaled_hit.after==scaled[setting]);
    }
    fresh();trace_on=1;joined();Ctx c=context(0);c.x[9]=10;c.x[10]=3;store32(CELL,3);f_804bd8c0(&c);
    CHECK(T.loss.write==1 && T.losses[0].sample.delta==-10 && T.losses[0].sample.before==3 && T.losses[0].sample.after==0);
    frame();CHECK(sends==1);
    fresh();trace_on=1;joined();protect=1;c=context(0);f_804bd8c0(&c);
    CHECK(T.loss.write==1 && T.losses[0].sample.delta==-2 && T.losses[0].sample.requested==8 && T.losses[0].sample.after==10);
    frame();CHECK(recomp_load32(&c,CELL)==10 && !sends && local_logs==1);
    fresh();trace_on=1;joined();CHECK(hos_oot_hits_bind(&policy));hos_oot_hits_policy_on(1);race=1;c=context(0);f_804bd8c0(&c);
    CHECK(before_calls==2 && commit_calls==1 && hit_invocation==1 && T.loss.write==1 && recomp_load32(&c,CELL)==7);
    frame();CHECK(sends==1);OotCoopHit h={0};CHECK(oot_coop_hit_parse(sent[0],112,&h) && h.before==9 && h.after==7);
    fresh();joined();CHECK(hos_oot_hits_bind(&policy));hos_oot_hits_policy_on(1);veto=1;c=context(0);f_804bd8c0(&c);
    CHECK(before_calls==1 && commit_calls==1 && recomp_load32(&c,CELL)==10);frame();CHECK(!sends);
    fresh();trace_on=1;joined();c=context(0);store64(LIFE+HIT_LIFE_OWNER,0);f_804bd8c0(&c);frame();CHECK(!sends && !local_logs);
    fresh();trace_on=1;joined();c=context(0);c.x[11]=9;c.x[12]=8;
    hos_oot_hits_committed(&c,1,0,8);CHECK(!T.loss.write && !sends); /* failed compare/exchange */
    hit_invocation=UINT64_MAX>>1;CHECK(!hos_oot_hits_begin() && hit_invocation==UINT64_MAX>>1);
    frame();CHECK(strstr(loss_report,"complete no counters EXHAUSTED") && strstr(activation_report,"complete no counters EXHAUSTED"));
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
    CHECK(pending==HIT_PENDING && H.dropped==1 && T.loss.write==HIT_PENDING+1 && !T.loss.not_kept);
    for(unsigned i=0;i<12;i++)frame();
    CHECK(sends==HIT_RATE/(OOT_HIT_BYTES+54) && !drop_logs && local_logs==HIT_PENDING+1);
    fresh();joined();Ctx c=context(0);f_804bd8c0(&c);
    view.version++;view.member[1].session=303;view_fn(&view,NULL);frame();CHECK(!sends);
    fresh();trace_on=1;joined();c=context(0);
    AcquireSRWLockExclusive(&H.lock);f_804bd8c0(&c);ReleaseSRWLockExclusive(&H.lock);
    CHECK(T.loss.write==1 && !sends);frame();CHECK(!drop_logs && local_logs==1);
}

static void attacks(void) {
    const uint64_t sensor=BASE+0xb000,components=BASE+0xc000,record=BASE+0xd000;
    fresh();trace_on=1;joined();
    store64(sensor+24,OWNER);store64(OWNER+624,components);store64(components+952,sensor);
    store64(sensor+128,record);store32(record+320,7);store32(record+324,0x40000000);
    Ctx c={0};c.x[19]=sensor;c.x[25]=record;
    unsigned char before[sizeof memory];memcpy(before,memory,sizeof memory);
    f_82ec53d0(&c);
    CHECK(T.activation.write==1 && T.activations[0].sample.kind==NATIVE_SETUP && T.activations[0].sample.primary==101 &&
          T.activations[0].sample.secondary==102 && T.activations[0].sample.owner==42 && T.activations[0].sample.power==7 &&
          T.activations[0].sample.multiplier==2.0f && T.activations[0].sample.path==0);
    CHECK(recomp_load32(&c,record+400)==101 && recomp_load32(&c,record+408)==102);
    store32(BASE+0xd000+400,0);store32(BASE+0xd000+408,0);
    CHECK(!memcmp(before,memory,sizeof memory)); /* only the synthetic guest, not observer, wrote IDs */
    c.x[0]=record+48;f_82ec6680(&c);
    CHECK(T.activation.write==2 && T.activations[1].sample.kind==NATIVE_RENEW && T.activations[1].sample.secondary==104);
    c.x[19]=sensor;c.x[21]=5;f_808f56f0(&c);
    CHECK(T.off.write==1 && T.offs[0].sample.kind==NATIVE_OFF && T.offs[0].sample.layer==5);
    frame();CHECK(attack_logs==4 && activation_logs==2 && !sends);
    store64(components+952,0);c.x[19]=sensor;c.x[25]=record;f_82ec53d0(&c);CHECK(T.activation.write==2);
    store64(components+952,sensor);c.x[25]=record+16;f_82ec53d0(&c);CHECK(T.activation.write==2);
    c.x[25]=record;store32(record+324,0x7fc00000);f_82ec53d0(&c);CHECK(T.activation.write==2);
    H.trace=0;hit_active_locked();CHECK(!hos_oot_attack_observe);
    c.x[0]=record+48;f_82ec6680(&c);CHECK(T.activation.write==2);
    puts("PASS: native setup/renew/off, power inputs, ownership, finite values and off-path");
}
static void trace_start(void) {fresh();trace_on=1;hos_oot_hits_start();frame();}
static void drain_all(void) {
    for(unsigned i=0;i<1000;i++) {
        frame();
        if(T.loss.read==T.loss.write && T.activation.read==T.activation.write &&
           T.setup.read==T.setup.write && T.nonloss.read==T.nonloss.write && T.off.read==T.off.write)break;
        CHECK(i!=999);
    }
    now+=1000;frame();
}
static void trace_totals(HitTrace *q,uint64_t offered,uint64_t recorded,uint64_t missing) {
    CHECK(q->offered==offered && q->read==recorded && q->not_kept==missing);
    CHECK(q->offered==q->read+q->not_kept && !q->exhausted);
}
static void trace_volume(void) {
    /* Both retained logs' exact kept volumes, without a single drain. Missing v1 records cannot be
       reconstructed: stress them separately as ALL losses and ALL activations, never label them recovered. */
    const unsigned setups[]={4688,1030},nonlosses[]={12550,2345},offs[]={7,1},unknown[]={651,136};
    for(unsigned side=0;side<2;side++) {
        trace_start();
        HitNative setup={.kind=NATIVE_SETUP,.primary=1,.secondary=2,.owner=42,.multiplier=1};
        HitNative nonloss={.kind=NATIVE_LIFE,.before=100,.after=100,.requested=100,.delta=1};
        for(unsigned i=0;i<nonlosses[side];i++) {
            nonloss.invocation=i+1;nonloss.at_ms=now++;hit_trace(&nonloss);
            if(i<setups[side]) {setup.invocation=i+1;setup.at_ms=now;hit_trace(&setup);}
            if(i==nonlosses[side]/2) {
                Ctx c=context(0);f_804bd8c0(&c);
                CHECK(T.loss.write==1 && T.losses[0].sample.before==10 && T.losses[0].sample.after==8 &&
                      T.losses[0].sample.delta==-2 && T.losses[0].sample.at_ms==now);
            }
        }
        for(unsigned i=0;i<offs[side];i++) {HitNative off={.kind=NATIVE_OFF};hit_trace(&off);}
        CHECK(!T.loss.not_kept && !T.activation.not_kept);
        drain_all();
        trace_totals(&T.loss,1,1,0);trace_totals(&T.activation,setups[side],setups[side],0);
        trace_totals(&T.setup,setups[side],OOT_HIT_TRACE_DETAILS,setups[side]-OOT_HIT_TRACE_DETAILS);
        trace_totals(&T.nonloss,nonlosses[side],OOT_HIT_TRACE_DETAILS,nonlosses[side]-OOT_HIT_TRACE_DETAILS);
        trace_totals(&T.off,offs[side],offs[side],0);
        CHECK(T.player_losses==1 && T.player_amount==2 && activation_logs==setups[side]);
        CHECK(strstr(loss_report,"offered 1 recorded 1 not-kept 0 pending 0 complete yes counters exact"));
        CHECK(strstr(activation_report,"not-kept 0 pending 0 complete yes counters exact"));
        CHECK(strstr(setup_report,"complete no counters exact"));
        for(unsigned i=0;i<unknown[side];i++) {
            HitNative loss={.kind=NATIVE_LIFE,.invocation=i+1,.delta=-3,.before=10,.requested=7,.after=7,.player=1};
            hit_trace(&loss);hit_trace(&setup);
        }
        drain_all();
        trace_totals(&T.loss,1+unknown[side],1+unknown[side],0);
        trace_totals(&T.activation,setups[side]+unknown[side],setups[side]+unknown[side],0);
    }
    puts("PASS: retained A/B volumes and unknown-drop worst cases; a real-hook synthetic player loss survives the middle of both floods");
}
static void trace_bounds(void) {
    /* Inclusive advertised 60-second bound, no drains, every timestamp and every duplicate ID retained. */
    trace_start();capture_records=1;
    unsigned losses=0,activations=0;
    for(unsigned second=0;second<60;second++) {
        for(unsigned i=0;i<256;i++) {
            HitNative a={.kind=i&1?NATIVE_SETUP:NATIVE_RENEW,.invocation=++activations,.at_ms=1000+second*1000+i,
                         .owner=42,.primary=7,.secondary=8};
            hit_trace(&a);
        }
        for(unsigned i=0;i<16;i++) {
            HitNative loss={.kind=NATIVE_LIFE,.invocation=++losses,.at_ms=1000+second*1000+i,
                            .before=10,.requested=8,.after=8,.delta=-2,.player=1};
            hit_trace(&loss);
        }
    }
    CHECK(losses==960 && activations==15360 && !T.loss.not_kept && !T.activation.not_kept);
    drain_all();
    trace_totals(&T.loss,losses,losses,0);trace_totals(&T.activation,activations,activations,0);
    for(unsigned i=1;i<=losses;i++)CHECK(seen_loss[i]==1);
    for(unsigned i=1;i<=activations;i++)CHECK(seen_activation[i]==1);
    CHECK(T.player_losses==960 && T.player_amount==1920);

    /* Exact capacity and capacity+1 for each independent lane; a later drain/reuse never erases failure. */
    trace_start();
    HitNative loss={.kind=NATIVE_LIFE,.before=9,.requested=5,.after=5,.delta=-4};
    HitNative activation={.kind=NATIVE_SETUP,.primary=1,.secondary=2};
    for(unsigned i=0;i<OOT_HIT_TRACE_LOSSES;i++)hit_trace(&loss);
    CHECK(!T.loss.not_kept && T.loss.write==OOT_HIT_TRACE_LOSSES);
    for(unsigned i=0;i<OOT_HIT_TRACE_ACTIVATIONS;i++)hit_trace(&activation);
    CHECK(!T.activation.not_kept && T.activation.write==OOT_HIT_TRACE_ACTIVATIONS);
    hit_trace(&loss);hit_trace(&activation);frame();
    CHECK(T.loss.not_kept==1 && T.activation.not_kept==1);
    CHECK(strstr(loss_report,"not-kept 1") && strstr(loss_report,"complete no"));
    CHECK(strstr(activation_report,"not-kept 1") && strstr(activation_report,"complete no"));
    drain_all();hit_trace(&loss);hit_trace(&activation);drain_all();
    trace_totals(&T.loss,OOT_HIT_TRACE_LOSSES+2,OOT_HIT_TRACE_LOSSES+1,1);
    trace_totals(&T.activation,OOT_HIT_TRACE_ACTIVATIONS+2,OOT_HIT_TRACE_ACTIVATIONS+1,1);
    CHECK(strstr(loss_report,"complete no") && strstr(activation_report,"complete no"));
    trace_start();T.loss.offered=UINT64_MAX;hit_trace(&loss);frame();
    CHECK(T.loss.exhausted && strstr(loss_report,"complete no counters EXHAUSTED"));
    puts("PASS: 16 loss requests/s + 256 activation requests/s for 60s without drains; inclusive capacities, sticky overflow and counter exhaustion");
}
static void trace_pending_off(void) {
    trace_start();
    /* A reserved but unpublished producer must not expose uninitialized data or be called recorded. */
    T.loss.offered=T.loss.write=1;
    HitNative loss={.kind=NATIVE_LIFE,.invocation=1,.before=10,.after=9,.requested=9,.delta=-1};
    hit_trace(&loss);now+=1000;frame();
    CHECK(!T.loss.read && !local_logs && strstr(loss_report,"offered 2 recorded 0 not-kept 0 pending 2"));
    T.losses[0].sample=loss;__atomic_store_n(&T.losses[0].ready,1,__ATOMIC_RELEASE);
    drain_all();trace_totals(&T.loss,2,2,0);
    CHECK(strstr(loss_report,"pending 0 complete yes"));

    fresh();hos_oot_hits_start();
    guest_reads=clock_reads=member_reads=0;
    const uint64_t invocation=hit_invocation;
    Ctx c={0};
    for(unsigned i=0;i<10000;i++) {
        frame();hit_trace(&loss);hos_oot_hits_attack(&c,0,0);hos_oot_hits_attack_id(&c,0);
        hos_oot_hits_attack_off(&c,0,0);CHECK(!hos_oot_hits_begin());
    }
    CHECK(!guest_reads && !clock_reads && !member_reads && !local_logs && !attack_logs && !locked);
    CHECK(!T.loss.offered && !T.activation.offered && !T.setup.offered && !T.nonloss.offered && !T.off.offered);
    CHECK(!T.reported && hit_invocation==invocation);
    puts("PASS: unpublished slots remain pending; 10000 disabled drains/hooks do no guest, clock, membership, lock or record work");
}
#ifndef _WIN32
static unsigned producers_done;
static void *trace_producer(void *arg) {
    const unsigned worker=(unsigned)(uintptr_t)arg;
    for(unsigned i=0;i<2048;i++) {
        HitNative a={.kind=NATIVE_SETUP,.invocation=1+worker*2048+i,.at_ms=worker*2048+i,.primary=7,.secondary=8};
        hit_trace(&a);
        if(i<128) {
            HitNative loss={.kind=NATIVE_LIFE,.invocation=1+worker*128+i,.at_ms=worker*128+i,
                            .before=10,.requested=9,.after=9,.delta=-1,.player=1};
            hit_trace(&loss);
        }
    }
    __atomic_fetch_add(&producers_done,1,__ATOMIC_RELEASE);
    return NULL;
}
static void trace_concurrent(void) {
    for(unsigned draining=0;draining<2;draining++) {
        trace_start();capture_records=1;producers_done=0;
        pthread_t threads[8];
        for(unsigned i=0;i<8;i++)CHECK(!pthread_create(&threads[i],NULL,trace_producer,(void*)(uintptr_t)i));
        if(draining)while(__atomic_load_n(&producers_done,__ATOMIC_ACQUIRE)!=8)frame();
        for(unsigned i=0;i<8;i++)CHECK(!pthread_join(threads[i],NULL));
        drain_all();
        trace_totals(&T.loss,1024,1024,0);trace_totals(&T.activation,16384,16384,0);
        for(unsigned i=1;i<=1024;i++)CHECK(seen_loss[i]==1);
        for(unsigned i=1;i<=16384;i++)CHECK(seen_activation[i]==1);
        CHECK(T.setup.offered==16384 && T.setup.offered==T.setup.read+T.setup.not_kept);
    }
    puts("PASS: eight concurrent producers at both exact capacities, with and without concurrent drain; every critical record exactly once");
}
#else
static void trace_concurrent(void) {puts("NOT RUN: pthread producer race fixture on Windows");}
#endif

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
#include "replayed_trace.c"
int main(void) {
    caller_claims();wire();native();permissions();receive();capacity();attacks();
    trace_volume();trace_bounds();trace_pending_off();trace_concurrent();replayed_trace();
    CHECK(!locked);if(failed)return 1;
    puts("PASS: committed/pre-clamp life, protection, consent, EVENT, replay and complete bounded trace-v2 records");return 0;
}
'''


def load_patch(path):
    sys.path.insert(0, str(ROOT / "nx2"))
    spec = importlib.util.spec_from_file_location("hits_patch", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


MUTATIONS = ("cas", "pre-clamp", "attack", "owner", "build", "off",
             "loss-isolation", "activation-capacity", "silent-overflow", "offered-count",
             "protected-request", "trace-off", "timestamp")


def replay_source(paths):
    body = ["static void replayed_trace(void) {"]
    for path in paths:
        raw = path.read_bytes()
        rows, counts = [], dict(loss=0, activation=0, setup=0, nonloss=0, off=0)
        for line in raw.decode("utf-8").splitlines():
            life = re.search(r"coop: hit: native (player|actor) (\d+) -> (\d+) requested (\d+) pre-clamp delta (-?\d+) \(invocation (\d+), path (\d+)\)", line)
            attack = re.search(r"coop: attack: (setup|renew|off) owner (\d+) ids (\d+)/(\d+) slot (\d+) layer (\d+) power (-?\d+) multiplier ([\d.e+-]+) \(sample (\d+)\)", line)
            if life:
                who, before, after, requested, delta, invocation, branch = life.groups()
                negative = int(delta) < 0 or int(after) < int(before) or int(requested) < int(before)
                counts["loss" if negative else "nonloss"] += 1
                rows.append(f"{{.kind=NATIVE_LIFE,.before={before},.after={after},.requested={requested},.delta={delta},.invocation={invocation},.path={branch},.player={int(who=='player')}" + "}")
            elif attack:
                kind, owner, primary, secondary, slot, layer, power, multiplier, invocation = attack.groups()
                counts["activation" if kind in ("setup", "renew") else "off"] += 1
                if kind == "setup":
                    counts["setup"] += 1
                rows.append(f"{{.kind=NATIVE_{kind.upper()},.owner={owner},.primary={primary},.secondary={secondary},.path={slot},.layer={layer},.power={power},.multiplier={multiplier},.invocation={invocation}}}")
            elif "coop: hit: native " in line or "coop: attack: " in line:
                raise ValueError(f"unparsed native record in {path}: {line}")
        if not rows:
            raise ValueError(f"no native v1 records in {path}")
        if counts["loss"] > 1024 or counts["activation"] > 16384:
            raise ValueError("replay exceeds the declared no-drain critical bound")
        print(f"REPLAY {path.name} sha256={hashlib.sha256(raw).hexdigest()} counts={counts}; original timing unknown, no drains until all offered")
        body += ["{", "static const HitNative records[] = {" + ",\n".join(rows) + "};",
                 "trace_start();",
                 "for(unsigned i=0;i<sizeof records/sizeof *records;i++){HitNative sample=records[i];sample.at_ms=now++;hit_trace(&sample);}",
                 "drain_all();"]
        for name, offered in counts.items():
            cap = 1024 if name == "loss" else 16384 if name == "activation" else 64
            kept = min(cap, offered)
            body += [f"trace_totals(&T.{name},{offered},{kept},{offered-kept});"]
        body += [f'puts("PASS: retained v1 log replay, {len(rows)} records, every loss/activation preserved");', "}"]
    return "\n".join(body + ["}"])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cc", default=os.environ.get("CC") or shutil.which("gcc") or "gcc")
    parser.add_argument("--patch-source", type=Path, default=ROOT / "nx2/oot_patches.py")
    parser.add_argument("--mutation", choices=MUTATIONS)
    parser.add_argument("--replay-log", type=Path, action="append", default=[])
    args = parser.parse_args()
    patch = load_patch(args.patch_source)
    source = (ROOT / "host/oot_coop_hits.c").read_text(encoding="utf-8")
    marker = "/* ---- wire and application-level replay protection"
    assert source.count(marker) == 1
    mutations = {
        "cas": ("if ((uint32_t)c->x[11] != (uint32_t)c->x[10] ||", "if ("),
        "pre-clamp": ("path ? (int64_t)(int32_t)c->x[9] : -(int64_t)(int32_t)c->x[9]",
                      "(int64_t)(uint32_t)c->x[12] - before"),
        "attack": ("hit_trace(&sample);", "(void)sample;"),
        "owner": ("recomp_load64(c, components + 952) != sensor", "0"),
        "build": ('!strcmp(hos_main_build_id, HITS_BUILD)', "1"),
        "off": ("H.known && H.trace,", "H.known,"),
        "loss-isolation": ("hit_trace_offer(&T.loss, T.losses, OOT_HIT_TRACE_LOSSES, sample);",
                           "hit_trace_offer(&T.nonloss, T.nonlosses, OOT_HIT_TRACE_DETAILS, sample);"),
        "activation-capacity": ("hit_trace_offer(&T.activation, T.activations, OOT_HIT_TRACE_ACTIVATIONS, sample);",
                                "hit_trace_offer(&T.activation, T.activations, OOT_HIT_TRACE_DETAILS, sample);"),
        "silent-overflow": ("hit_trace_increment(&q->not_kept, q);", "(void)q;"),
        "offered-count": ("if (!hit_trace_increment(&q->offered, q)) return;", "if (0) return;"),
        "protected-request": ("sample->delta < 0 || sample->requested < sample->before || sample->after < sample->before",
                              "sample->after < sample->before"),
        "trace-off": ("if (!__atomic_load_n(&hos_oot_attack_observe, __ATOMIC_ACQUIRE)) return;", "if (0) return;"),
        "timestamp": (".at_ms = now, .owner = owner", ".at_ms = 0, .owner = owner"),
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
               '(void)c;goto L_808f575c;\nL_808f575c:;\nreturn;\n}\n')
    if hasattr(patch, "patch_attacks"):
        attacks, _ = patch.patch_attacks(attacks)
    with tempfile.TemporaryDirectory(prefix="hookshot-hits-") as temp:
        temp = Path(temp)
        (temp / "a64.h").write_text(A64, encoding="utf-8")
        (temp / "hits_body.c").write_text(source[source.index(marker):], encoding="utf-8")
        (temp / "patched_life.c").write_text(patched, encoding="utf-8")
        (temp / "patched_attacks.c").write_text(attacks, encoding="utf-8")
        (temp / "replayed_trace.c").write_text(replay_source(args.replay_log), encoding="utf-8")
        (temp / "test.c").write_text(FIXTURE, encoding="utf-8")
        exe = temp / "hits-test.exe"
        cmd = [args.cc, "-std=gnu11", "-O1", "-g", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
               "-I" + str(temp), "-I" + str(ROOT / "host"), str(temp / "test.c"), str(ROOT / "host/coop_proto.c"),
               str(ROOT / "host/oot_damage.c"), str(ROOT / "third_party/monocypher/monocypher.c"), "-lm", "-o", str(exe)]
        if os.name != "nt":
            cmd += ["-pthread", "-fsanitize=address,undefined", "-fno-sanitize-recover=undefined"]
        print("COMPILE:", " ".join(cmd), flush=True)
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode:
            print(result.stdout + result.stderr)
            return result.returncode
        print("COMPILED fixture; executing behavioral assertions", flush=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True, timeout=120)
        print(result.stdout + result.stderr, end="")
        return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
