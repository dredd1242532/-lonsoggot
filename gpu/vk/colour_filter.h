// Linear-RGB daltonization: I + E * (I - S), not the deficiency simulation S.
// S: Machado, Oliveira and Fernandes (2009), severity 1.0 matrices:
// https://www.inf.ufrgs.br/~oliveira/pubs_files/CVD_Simulation/CVD_Simulation.html
// E redistributes the lost red/green signal to green/blue (Fidaner et al.'s
// error redistribution); for tritanopia it redistributes blue to red/green.
#pragma once
static inline void colour_filter_matrix(int mode, float out[9]) {
    static const float simulation[3][9] = {
        {0.152286f, 1.052583f, -0.204869f, 0.114503f, 0.786281f, 0.099216f, -0.003882f, -0.048116f, 1.051998f},
        {0.367322f, 0.860646f, -0.227968f, 0.280085f, 0.672501f, 0.047414f, -0.011820f, 0.042940f, 0.968880f},
        {1.255528f, -0.076749f, -0.178779f, -0.078411f, 0.930809f, 0.147602f, 0.004733f, 0.691367f, 0.303900f}
    };
    const float red_green[9] = {0,0,0, .7f,1,0, .7f,0,1};
    const float blue[9] = {1,0,.7f, 0,1,.7f, 0,0,0};
    for (int i = 0; i < 9; i++) out[i] = i % 4 == 0 ? 1.0f : 0.0f;
    if (mode < 1 || mode > 3) return;
    const float *s = simulation[mode - 1], *e = mode == 3 ? blue : red_green;
    for (int r = 0; r < 3; r++) {
        for (int c = 0; c < 2; c++)
            for (int k = 0; k < 3; k++) out[r * 3 + c] += e[r * 3 + k] * ((k == c ? 1.0f : 0.0f) - s[k * 3 + c]);
        // Published coefficients are rounded: enforce the achromatic axis exactly.
        out[r * 3 + 2] = 1.0f - out[r * 3] - out[r * 3 + 1];
    }
}
