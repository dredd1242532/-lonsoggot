# Temporal history validity

DLSS and FSR4 use the shared temporal chain; FSR3.1 prepares its own inputs. If camera reprojection fails, motion vectors are unavailable and the previous picture has no valid mapping to the current one. Each path must deliver a reset in that same frame rather than retaining unusable history.

Validate both input matrices and each result coefficient before converting to float. A finite double outside the float range is rejected before conversion. Preserve the existing algorithm, singularity threshold and age/size rules: valid consecutive camera motion retains history. This adds no camera-cut heuristic or interface change.

The game-free fixture compiles actual reprojection math, both preparation policies, the shared frame reset and all three consumer assignments (DLSS, FSR4, FSR3.1). It covers singular and non-finite inputs, float overflow/boundaries, recovery, frame gaps, FSR3.1 size changes and valid perspectives at16:9 and ultrawide. Engine SDKs and a GPU are not required. CPU cases do not prove in-game frequency, visual gains or GPU handling of injected invalid matrices.

## PsychoV35 with DLSS/DLAA (opt-in)

`RX_VK_PSYCHOV35_DLSS=1` lets NVIDIA DLSS/DLAA take the Native HDR PsychoV35 scene, whose gameplay network input and output are RGBA16F. It is off by default. Vanilla's packed B10G11R11 route is unchanged; this switch does not enable PsychoV35 for FSR or TAAU.

The network's perceptual colour encoding still needs the existing HDR prep square to linear light, even though the scene tone-map pass itself writes linear colour. Storage precision does not determine that encoding. The typed RGBA16F output pass writes DLSS's linear result without squaring again. Changing the tone mapper or input format resets history.

Regenerate the output variant with glslang 16.6.0:

```sh
python -I -B tests/test_psychov35_dlss.py --write --glslang glslang --cxx clang++
```

The existing packed output header is unchanged. `tests/test_psychov35_dlss.py` checks the opt-in format pairs, the same policy at camera/actor/reactive consumers, history resets, HDR prep selection and the compiled output's RGBA16F storage image type.

### Hookshot review and NVIDIA frame generation

This path adapts [Hookshot #2707](https://github.com/NotRexed/Hookshot/pull/2707),
head `f1d654ce6f841fd4ce5bcbbe8900629a5394e484`, by nooblikejagger. It is an open draft at the time of review.
Its author reports a successful RTX 4070 SUPER HDR-display test for DLSS, but no controlled performance comparison
or NVIDIA frame-generation measurement. Those results do not validate this Longshot port.

[Hookshot #2481](https://github.com/NotRexed/Hookshot/pull/2481) introduced PsychoV35 and deliberately narrowed
RGBA16F to Native HDR plus PsychoV35. Its discussion confirms that selecting DLSS and frame generation in the menu
did not prove they ran: the log revealed the format rejection and fallback. [Hookshot #2243](https://github.com/NotRexed/Hookshot/pull/2243)
established the native-HDR DLSS encoding; its author confirmed that it landed through #2342. Removing the format
check alone would leave a packed-format storage shader bound to an RGBA16F image. Skipping the prep decode because
storage is floating point would instead change brightness.

[Hookshot #2118](https://github.com/NotRexed/Hookshot/pull/2118), merged, supplies the NGX frame-generation path
already in Longshot. It takes the temporal chain's inputs and logs successful and fallback sets every 600 presents.
In Longshot, `temporal_upscale` calls `dlss_fg_inputs` after a successful evaluation and output dispatch. That function
copies the frame's depth and motion and carries its camera and history reset; it does not inspect the scene colour format.
When the chain rejects RGBA16F, no new inputs reach it and `dlss_fg_frames` falls back with `FG_SKIP_INPUTS`.
Accepting the scene restores this handoff. Existing GPU/runtime support, fixed-multiplier, gap and maximum-frame checks
still apply; accepting RGBA16F does not promise that every present can use NVIDIA frame generation.

Runtime validation must start cold with Native HDR, PsychoV35, DLSS/DLAA, the switch above and a supported fixed NVIDIA
frame-generation multiplier. Verify the HDR feature creation, `PsychoV35 DLSS: enabled` with input/output format 97,
repeated DLSS drawn-frame counts and `DLSS Frame Gen (NGX)` made-frame counts. Compare against the switch off and
Vanilla in the same scene, then switch tone mappers and DLSS/DLAA during play. Check brightness on an HDR display,
edges, actor motion, history resets and validation errors. A transitional packed-format frame after a hot switch is
insufficient evidence; a menu selection alone is insufficient too. Record measured runs in `tested-configurations.md`.

### Local verification, 2026-11-12

Renderer: `d4a69456` plus this port, build source stamp `878c957c`; host: unchanged `7afe6ae1`
(`v2.1.0-rc.3-328-g7afe6ae1`, source `7cfff061`). The renderer API and scene-format host hook agree between those
bases. Windows 11 Home build 26300, RTX 4070 12 GB, NVIDIA 621.01, i7-13700F, 32 GB RAM. Copied Hyrule Field child
save, 2560x1440 output (2560x1421 client), DLSS Quality 1707x960, preset K, sharpness 0, Native HDR, PsychoV35,
200-nit paper white, 400-nit peak, NVIDIA frame generation 2x. Both NVIDIA runtimes were installed locally through
`scripts/ngx_sdk.py` with the player's explicit licence acceptance.

Four cold starts, on / off / off / on, each with 30 seconds standing after the map settled. The off arm uses the same
renderer with `RX_VK_PSYCHOV35_DLSS=0`, retaining main's packed-only rejection. These are correctness measurements;
they are not an FPS comparison or a claim about performance on another GPU.

| Steady gameplay interval | Switch off, both runs | Switch on, both runs |
|---|---|---|
| Native HDR DLSS feature | Not made | Made, HDR linear colour |
| Network input/output | RGBA16F rejected | RGBA16F accepted (format 97) |
| DLSS evaluation | Game's network runs | 600 frames drawn per complete status interval |
| NVIDIA FG sets made per 600 presents | 0 | 600 |
| Missing-input FG fallbacks in those intervals | 600 | 0 |

The enabled logs also show actor motion (37,800 draws in one complete 600-frame interval). Live Vanilla -> PsychoV35
switching returns to the RGBA16F route, with history resets and complete 600/600 FG intervals after settling. No device
lost or failure to make the RGBA16F shader was logged. Local PNGs were captured for both arms; they contain PQ values,
so an SDR image viewer is not a brightness assessment on an HDR monitor. Absolute brightness/colour judgement,
Linux, other NVIDIA GPUs/drivers and reactive-mask runtime behaviour remain unverified.

A further cold start with `RX_VK_DLSS=2` verifies DLAA at 2560x1440: RGBA16F input/output, an HDR feature,
repeated 600/600 DLAA evaluations and 600/600 NVIDIA FG sets with no missing-input fallbacks in steady gameplay.
Live DLSS/DLAA switching was not verified: the control bridge rejected both `dlss=2` and `resolution=1`; the menu's
Native step changes the upscaler while preserving its saved render-resolution value. The failed automation request
did not change the game setting. A separate extra DLSS run repeated the successful tone-mapper transitions.

The renderer build compiled 207 files and linked with NGX, zero failures. The focused PsychoV35, HDR encoding,
reactive-mask and temporal-history checks pass; the new format-policy fixture fails on the original source. The packed
output header regenerates byte-for-byte identically with glslang 16.6.0. The whole `tests/run_all.py` run gives 479
passed, 10 failed, 41 skipped. Eight failures reproduce in the unchanged `d4a69456` checkout (`test_bake_ds`,
`test_bake_portability`, `test_control`, `test_fpx_sticky`, `test_lift_cache`, `test_nup_paths`, `test_spawn_emit`,
`test_team_chat`); `test_linux_play_scope` hangs there too. `test_oot_wide_save_picture` timed out compiling under
the parallel suite's load and passes alone on both trees. No checks were weakened or skipped to obtain these results.
