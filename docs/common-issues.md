# Common issues

Use these checks to gather useful information, then search [Longshot reports](https://git.magicbean.dev/MagicBeanDev/Longshot/issues).
A similar report may help, but it does not establish the cause on your machine.

Need a hand? [Meet us on Discord](https://discord.gg/KX99srmQTe) to ask a question and compare notes with other players.

## The build rejects the game version

Compare your copy with the [supported requirements](../README.md#what-you-need).
Record the exact builder message and selected version. Do not share game files.

## The game closes or shows a black screen

Check the installed graphics driver and documented Vulkan requirements.
Keep `longshot_log.txt`; on Linux, look for `longshot_startup.log` if the game never reached normal logging.
Include the GPU, driver, OS, game version and point of failure in your report.

## Graphics are missing or corrupted

Record the location and the affected objects. Check for `longshot_damaged_files.txt`, which can identify incomplete source files.
Compare with the [graphics guidance](graphics-and-controls.md) and current reports before changing options.

## The controller behaves differently than expected

Check Longshot's selected layout and the game's bindings. Include the controller model, connection method
and whether the mismatch occurs in gameplay, menus or a specific action.

## Performance drops or stutters

Note whether it happens on a first visit, every visit or only after changing areas.
Follow the [comparison steps](../README.md#low-fps-or-stutter) and include the relevant log.

## An update or save transfer failed

Stop before overwriting another copy. Check [save locations and backups](saves.md), then report the exact steps
and version. Keep your save files private.

## A download is flagged

Read the [download safety guidance](/Longshot/antivirus/). A warning needs investigation; it is not proof either way.
