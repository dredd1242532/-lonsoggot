# Common issues

## What’s going wrong?

Pick the problem that looks familiar. Start with the short checklist, then open **Still stuck?** if you need a hand.
One step at a time. Let’s get this bean back on track.

[Search reported issues](/Longshot/progress/#problem-search) · [Ask the beans on Discord](https://discord.gg/KX99srmQTe)

## The build rejects the game version

### Try this first

1. **Check the selected game.** Make sure the builder is pointed at the copy you meant to use, with its update installed.
2. **Compare the version.** Check it against the [supported game requirements](../README.md#what-you-need). A different version will not build.
3. **Use the setup guide for your platform.** Follow the [Windows or Linux steps](getting-started.md) with the supported copy.

### Still stuck?

Keep the exact builder message and the version you selected. Include them when asking for help; do not share game files.

[Search installation reports](/Longshot/progress/#problem-search?q=version) · [Ask on Discord](https://discord.gg/KX99srmQTe)

## The game closes or shows a black screen

### Try this first

1. **Check your graphics card.** Compare it with the [graphics requirements](../README.md#what-you-need), including Vulkan 1.3 and mesh shaders.
2. **Update the graphics driver.** Use the driver for your card; on Linux, check your Mesa or NVIDIA driver. An older unsupported card cannot gain the required hardware features from an update.
3. **Match the symptom.** Use the [startup troubleshooting reference](../README.md#if-something-goes-wrong) for a specific error, such as “device lost”.

### Still stuck?

Keep `longshot_log.txt` from the game folder. If Linux Builder Play exits before that log appears, check
`longshot_startup.log` after the game exits. If you started from a terminal, keep its output instead.
Tell us your GPU, driver, OS and game version, and whether it closes before the menu or after loading a save.

[Search crash reports](/Longshot/progress/#problem-search?q=crash) · [Search black-screen reports](/Longshot/progress/#problem-search?q=black+screen) · [Ask on Discord](https://discord.gg/KX99srmQTe)

## Graphics are missing or corrupted

### Try this first

1. **Check for damaged files.** Look for `longshot_damaged_files.txt` in the game folder. If it lists damaged files, make a fresh dump from your own game and rebuild from that copy.
2. **Check the driver and requirements.** Missing mesh-shader support can leave parts of the picture out. Compare your setup with the [graphics requirements](../README.md#what-you-need).
3. **Change one thing at a time.** Use the [graphics settings guide](graphics-and-controls.md) to understand an option before trying it. For AMD texture problems, read the [documented workaround and its memory cost](../README.md#amd-texture-workaround) first.

### Still stuck?

Note the location and what looks wrong: a missing wall, a texture, lighting or another object. Keep a screenshot and
the relevant log. Search for the same symptom before trying an unrelated workaround.

[Search graphics reports](/Longshot/progress/#problem-search?q=graphics) · [Ask on Discord](https://discord.gg/KX99srmQTe)

## The controller behaves differently than expected

### Try this first

1. **Check the layout.** Press **F1**, open **Controls → Controls**, and check **Layout preset**. Nintendo puts A on the right face button; Xbox / PC puts it on the bottom.
2. **Check the button icons.** On that same Controls page, **Button prompts** chooses the icons shown in the game. Check the bindings too; the icons alone do not change what a button does.
3. **Compare the game’s own settings.** Longshot’s bindings and the game’s controls both matter. Use the [controls reference](../README.md#controls) for remapping steps.

### Still stuck?

Include the controller model, whether you use USB or Bluetooth, the selected layout, and the button or action that
behaves differently. Say whether it happens in gameplay, menus or both.

[Search controller reports](/Longshot/progress/#problem-search?q=controller) · [Ask on Discord](https://discord.gg/KX99srmQTe)

## Performance drops or stutters

### Try this first

1. **Try the same spot twice.** After shader preparation finishes, walk through the area again. First-visit shader work can cause stutter; note whether it improves on the second pass.
2. **Keep the comparison fair.** Use the same resolution, upscaler and frame cap. Turn off **Frame generation** while comparing so the FPS reflects frames the game renders.
3. **Check the right guide.** Use [graphics settings](graphics-and-controls.md) for the options, or the [performance checklist](../README.md#low-fps-or-stutter) for a slowdown that keeps happening.

### Still stuck?

Keep the log from the slow run. Note the area, settings, and whether it happens on a first visit, every visit or only
after changing areas. If it started after an update, include both version numbers and an earlier log if you have one.

[Search stutter reports](/Longshot/progress/#problem-search?q=stutter) · [Search performance reports](/Longshot/progress/#problem-search?q=performance) · [Ask on Discord](https://discord.gg/KX99srmQTe)

## An update or save transfer failed

### Try this first

1. **Pause before copying again.** Close the game and keep a separate copy of the saves you want to protect. Do not overwrite another copy while checking what happened.
2. **Check the source and destination.** Confirm the build, profile and save slot you meant to use. The [save-transfer guide](saves.md#easy-beans) walks through the launcher’s buttons.
3. **Choose the right next step.** For an update problem, follow [updates and saves](updates-and-saves.md). For an unwanted transfer, use the [backup recovery steps](saves.md#need-to-undo-a-transfer).

### Still stuck?

Describe the steps you took, the versions involved and any error message. Keep the original and backup copies until
the expected save loads correctly. Keep save files private.

[Search save reports](/Longshot/progress/#problem-search?q=save) · [Search update reports](/Longshot/progress/#problem-search?q=update) · [Ask on Discord](https://discord.gg/KX99srmQTe)

## A download is flagged

### Try this first

1. **Check where it came from.** Compare the download with the [team’s listed releases](https://git.magicbean.dev/MagicBeanDev/Longshot/releases). If no release is listed, do not assume another download is official.
2. **Keep the warning details.** Note the download URL, filename, version and detection name.
3. **Follow the download safety guide.** [Check the warning before continuing](/Longshot/antivirus/). Do not disable protection or add broad exclusions for an unverified file.

### Still stuck?

Ask the team about the source and detection. A warning alone does not tell you whether a file is safe or malicious.
Share the warning details, not game files or keys.

[Search download reports](/Longshot/progress/#problem-search?q=antivirus) · [Ask on Discord](https://discord.gg/KX99srmQTe)
