"""POS2/3 must refuse each other rather than misread the richer tail.
The frozen version-2 parser below is the production parser at bfc7a99b, with only its symbol renamed.
It uses unchanged wire scalar helpers from the current production translation unit.
"""
import argparse
import os
from pathlib import Path
import subprocess
import tempfile
ROOT=Path(__file__).resolve().parents[1]
RUN_ALL_ARGS=['--cc','gcc']
C=r'''
#define _CRT_SECURE_NO_WARNINGS
#include "coop_proto.c"
#include <assert.h>
#include <stdio.h>
static int legacy_parse(const uint8_t *p, size_t n, CoopPos *out) {
    if (n < POS_HEAD || p[0] != 2 || p[10] > COOP_POS_TEXT || n < POS_HEAD + p[10] + 17u ||
        !printable(p + POS_HEAD, p[10]))
        return 0;
    CoopPos r;
    memset(&r, 0, sizeof r);
    r.pseq = get32(p + 1), r.t_ms = get32(p + 5), r.flags = p[9];
    memcpy(r.area, p + POS_HEAD, p[10]);
    size_t at = POS_HEAD + (size_t)p[10];
    int finite = 1;
    for (int k = 0; k < 3; k++) finite &= getf(p + at + 4 * k, &r.pos[k]);
    finite &= getf(p + at + 12, &r.yaw);
    r.nslots = p[at + 16], at += 17;
    if (!finite || r.nslots > COOP_POS_SLOTS) return 0;
    for (int i = 0; i < r.nslots; i++) {
        if (at >= n || p[at] > COOP_POS_TEXT || at + 1 + p[at] + 4 > n || !printable(p + at + 1, p[at]) ||
            !getf(p + at + 1 + p[at], &r.slot[i].frame))
            return 0;
        memcpy(r.slot[i].node, p + at + 1, p[at]);
        at += 1u + p[at] + 4;
    }
    if (at != n) return 0;
    *out = r;
    return 1;
}
int main(void) {
    CoopPos pose={.pseq=42,.t_ms=200,.flags=COOP_POS_IN_WORLD,.area="Fixture",.pos={1,2,3},.nslots=1,
                  .slot={{"Walk",2}},.health_q=9,.max_health_q=12},out;
    uint8_t current[COOP_MAX_MESSAGE],old[COOP_MAX_MESSAGE];
    const size_t n=coop_pos_pack(current,sizeof current,&pose);
    assert(n && coop_pos_parse(current,n,&out) && !legacy_parse(current,n,&out));
    // Seven bytes follow the old slots: three gear bytes, then four health bytes.
    memcpy(old,current,n-7);old[0]=2;
    assert(legacy_parse(old,n-7,&out) && out.pseq==42 && out.nslots==1 && out.slot[0].frame==2);
    assert(!coop_pos_parse(old,n-7,&out));
    old[0]=COOP_POS_VERSION;assert(!coop_pos_parse(old,n-7,&out));
    current[0]=2;assert(!legacy_parse(current,n,&out));
    puts("PASS POS versions: frozen v2 accepts v2/refuses v3; current v3 accepts v3/refuses v2; changing only the version cannot bypass length checks");
}
'''
def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--cc',default=os.environ.get('CC','gcc'));a=p.parse_args()
    with tempfile.TemporaryDirectory(prefix='coop-pos-versions-') as tmp:
        d=Path(tmp);(d/'test.c').write_text(C)
        subprocess.run([a.cc,'-std=gnu11','-Wall','-Wextra','-Werror','-I'+str(ROOT/'host'),str(d/'test.c'),str(ROOT/'third_party/monocypher/monocypher.c'),'-o',str(d/'test')],check=True)
        subprocess.run([str(d/'test')],check=True)
if __name__=='__main__':main()
