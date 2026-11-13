// Co-op projection, ported from coop-players; row-major submitted camera, never live guest memory.
#pragma once
#include <algorithm>
#include <cmath>
namespace mkwvk::coop_overlay_math {
struct Marker { float x, y, dx, dy, text_x, text_y; bool arrow; };
inline bool project(const float m[16], const float p[3], const float viewport[4], const float crop[4],
                    float text_w, float text_h, float margin, bool arrows, Marker& out) {
    for (int i=0;i<4;i++) if (!std::isfinite(viewport[i]) || !std::isfinite(crop[i])) return false;
    if (!(viewport[2]>0 && viewport[3]>0 && crop[2]>0 && crop[3]>0) ||
        !std::isfinite(text_w) || !std::isfinite(text_h) || !std::isfinite(margin) ||
        text_w<0 || text_h<0 || margin<0 || text_w+2*margin>viewport[2] || 2*text_h+2*margin>viewport[3]) return false;
    double c[4];
    for (int r=0;r<4;r++) {
        c[r]=m[4*r+3];
        for (int k=0;k<3;k++) c[r]+=double(m[4*r+k])*p[k];
        if (!std::isfinite(c[r])) return false;
    }
    const double w=c[3];
    double u=0, v=0;
    const bool front=w>1e-6;
    if (front) { u=((c[0]/w+1)*.5-crop[0])/crop[2]; v=((1-c[1]/w)*.5-crop[1])/crop[3]; }
    const bool inside=front && c[2]>=-w && c[2]<=w && u>=0 && u<=1 && v>=0 && v<=1;
    out.arrow=!inside;
    if (inside) {
        out.x=float(viewport[0]+u*viewport[2]); out.y=float(viewport[1]+v*viewport[3]);
        out.dx=out.dy=0;
    } else {
        if (!arrows) return false;
        // Do not divide by negative w: a friend behind-right remains to the right, not mirrored left.
        double dx=c[0]*viewport[2]/crop[2], dy=-c[1]*viewport[3]/crop[3];
        if (front) dx=(u-.5)*viewport[2],dy=(v-.5)*viewport[3];
        if (std::abs(dx)+std::abs(dy)<1e-9) dy=1; // straight behind: turn around, indicated at the bottom
        const double length=std::hypot(dx,dy); dx/=length; dy/=length;
        const double hx=viewport[2]*.5-margin-text_w*.5, hy=viewport[3]*.5-margin-text_h;
        if (!(hx>0 && hy>0)) return false;
        const double t=std::min(std::abs(dx)>1e-9?hx/std::abs(dx):1e30,std::abs(dy)>1e-9?hy/std::abs(dy):1e30);
        out.x=float(viewport[0]+viewport[2]*.5+dx*t); out.y=float(viewport[1]+viewport[3]*.5+dy*t);
        out.dx=float(dx); out.dy=float(dy);
    }
    out.text_x=std::clamp(out.x-text_w*.5f,viewport[0]+margin,viewport[0]+viewport[2]-margin-text_w);
    out.text_y=std::clamp(out.y-text_h-(out.arrow?margin:0),viewport[1]+margin,viewport[1]+viewport[3]-margin-text_h);
    return std::isfinite(out.x) && std::isfinite(out.y);
}
}
