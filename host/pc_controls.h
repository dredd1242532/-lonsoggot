/* Optional keyboard gameplay actions and the menu bridge (renderer API 22). */
#pragma once
#include "../gpu/vk/vk_api.h"
MkwVkPcControls *hos_pc_controls(void);
uint64_t hos_pc_game_call(void *ctx, uint64_t entry, const uint64_t args[5]);
int hos_pc_mouse(int *x,int *y,int *w,int *h,uint32_t *pressed,int *scroll);
void hos_pc_menu_focus(uint64_t screen);
void rx_pc_menu_tick(void *ctx,uint64_t screen);
