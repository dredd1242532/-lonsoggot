# User-guide screenshots

These images are shared by the canonical Markdown guides and longshot.magicbean.dev. Keep one copy in this
Longshot directory; do not duplicate the images in LongshotLauncher or the website's generated `public/` folder.
`website/src/repo-docs/assets.ts` copies the manifest's images unchanged, and `pages.ts` rewrites guide-relative
links to those public files.

## Provenance

`manifest.json` records each image's dimensions, SHA-256, capture conditions, application release and reviewed UI
source hashes. The launcher captures use the official Linux x86_64 v1.0.0-rc.3 AppImage. Its release target and
binary SHA-256 are recorded separately: a binary hash is not a source commit. The release tag's UI sources are
identified by `reviewed_source`, without claiming a reproducible AppImage build.

The screenshots show Longshot's own UI and branding with the launcher's bundled Inter fonts. The launcher
sources and their license are in [LongshotLauncher](https://git.magicbean.dev/MagicBeanDev/LongshotLauncher);
the font's notice is `App/Assets/Fonts/Inter-LICENSE.txt` there. No game assets, extracted fonts, gameplay,
save data, private paths or keys are included. The application window is captured unchanged, including its
own labels. The selected **Builds** version is release metadata, not evidence that a game was built or run.

## Capture and maintenance

1. Use the official launcher release in an isolated temporary launcher/config/data profile. Never point it at
   a real game or save for a guide capture. Record the release, source commit, binary hash, UI scale and theme.
2. Open the relevant screen. Use empty or clearly synthetic UI state for file/save workflows; do not import,
   export, replace or delete real saves to obtain an illustration.
3. Capture the application window. Inspect the full image for personal paths, identifiers and private data.
   Preserve actual labels and layout; do not compose or replace UI text. Record any capture fixture explicitly.
4. Add the image and a manifest entry here. Add descriptive alt text and a version/platform caption in the
   canonical guide. Keep the written instructions usable without the image.
5. Review the recorded UI source files when their hashes change. A changed hash means the screenshot needs a
   human review, not necessarily a recapture: compare labels, controls and navigation, then update the reviewed
   source revision and hashes or replace the image. Launcher UI changes are reviewed against its separate
   repository. Do not claim a capture is current merely because its PNG hash still matches.
6. Build and check the website using `website/README.md`, then inspect the affected pages at desktop/mobile
   sizes and in light/dark themes. Ensure each image loads from the site's own assets and remains readable.

A UI-source review checker and game-free menu capture fixture are still being developed in this WIP change.
They will make source drift reviewable without running the game or copying game-provided menu fonts.
