"""POS routing uses only session-bound accepted unicasts, including queued revocation.
Runs the production session, codec and UDP sender without a game or relay process.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
RUN_ALL_ARGS = ["--cc", "gcc"]
C = r'''
#include "coop.c"
#include "coop_relay.h"
#include "coop_players.h"
#include <assert.h>
void hos_log(const char *fmt, ...) { (void)fmt; }
int hos_ini_option(const char *key, int otherwise) { (void)key; return otherwise; }
int hos_ini_text(const char *key, char *out, size_t n) { (void)key; (void)out; (void)n; return 0; }
static unsigned drain(CoopUdp *relay, CoopSess *s, unsigned expected, size_t *bytes) {
    unsigned seen = 0; int n; uint8_t buf[COOP_MAX_DGRAM]; CoopAddr addr;
    *bytes = 0;
    while ((n = coop_udp_recv(relay, buf, sizeof buf, &addr, 0)) > 0) {
        CoopHdr h; const uint8_t *p; uint8_t kind, clear[COOP_MAX_MESSAGE]; size_t len;
        assert(coop_dgram_parse(buf, n, &h, &p));
        assert(h.dst >= 2 && h.dst <= 8 && !(seen & (1u << h.dst)));
        assert(coop_open(p,h.len,s->msg_key,h.src,h.dst,&kind,clear,sizeof clear,&len));
        assert(kind == COOP_K_POS); CoopPos pose; assert(coop_pos_parse(clear,len,&pose));
        seen |= 1u << h.dst; *bytes += n;
    }
    assert(seen == expected); return seen;
}
int main(void) {
    CoopParams p = {.name="fixture"}; CoopSess *s = coop_sess_new(&p);
    CoopUdp *relay = coop_udp_open(0); assert(s && relay);
    s->sock=coop_udp_open(0); assert(s->sock);
    assert(coop_net_resolve("127.0.0.1",coop_udp_port(relay),&s->relay));
    s->relay_known=1; s->room=1; s->self=1; s->session=s->room_session=100;
    memcpy(s->secret,"H7K3WMBCDFGH234567",COOP_SECRET_LEN+1);
    coop_room_keys(s->secret,s->locator,s->msg_key);
    s->roster.n=8;
    for (int i=0;i<8;i++) s->roster.m[i].id=i+1,s->roster.m[i].session=100+i;
    set_state(s,COOP_JOINED); publish(s); CoopView v; coop_sess_view(s,&v);
    CoopPos pos={.flags=COOP_POS_IN_WORLD,.nslots=COOP_POS_SLOTS};
    memset(pos.area,'A',COOP_POS_TEXT);
    for (int i=0;i<COOP_POS_SLOTS;i++) memset(pos.slot[i].node,'B',COOP_POS_TEXT);
    uint8_t packed[COOP_MAX_MESSAGE]; size_t len=coop_pos_pack(packed,sizeof packed,&pos), bytes;
    assert(len == 290);   // one integrated version: three gear bytes followed by four health bytes
    assert(!coop_sess_send(s,0,COOP_K_POS,packed,len));
    coop_sess_accept_members(s,&v,1u<<2);
    assert(coop_sess_send(s,0,COOP_K_POS,packed,len)); flush_outbox(s); drain(relay,s,1u<<2,&bytes);
    assert(!coop_sess_send(s,3,COOP_K_POS,packed,len));
    // A queued pose is cancelled even when revoke/reaccept happens before the socket next runs.
    assert(coop_sess_send(s,0,COOP_K_POS,packed,len));
    coop_sess_accept_members(s,&v,0); coop_sess_accept_members(s,&v,1u<<2);
    flush_outbox(s); drain(relay,s,0,&bytes);
    // Acceptance starts the very next update, without a reconnect or new player generation.
    assert(coop_sess_send(s,0,COOP_K_POS,packed,len)); flush_outbox(s); drain(relay,s,1u<<2,&bytes);
    coop_sess_accept_members(s,&v,0x1fc);
    assert(coop_sess_send(s,0,COOP_K_POS,packed,len)); flush_outbox(s); drain(relay,s,0x1fc,&bytes);
    const double rate=1000.0/COOP_PLAYERS_SEND_MS;
    const size_t integrated_bytes=bytes;
    assert(integrated_bytes == 7 * (len + 54));
    assert(7*rate < COOP_RELAY_PACKET_RATE/2.0);
    assert(integrated_bytes*rate < COOP_RELAY_BYTE_RATE/2.0);
    printf("PASS POS privacy: 7 unicasts/update, %.6f updates/s, %.3f datagrams/s, %zu bytes/update, %.3f bytes/s (combined gear/health payload); limits %d/%d\n",
           rate,7*rate,integrated_bytes,integrated_bytes*rate,COOP_RELAY_PACKET_RATE,COOP_RELAY_BYTE_RATE);
    // Download is not presentation; explicit copy presence has the same queued revocation fence.
    coop_sess_accept_members(s,&v,0);
    coop_sess_download_members(s,&v,0x1fc);
    assert(!coop_sess_send(s,0,COOP_K_POS,packed,len));
    coop_sess_presence_members(s,&v,1u<<2);
    assert(coop_sess_send(s,0,COOP_K_POS,packed,len)); flush_outbox(s); drain(relay,s,1u<<2,&bytes);
    assert(coop_sess_send(s,0,COOP_K_POS,packed,len));
    coop_sess_presence_members(s,&v,0); coop_sess_presence_members(s,&v,1u<<2);
    flush_outbox(s); drain(relay,s,0,&bytes);
    assert(coop_sess_send(s,0,COOP_K_POS,packed,len)); flush_outbox(s); drain(relay,s,1u<<2,&bytes);
    // Same member number, new session: neither a queued nor a new pose inherits acceptance.
    assert(coop_sess_send(s,0,COOP_K_POS,packed,len));
    CoopRoster changed=s->roster; for(int i=1;i<8;i++) changed.m[i].session+=100;
    apply_roster(s,&changed); publish(s); flush_outbox(s); drain(relay,s,0,&bytes);
    assert(!coop_sess_send(s,0,COOP_K_POS,packed,len));
    coop_sess_free(s); coop_udp_close(relay);
}
'''

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cc',default=os.environ.get('CC','gcc'))
    ap.add_argument('--mutation',choices=['enqueue','revoke'])
    a=ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='longshot-pos-privacy-') as tmp:
        d=Path(tmp); text=(ROOT/'host/coop.c').read_text()
        if a.mutation=='enqueue':
            text=text.replace('&& (accepted_locked(s, id) || presence_locked(s, id)))','&& id != s->view.self)')
        if a.mutation=='revoke':
            text=text.replace('slot->pos_epoch == s->accepted_epoch[slot->dst]','1')
        (d/'coop.c').write_text(text); (d/'check.c').write_text(C)
        libs=['-lws2_32','-lbcrypt','-liphlpapi'] if os.name=='nt' else ['-pthread','-lm']
        cmd=[a.cc,'-std=gnu11','-D_GNU_SOURCE','-D_CRT_SECURE_NO_WARNINGS','-Wall','-Wextra','-Werror','-I'+str(d),'-I'+str(ROOT/'host'),str(d/'check.c'),str(ROOT/'host/coop_net.c'),str(ROOT/'host/coop_proto.c'),str(ROOT/'host/coop_relay.c'),str(ROOT/'third_party/monocypher/monocypher.c'),'-o',str(d/'check')]+libs
        subprocess.run(cmd,check=True); subprocess.run([str(d/'check')],check=True)

if __name__=='__main__': main()
