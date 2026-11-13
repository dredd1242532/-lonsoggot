/* Authored synthetic arena only: not coordinates, damage or observations from the game. */
#include "coop_duel.h"
#include "coop_duel_menu.h"
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <stdarg.h>
static unsigned checks, observed;
#define CHECK(x) do { checks++; if (!(x)) { fprintf(stderr, "FAIL line %d: %s\n", __LINE__, #x); exit(1); } } while (0)
static CoopView menu_view;
static CoopOffer menu_offer;
static CoopRules presentation_rules;
static uint32_t presented_members;
static int temporary = 1, presentation_ready = 1, kit_ready = 1;
static unsigned presentation_queries, kit_queries, chat_queries, menu_queries;
static int chat_input, menu_input;
static char menu_log[256];
void coop_view_get(CoopView *v) { *v = menu_view; }
int hos_oot_coop_offer(CoopOffer *o) { *o = menu_offer; return 1; }
int hos_oot_coop_presentation(CoopRules *r, uint32_t *members) {
    presentation_queries++;
    memset(r, 0, sizeof *r); *members = 0;
    if (!presentation_ready) return 0;
    *r = presentation_rules; *members = presented_members;
    return 1;
}
int hos_oot_coop_kit_ready(uint32_t offer, uint32_t epoch) {
    kit_queries++;
    CHECK(offer == menu_offer.token && epoch == menu_offer.rules.epoch);
    return kit_ready;
}
int rx_option(int id, int otherwise) {
    CHECK(!otherwise && (id == 1 || id == HOS_OPT_CHAT_FOCUS));
    if (id == HOS_OPT_CHAT_FOCUS) { chat_queries++; return chat_input; }
    menu_queries++; return menu_input;
}
int coop_profile_active(void) { return temporary; }
void hos_log(const char *fmt, ...) {
    va_list args;
    va_start(args, fmt);
    vsnprintf(menu_log, sizeof menu_log, fmt, args);
    va_end(args);
}
static void setting(const char *key, const char *value) {
#ifdef _WIN32
    CHECK(_putenv_s(key, value ? value : "") == 0);
#else
    CHECK(value ? setenv(key, value, 1) == 0 : unsetenv(key) == 0);
#endif
}
static CoopArena arena(void) {
    CoopArena a = {.version = 1, .id = 1, .vertices = 4, .environment = COOP_ARENA_CLEAR,
        .spawn = {{0,0,-2,0},{0,0,2,3.141592654f}}, .ymin = -1, .ymax = 1, .margin = .1f,
        .boundary = {{-10,-10},{10,-10},{10,10},{-10,10}}};
    memset(a.build, 1, 20); memset(a.variant, 2, 32); memset(a.source, 3, 32); memset(a.survey, 4, 32);
    strcpy(a.map, "synthetic-fixture"); return a;
}
static CoopRules rules(void) {
    CoopRules r; coop_rules_default(&r); r.epoch = 3;
    const int values[][2] = {{80,1},{16,1},{18,100},{19,1},{20,700},{21,0},{31,10},{32,0},
        {34,3},{35,0},{36,0},{37,1},{38,0},{39,0},{40,3},{83,1},{97,1}};
    for (unsigned i = 0; i < sizeof values / sizeof *values; i++) CHECK(coop_feature_set(&r, values[i][0], values[i][1]));
    return r;
}
static CoopDuelTerms terms(CoopRules *r, uint64_t id) {
    CoopDuelTerms t; CoopArena a = arena(); uint8_t members[] = {1,2}; uint64_t sessions[] = {101,202};
    CHECK(coop_duel_terms(&t, r, &a, id, 33, members, sessions)); return t;
}
static CoopDuelContext context(unsigned self, uint64_t now) {
    CoopDuelContext c = {.enabled=1,.diagnostic=1,.joined=1,.accepted=1,.temporary=1,.playing=1,
        .self=self,.capabilities=COOP_DUEL_CAP_REQUIRED,.offer=55+self,.epoch=3,
        .save_generation=9+self,.area_generation=10+self,.kit_generation=11+self,.sampled_ms=now};
    return c;
}
static void sample(CoopDuelContext c[2], uint64_t now) { c[0].sampled_ms = c[1].sampled_ms = now; }
static void begin_pair(CoopDuel d[2], CoopDuelContext c[2], CoopDuelTerms *t, uint64_t now) {
    memset(d, 0, 2 * sizeof *d);
    for (unsigned i = 0; i < 2; i++) { c[i] = context(i, now); CHECK(!coop_duel_begin(&d[i], t, &c[i], now)); }
}
static void ready_pair(CoopDuel d[2], CoopDuelContext c[2], uint64_t now) {
    sample(c, now); CoopDuelControl m[2];
    for (unsigned i = 0; i < 2; i++) CHECK(!coop_duel_ready(&d[i], &c[i], d[i].terms.digest, 3, now, &m[i]));
    for (unsigned i = 0; i < 2; i++) {
        CHECK(!coop_duel_receive(&d[i], &c[i], 2-i, d[i].terms.session[1-i], &m[1-i], now));
        CHECK(d[i].state == COOP_DUEL_COUNTDOWN);
    }
    sample(c, now + 2999);
    for (unsigned i = 0; i < 2; i++) { CHECK(!coop_duel_update(&d[i], &c[i], now+2999)); CHECK(d[i].state == COOP_DUEL_COUNTDOWN); }
    sample(c, now + 3000);
    for (unsigned i = 0; i < 2; i++) { CHECK(!coop_duel_update(&d[i], &c[i], now+3000)); CHECK(d[i].state == COOP_DUEL_ACTIVE); }
}
static int observe(const CoopDuelAttack *a, CoopDuelContact *out, void *local) {
    CHECK(a->attack != 0); observed++; *out = *(CoopDuelContact *)local; return 1;
}
static int hit(CoopDuel *d, CoopDuelContext *c, uint64_t id, uint64_t now, CoopDuelContact *native) {
    c->sampled_ms = now;
    CoopDuelAttack a = {.duel=d->terms.id,.session=d->terms.session[1-c->self],.attack=id,.observed_ms=now,
        .epoch=3,.round=d->round,.from=2-c->self};
    return coop_duel_hit(d, c, &a, now, observe, native);
}
static void exchange(CoopDuel d[2], CoopDuelContext c[2], uint64_t now) {
    sample(c, now); CoopDuelControl m[2];
    CHECK(coop_duel_status(&d[0], &m[0]) && coop_duel_status(&d[1], &m[1]));
    for (unsigned i = 0; i < 2; i++) CHECK(!coop_duel_receive(&d[i], &c[i], 2-i, d[i].terms.session[1-i], &m[1-i], now));
}
static void close_round(CoopDuel d[2], CoopDuelContext c[2], uint64_t now) {
    exchange(d, c, now);
    CHECK(d[0].state == COOP_DUEL_SETTLING && d[1].state == COOP_DUEL_SETTLING);
    sample(c, now + 500);
    CHECK(!coop_duel_update(&d[0], &c[0], now+500)); CHECK(!coop_duel_update(&d[1], &c[1], now+500));
    exchange(d, c, now+500);
}
static void descriptors(const char *path) {
    CoopArena a = arena(), out = {0}, bad; uint8_t wire[COOP_ARENA_BYTES + 1], digest[32], other[32];
    CHECK(coop_arena_valid(&a)); CHECK(coop_arena_pack(wire, sizeof wire, &a) == COOP_ARENA_BYTES);
    CHECK(coop_arena_parse(wire, COOP_ARENA_BYTES, &out)); CHECK(coop_arena_digest(&out, digest));
    CHECK(coop_arena_matches(&out, a.build, a.map, 0, a.variant)); CHECK(!coop_arena_matches(&out, a.build, a.map, 1, a.variant));
    CHECK(!coop_arena_matches(&out, a.build, "other-map", 0, a.variant));
    CHECK(!coop_arena_parse(wire, COOP_ARENA_BYTES-1, &out)); CHECK(!coop_arena_parse(wire, COOP_ARENA_BYTES+1, &out));
    wire[8] = 2; CHECK(!coop_arena_parse(wire, COOP_ARENA_BYTES, &out)); wire[8] = 1;
    wire[383] = 1; CHECK(!coop_arena_parse(wire, COOP_ARENA_BYTES, &out)); wire[383] = 0;
    bad=a; bad.version++; CHECK(!coop_arena_valid(&bad)); bad=a; bad.age=2; CHECK(!coop_arena_valid(&bad));
    bad=a; bad.id=2; CHECK(!coop_arena_valid(&bad)); bad=a; bad.environment=0; CHECK(!coop_arena_valid(&bad));
    bad=a; bad.time_policy=1; CHECK(!coop_arena_valid(&bad)); bad=a; bad.vertices=17; CHECK(!coop_arena_valid(&bad));
    bad=a; bad.vertices=2; CHECK(!coop_arena_valid(&bad)); bad=a; bad.spawn[0][0]=10; CHECK(!coop_arena_valid(&bad));
    bad=a; bad.spawn[0][1]=2; CHECK(!coop_arena_valid(&bad)); bad=a; bad.spawn[0][3]=NAN; CHECK(!coop_arena_valid(&bad));
    bad=a; bad.spawn[0][3]=3.141592654f; CHECK(!coop_arena_valid(&bad)); bad=a; bad.boundary[0][0]=INFINITY; CHECK(!coop_arena_valid(&bad));
    bad=a; bad.boundary[2][0]=0; bad.boundary[2][1]=-10; CHECK(!coop_arena_valid(&bad));
    bad=a; memcpy(bad.spawn[1], bad.spawn[0], sizeof bad.spawn[0]); CHECK(!coop_arena_valid(&bad));
    bad=a; bad.ymax=bad.ymin; CHECK(!coop_arena_valid(&bad)); bad=a; bad.margin=0; CHECK(!coop_arena_valid(&bad));
    bad=a; memset(bad.survey,0,32); CHECK(!coop_arena_valid(&bad)); bad=a; memset(bad.map,'x',64); CHECK(!coop_arena_valid(&bad));
    bad=a; bad.spawn[0][0]=1; CHECK(coop_arena_digest(&bad,other)); CHECK(memcmp(digest,other,32));
    FILE *f=fopen(path,"wb"); CHECK(f); CHECK(fwrite(wire,1,COOP_ARENA_BYTES,f)==COOP_ARENA_BYTES); CHECK(!fclose(f));
    CHECK(coop_arena_load(path,&out)); CHECK(!coop_arena_load("",&out));
    f=fopen(path,"ab"); CHECK(f); CHECK(fputc(1,f)!=EOF); CHECK(!fclose(f)); CHECK(!coop_arena_load(path,&out));
    puts("PASS: descriptor version, exact size, private file loading, map/build/age, bounds, winding, facing and provenance");
}
static void ready_tests(void) {
    CoopRules r=rules(); CoopDuelTerms t=terms(&r,1), changed; CoopDuel d[2]; CoopDuelContext c[2]; CoopDuelControl m;
    for (unsigned bit=1; bit<=128; bit<<=1) {
        begin_pair(d,c,&t,100); c[0].capabilities &= ~bit;
        CHECK(coop_duel_ready(&d[0],&c[0],t.digest,3,100,&m)==COOP_DUEL_CAPABILITY); CHECK(!d[0].ready[0]);
        CHECK(strlen(coop_duel_reason(COOP_DUEL_CAPABILITY,c[0].capabilities))>10);
    }
    begin_pair(d,c,&t,100); uint8_t wrong[32]; memcpy(wrong,t.digest,32); wrong[5]^=1;
    CHECK(coop_duel_ready(&d[0],&c[0],wrong,3,100,&m)==COOP_DUEL_STALE);
    CHECK(coop_duel_ready(&d[0],&c[0],t.digest,2,100,&m)==COOP_DUEL_STALE);
    CHECK(!coop_duel_ready(&d[1],&c[1],t.digest,3,100,&m));
    m.capabilities &= ~COOP_DUEL_CAP_CONTACT; CHECK(coop_duel_receive(&d[0],&c[0],2,202,&m,100)==COOP_DUEL_CAPABILITY);
    m.capabilities=COOP_DUEL_CAP_REQUIRED; m.epoch=2; CHECK(coop_duel_receive(&d[0],&c[0],2,202,&m,100)==COOP_DUEL_STALE);
    m.epoch=3; m.digest[0]^=1; CHECK(coop_duel_receive(&d[0],&c[0],2,202,&m,100)==COOP_DUEL_STALE); m.digest[0]^=1;
    CHECK(coop_duel_receive(&d[0],&c[0],1,101,&m,100)==COOP_DUEL_PEER);
    uint8_t bytes[COOP_DUEL_CONTROL_BYTES]; CoopDuelControl parsed;
    CHECK(coop_duel_control_pack(bytes,sizeof bytes,&m)==sizeof bytes); CHECK(coop_duel_control_parse(bytes,sizeof bytes,&parsed));
    CHECK(!coop_duel_control_parse(bytes,sizeof bytes-1,&parsed)); bytes[0]=2; CHECK(!coop_duel_control_parse(bytes,sizeof bytes,&parsed)); bytes[0]=1;
    bytes[100]=1; CHECK(!coop_duel_control_parse(bytes,sizeof bytes,&parsed)); bytes[100]=0;
    CHECK(!coop_duel_receive(&d[0],&c[0],2,202,&m,100)); CHECK(!d[0].ready[0]);
    CHECK(!coop_duel_ready(&d[0],&c[0],t.digest,3,100,&m)); CHECK(d[0].state==COOP_DUEL_COUNTDOWN);
    c[0].epoch++; CHECK(coop_duel_update(&d[0],&c[0],100)==COOP_DUEL_CONTEXT);
    CHECK(!d[0].ready[0] && !d[0].ready[1]); CHECK(coop_duel_begin(&d[0],&t,&(CoopDuelContext){0},100)==COOP_DUEL_DISABLED);
    CHECK(coop_feature_set(&r,31,20)); changed=terms(&r,1); CHECK(memcmp(t.digest,changed.digest,32));
    begin_pair(d,c,&changed,100);
    CHECK(coop_duel_ready(&d[0],&c[0],t.digest,3,100,&m)==COOP_DUEL_STALE);
    CHECK(coop_feature_set(&r,200,7)); CoopDuelTerms unknown=terms(&r,1); CHECK(memcmp(changed.digest,unknown.digest,32));
    /* Every supported rule has at least one absent/unsupported refusal, independently of registry range checks. */
    const int required[]={80,16,18,19,20,21,31,32,34,35,36,37,38,39,40,83,97};
    for (unsigned k=0;k<sizeof required/sizeof *required;k++) {
        CoopRules bad=rules(); unsigned n=bad.features.count;
        for (unsigned i=0;i<n;i++) if (bad.features.bytes[3*i]==required[k]) {
            memmove(bad.features.bytes+3*i,bad.features.bytes+3*(i+1),3*(n-i-1)); bad.features.count--; break;
        }
        CoopArena a=arena(); CHECK(!coop_duel_terms(&changed,&bad,&a,1,33,t.member,t.session));
    }
    const int unsupported[][2]={{16,0},{19,2},{21,1},{34,2},{35,1},{36,1},{38,1},{39,1},{32,1},{40,12},{83,2},{97,0},{17,0},{84,1}};
    for (unsigned k=0;k<sizeof unsupported/sizeof *unsupported;k++) {
        CoopRules bad=rules(); int id=unsupported[k][0], value=unsupported[k][1];
        int found=0; for (unsigned i=0;i<bad.features.count;i++) if (bad.features.bytes[3*i]==id) {
            bad.features.bytes[3*i+1]=(uint8_t)value; bad.features.bytes[3*i+2]=(uint8_t)(value>>8); found=1;
        }
        if (!found) CHECK(coop_feature_set(&bad,id,value));
        CoopArena a=arena(); CHECK(!coop_duel_terms(&changed,&bad,&a,1,33,t.member,t.session));
    }
    CoopArena adult=arena(); adult.age=1;
    CoopRules adult_rules=rules(); CHECK(coop_feature_set(&adult_rules,32,1)); CHECK(coop_feature_set(&adult_rules,40,12));
    CHECK(coop_duel_terms(&changed,&adult_rules,&adult,1,33,t.member,t.session));
    puts("PASS: exact Ready digest/epoch, every required rule, capability bits, participant binding and countdown");
}
static void ledger_tests(void) {
    CoopRules r=rules(); CoopDuelTerms t=terms(&r,1); CoopDuel d[2]; CoopDuelContext c[2];
    CoopDuelContact normal={.contact=1,.native_units=2}, lethal={.contact=1,.native_units=160};
    begin_pair(d,c,&t,100); ready_pair(d,c,100);
    CHECK(!hit(&d[0],&c[0],1,4000,&normal)); CHECK(d[0].hp[0]==38 && d[0].hp[1]==40);
    CHECK(hit(&d[0],&c[0],1,4000,&normal)==COOP_DUEL_REPLAY);
    CHECK(hit(&d[0],&c[0],2,4100,&normal)==COOP_DUEL_GRACE);
    CHECK(hit(&d[0],&c[0],2,4800,&normal)==COOP_DUEL_REPLAY);
    CHECK(!hit(&d[0],&c[0],3,4800,&normal)); CHECK(d[0].hp[0]==36);
    CoopDuelContact miss={0}, shield={.contact=1,.shielded=1,.native_units=2};
    /* Native adapter refusal forwarded unchanged for range, facing and cover misses; no invented geometry. */
    for (unsigned i=0;i<3;i++) CHECK(hit(&d[0],&c[0],4+i,5600+i*800,&miss)==COOP_DUEL_MISS);
    CHECK(hit(&d[0],&c[0],7,8000,&shield)==COOP_DUEL_MISS); CHECK(d[0].hp[0]==36);
    CoopDuelAttack stale={.duel=1,.session=202,.attack=8,.observed_ms=8000,.epoch=3,.round=1,.from=2};
    c[0].sampled_ms=8300; CHECK(coop_duel_hit(&d[0],&c[0],&stale,8300,observe,&normal)==COOP_DUEL_STALE);
    stale.observed_ms=8300; CHECK(coop_duel_hit(&d[0],&c[0],&stale,8300,observe,&normal)==COOP_DUEL_REPLAY);
    CoopDuelControl remote; CHECK(coop_duel_status(&d[1],&remote)); remote.hp=0;
    CHECK(!coop_duel_receive(&d[0],&c[0],2,202,&remote,8300)); CHECK(d[0].hp[0]==36 && d[0].hp[1]==0);
    sample(c,10300); CHECK(coop_duel_update(&d[0],&c[0],10300)==COOP_DUEL_TIMEOUT); CHECK(!d[0].score[0] && !d[0].score[1]);
    for (unsigned best=1;best<=5;best+=2) {
        CHECK(coop_feature_set(&r,34,(int)best)); t=terms(&r,1); begin_pair(d,c,&t,100);
        uint64_t now=100;
        for (unsigned round=1;round<=best/2+1;round++) {
            ready_pair(d,c,now); now+=4000;
            CHECK(!hit(&d[1],&c[1],1,now,&lethal)); close_round(d,c,now);
            CHECK(d[0].score[0]==round && d[1].score[0]==round && !d[0].score[1]);
            if (round==best/2+1) CHECK(d[0].state==COOP_DUEL_FINISHED && d[1].state==COOP_DUEL_FINISHED);
            else {
                now+=1000; sample(c,now);
                CHECK(!coop_duel_next_round(&d[0],&c[0],now)); CHECK(!coop_duel_next_round(&d[1],&c[1],now));
                CHECK(!d[0].ready[0] && !d[1].ready[1] && d[0].hp[0]==40);
            }
        }
    }
    CHECK(coop_feature_set(&r,34,3)); t=terms(&r,1); begin_pair(d,c,&t,100); ready_pair(d,c,100);
    CHECK(!hit(&d[0],&c[0],1,4000,&lethal)); CHECK(!hit(&d[1],&c[1],1,4000,&lethal)); close_round(d,c,4000);
    CHECK(d[0].state==COOP_DUEL_ROUND_END && !d[0].score[0] && !d[0].score[1] && !d[0].last_winner);
    CHECK(d[1].state==COOP_DUEL_ROUND_END && !d[1].last_winner);
    char text[160]; coop_duel_score_text(&d[0],text,sizeof text); CHECK(strstr(text,"0-0") && strstr(text,"Round ended"));
    CHECK(coop_feature_set(&r,18,25)); t=terms(&r,1); begin_pair(d,c,&t,100); ready_pair(d,c,100);
    normal.native_units=1;
    for (unsigned i=0;i<4;i++) CHECK(!hit(&d[0],&c[0],i+1,4000+i*800,&normal));
    CHECK(d[0].hp[0]==39 && !d[0].remainder);
    CHECK(coop_feature_set(&r,18,0)); t=terms(&r,1); begin_pair(d,c,&t,100); ready_pair(d,c,100);
    CHECK(!hit(&d[0],&c[0],1,4000,&lethal)); CHECK(d[0].hp[0]==40);
    begin_pair(d,c,&t,100); ready_pair(d,c,100);
    for (unsigned i=1;i<=32;i++) CHECK(hit(&d[0],&c[0],i,4000,&miss)==COOP_DUEL_MISS);
    CHECK(hit(&d[0],&c[0],33,4000,&miss)==COOP_DUEL_LIMIT);
    CHECK(hit(&d[0],&c[0],33,5000,&miss)==COOP_DUEL_REPLAY);
    CHECK(hit(&d[0],&c[0],34,5000,&miss)==COOP_DUEL_MISS);
    puts("PASS: receiver-only hearts, stable attack dedup, grace, misses, simultaneous draw, best-of1/3/5 and exact scale carry");
}
static void interruptions(void) {
    CoopRules r=rules(); CoopDuelTerms t=terms(&r,1); CoopDuel d[2]; CoopDuelContext c[2];
    for (unsigned which=0;which<9;which++) {
        begin_pair(d,c,&t,100); ready_pair(d,c,100); sample(c,4000);
        switch (which) {
        case 0:c[0].accepted=0;break; case 1:c[0].joined=0;break; case 2:c[0].menu_or_chat=1;break;
        case 3:c[0].playing=0;break; case 4:c[0].offer++;break; case 5:c[0].save_generation++;break;
        case 6:c[0].area_generation++;break; case 7:c[0].kit_generation++;break; case 8:c[0].capabilities=0;break;
        }
        CHECK(coop_duel_update(&d[0],&c[0],4000)!=COOP_DUEL_OK); CHECK(d[0].state==COOP_DUEL_INTERRUPTED);
        CHECK(!d[0].ready[0] && !d[0].ready[1] && !d[0].score[0] && !d[0].score[1]);
        c[0]=context(0,4000); CHECK(coop_duel_begin(&d[0],&t,&c[0],4000)==COOP_DUEL_REPLAY);
    }
    begin_pair(d,c,&t,100); ready_pair(d,c,100); c[0].enabled=0;
    unsigned before=observed; CoopDuelContact normal={.contact=1,.native_units=1};
    CHECK(hit(&d[0],&c[0],1,4000,&normal)==COOP_DUEL_DISABLED); CHECK(observed==before);
    CoopDuel off={0}, kept=off; CHECK(coop_duel_update(&off,NULL,UINT64_MAX)==COOP_DUEL_DISABLED); CHECK(!memcmp(&off,&kept,sizeof off));
    begin_pair(d,c,&t,100); sample(c,30101); CHECK(coop_duel_update(&d[0],&c[0],30101)==COOP_DUEL_TIMEOUT);
    begin_pair(d,c,&t,100); ready_pair(d,c,100);
    CoopDuelControl cancel; CHECK(coop_duel_status(&d[1],&cancel));
    cancel.kind=COOP_DUEL_CANCEL; cancel.sequence=0; cancel.hp=0;
    CHECK(!coop_duel_receive(&d[0],&c[0],2,202,&cancel,3100));
    CHECK(d[0].state==COOP_DUEL_INTERRUPTED && !d[0].ready[0] && !d[0].ready[1]);
    puts("PASS: leave, revoke, relay loss, menu/chat, save/area/kit generations, fresh-at-apply and zero-work off path");
}
static void menu_tests(const char *path) {
    menu_offer.rules=rules(); menu_offer.accepted=0; menu_offer.presence_accepted=1;
    menu_offer.token=55; strcpy(menu_offer.maker_key,"0000-0000-0000-0021");
    presentation_rules=menu_offer.rules; presented_members=1u<<2; // the live owner returns remote members only
    menu_view.state=COOP_JOINED; menu_view.n=2; menu_view.self=1; menu_view.session=101;
    menu_view.member[0].id=1; menu_view.member[0].session=101; menu_view.member[1].id=2; menu_view.member[1].session=202;
    MkwVkCoopView out={0}; setting("HOS_COOP_HITS_TRACE",NULL);
    hos_coop_duel_menu_view(&out,&menu_view,&menu_offer); CHECK(!out.duel_diagnostic); CHECK(!hos_coop_duel_menu_ready("x",3));
    setting("HOS_COOP_HITS_TRACE","0"); hos_coop_duel_menu_view(&out,&menu_view,&menu_offer); CHECK(!out.duel_diagnostic);
    CHECK(!presentation_queries && !kit_queries && !chat_queries && !menu_queries);
    setting("HOS_COOP_HITS_TRACE","1"); hos_coop_duel_menu_view(&out,&menu_view,&menu_offer);
    CHECK(out.duel_diagnostic && strlen(out.duel_digest)==64 && strstr(out.duel_note,"cannot start a fight"));
    CHECK(strstr(out.duel_terms,"10 duel hearts") && strstr(out.duel_terms,"best of 3"));
    CHECK(presentation_queries==1); // copy presentation works without ordinary progress acceptance
    CHECK(!hos_coop_duel_menu_ready(out.duel_digest,2)); setting("HOS_COOP_ARENA_FILE",NULL);
    CHECK(!kit_queries && !chat_queries && !menu_queries);
    CHECK(!hos_coop_duel_menu_ready(out.duel_digest,3));
    CHECK(kit_queries==1 && strstr(menu_log,"no valid local surveyed arena descriptor"));
    const unsigned kit_before=kit_queries, chat_before=chat_queries, menu_before=menu_queries;
    chat_input=1;
    CHECK(!hos_coop_duel_menu_ready(out.duel_digest,3));
    CHECK(chat_queries==chat_before+1 && menu_queries==menu_before && kit_queries==kit_before);
    CHECK(strstr(menu_log,"menu or chat owns input"));
    chat_input=0; menu_input=1;
    CHECK(!hos_coop_duel_menu_ready(out.duel_digest,3));
    CHECK(chat_queries==chat_before+2 && menu_queries==menu_before+1 && kit_queries==kit_before);
    CHECK(strstr(menu_log,"menu or chat owns input"));
    menu_input=0; kit_ready=0;
    CHECK(!hos_coop_duel_menu_ready(out.duel_digest,3));
    CHECK(kit_queries==kit_before+1 && strstr(menu_log,"kit is not verified"));
    kit_ready=1;
    CoopArena a=arena(); uint8_t bytes[COOP_ARENA_BYTES];
    CHECK(coop_arena_pack(bytes,sizeof bytes,&a)==sizeof bytes);
    FILE *f=fopen(path,"wb"); CHECK(f); CHECK(fwrite(bytes,1,sizeof bytes,f)==sizeof bytes); CHECK(!fclose(f));
    setting("HOS_COOP_ARENA_FILE",path);
    CHECK(!hos_coop_duel_menu_ready(out.duel_digest,3));   // valid private data never invents a native capability
    CHECK(kit_queries==kit_before+2 && strstr(menu_log,"native sword contact, damage, shield and arena safety are not proven"));
    menu_view.member[1].session++;
    CHECK(!hos_coop_duel_menu_ready(out.duel_digest,3));
    CHECK(kit_queries==kit_before+2 && strstr(menu_log,"participant sessions changed"));
    menu_view.member[1].session--;
    setting("HOS_COOP_ARENA_FILE",NULL);
    presentation_ready=0; menu_offer.accepted=1;
    memset(&out,0,sizeof out); hos_coop_duel_menu_view(&out,&menu_view,&menu_offer);
    CHECK(!out.duel_digest[0] && strstr(out.duel_note,"Accept"));
    presentation_ready=1; menu_offer.accepted=0; presentation_rules.epoch++;
    memset(&out,0,sizeof out); hos_coop_duel_menu_view(&out,&menu_view,&menu_offer);
    CHECK(!out.duel_digest[0] && strstr(out.duel_note,"Accept")); // a stale live charter cannot authorize the preview
    presentation_rules.epoch--; temporary=0;
    memset(&out,0,sizeof out); hos_coop_duel_menu_view(&out,&menu_view,&menu_offer);
    CHECK(!out.duel_digest[0] && strstr(out.duel_note,"temporary save copy"));
    temporary=1;
    for (int self=1; self<=2; self++) {
        menu_view.self=self; menu_view.session=(uint64_t)self*101; presented_members=1u<<(3-self);
        memset(&out,0,sizeof out); hos_coop_duel_menu_view(&out,&menu_view,&menu_offer);
        CHECK(strlen(out.duel_digest)==64); // neither roster position needs a remote grant for self
        presented_members=0;
        memset(&out,0,sizeof out); hos_coop_duel_menu_view(&out,&menu_view,&menu_offer);
        CHECK(!out.duel_digest[0] && strstr(out.duel_note,"Exactly two"));
    }
    presented_members=1u<<1; menu_view.member[0].away=1;
    memset(&out,0,sizeof out); hos_coop_duel_menu_view(&out,&menu_view,&menu_offer);
    CHECK(!out.duel_digest[0] && strstr(out.duel_note,"Exactly two"));
    menu_view.member[0].away=0;
    menu_offer.accepted=1; menu_view.state=COOP_OFF; memset(&out,0,sizeof out); hos_coop_duel_menu_view(&out,&menu_view,&menu_offer);
    CHECK(!out.duel_diagnostic); setting("HOS_COOP_HITS_TRACE",NULL);
    puts("PASS: live presentation/epoch/participant and independent kit readiness, both input fences, diagnostic native-capability refusal; switch absent/0 and co-op off stay closed");
}
int main(int argc,char **argv) {
    CHECK(argc==2); descriptors(argv[1]); ready_tests(); ledger_tests(); interruptions(); menu_tests(argv[1]);
    printf("PASS: %u duel fixture assertions; %u trusted local observer calls\n",checks,observed); return 0;
}
