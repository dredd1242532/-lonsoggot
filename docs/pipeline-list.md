# Send your pipeline list

The first time the game meets a pipeline it builds it on the spot, which stutters. Releases build a list of known
pipelines before the title, and every player's play adds to it. To send yours:

1. In the game, open the menu (Insert or F1), go to the **Debug** tab and choose **Save my pipeline list** (under Developer). Or,
   from a clone, run `python -I nx2/pipeline_share.py <game folder>`.
2. It saves `longshot-pipelines-<date>.zip` in your Downloads folder, copies a filled-in report to the clipboard and
   opens the [Submit your pipelines](https://git.magicbean.dev/MagicBeanDev/Longshot/issues/14) issue in your browser.
3. Attach the zip to a comment there, paste the report and fill in where you played.

The zip holds only `longshot_pipeline_states.bin` (shader hashes, formats and draw state, and the hashes of the compute
programs your play ran, so that a release can keep those ready too: no game code, game data or personal info) and the
report (the version, OS, GPU and driver, output and render resolution, upscaler, frame
generation, HDR and the mods the game loaded). Nothing is uploaded for you. If the list is missing or damaged, it says
so and saves nothing. In a run without a window (`HOS_HEADLESS`) the menu saves the zip but opens nothing.
