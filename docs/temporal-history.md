# Temporal history validity

DLSS and FSR4 use the shared temporal chain; FSR3.1 prepares its own inputs. If camera reprojection fails, motion vectors are unavailable and the previous picture has no valid mapping to the current one. Each path must deliver a reset in that same frame rather than retaining unusable history.

Validate both input matrices and each result coefficient before converting to float. A finite double outside the float range is rejected before conversion. Preserve the existing algorithm, singularity threshold and age/size rules: valid consecutive camera motion retains history. This adds no camera-cut heuristic or interface change.

The game-free fixture compiles actual reprojection math, both preparation policies, the shared frame reset and all three consumer assignments (DLSS, FSR4, FSR3.1). It covers singular and non-finite inputs, float overflow/boundaries, recovery, frame gaps, FSR3.1 size changes and valid perspectives at16:9 and ultrawide. Engine SDKs and a GPU are not required. CPU cases do not prove in-game frequency, visual gains or GPU handling of injected invalid matrices.

## PsychoV35 with DLSS/DLAA and FSR 4 (opt-in)

`RX_VK_PSYCHOV35_DLSS=1` lets NVIDIA DLSS/DLAA and AMD FSR 4 (and its Native AA) take the Native HDR PsychoV35 scene, whose gameplay network input and output are RGBA16F (`upscaler_rows.h` `takes_psychov35`: the engines the chain gives linear light in an HDR context). It is off by default. Vanilla's packed B10G11R11 route is unchanged; this switch does not enable PsychoV35 for FSR 3.1 or TAAU.

The pre-existing Upscaler Off stretch also only writes packed output, so Off with PsychoV35 still falls back to the
game's network, even at 100%. It must not be labelled native with no anti-aliasing in a benchmark. This
switch does not change that route; Vanilla with Off and 100% provides the true native, no-upscaling comparison.

The network's perceptual colour encoding still needs the existing HDR prep square to linear light, even though the scene tone-map pass itself writes linear colour. Storage precision does not determine that encoding. The typed RGBA16F output pass writes the engine's linear result without squaring again. FSR 4 takes the same pair: its context is made for HDR, linear colour while the game presents PQ (`vk_fsr4.cpp`), and the route waits for that context as DLSS's waits for its HDR feature (`RX_VK_FSR4_HDR=0` prevents it). Changing the tone mapper or input format resets history.

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
established the native-HDR DLSS encoding; its author confirmed that it landed through
[Hookshot #2342](https://github.com/NotRexed/Hookshot/pull/2342). Removing the format
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
frame-generation multiplier. Verify the HDR feature creation, the log line `vk: PsychoV35 DLSS: RGBA16F linear route`
with network input and output format 97, repeated DLSS drawn-frame counts and `DLSS Frame Gen (NGX)` made-frame counts.
Compare against the switch off and Vanilla in the same scene, then switch tone mappers and DLSS/DLAA during play. Check
brightness on an HDR display, edges, actor motion, history resets and validation errors. With FSR 4 or its Native AA, the log
line is `vk: PsychoV35 FSR 4: RGBA16F linear route`, the status lines count `AMD FSR 4 ...: N frames drawn`, and no
`the game's upscale runs` follows; there is no NVIDIA frame generation to check. A transitional packed-format
frame after a hot switch is insufficient evidence; a menu selection alone is insufficient too. Record measured runs in
`tested-configurations.md`.

## Native HDR and FSR 3.1 frame generation

FSR 3.1 interpolation prepares its own scene depth and camera motion, independently of the shared upscaler chain.
`RX_VK_FSR_FG=1` asks for its midpoint at Frame generation 2x. Native HDR PsychoV35 also needs the existing
`RX_VK_PSYCHOV35_DLSS=1` opt-in to accept matching RGBA16F network buffers. Known and shape-detected networks use
matching sampled/storage views; mixed formats and unrelated dispatches are rejected. This does not enable the legacy
FSR 3.1 upscaler, which still needs its separate compatibility work. No new default or environment switch is added.

The present copied into frame generation can contain PQ codes despite its UNORM capture storage. Pass the captured
present's native-PQ flag from both presentation paths; give the same transfer function and peak luminance to the AMD
frame-interpolation and optical-flow constant blocks. SDR retains transfer 0 and its unused 0..1 range; native PQ uses
transfer 1 and 0..the selected HDR peak in nits. This corrects Native HDR metadata for Vanilla too. The SDK fields are
documented in [AMD's frame-interpolation guide](https://gpuopen.com/manuals/fidelityfx_sdk/techniques/frame-interpolation/).
No vendored shader or constant-block layout changes are needed.

Scene input format or tone-map changes reset depth/motion history. Encoding or PQ peak changes, frame gaps, cuts and
resizes reset interpolation history, so frames in different colour spaces are never blended. A reset frame keeps the
new source for the next interval but produces no AMD midpoint; existing fallback remains in effect for that frame.

Validate Native HDR PsychoV35 at 67/83/100%, Vanilla HDR and SDR, an HDR-peak change, FG Off/on, and camera movement.
On NVIDIA hardware, disable NVIDIA FG to isolate FSR. Inspect `fsr3fi` generated-frame and missing-input counts, the
PQ/peak diagnostic and actual generated captures; a selected menu row proves none of these. Record GPU/driver/OS,
scene and exact commits. These are functional checks, not an uncapped performance measurement or calibrated HDR proof.

Existing limitations remain: a single midpoint at 2x only, camera-derived motion without the shared chain's actor
vectors, and no HUD-less colour. HUD smearing/ghosting can remain, so FSR generation stays opt-in; 3x and higher use
the renderer's existing generation path. This change does not solve the separate general scene-transition flicker.
