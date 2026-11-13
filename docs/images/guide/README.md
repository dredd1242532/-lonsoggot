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

## Developer maintenance plan

The UI change author owns screenshot freshness for the controls they change. The PR reviewer checks the image,
written steps and provenance; the documentation maintainer checks the rendered guide before release. A hash match
proves that the recorded files match, not that every possible UI dependency is covered or that the instructions are
correct. Extend the source list when the UI gains a dependency or moves to another file.

| Change | Author's action | Reviewer checks |
|---|---|---|
| Longshot menu labels, layout, defaults or available controls | Run the source review below; inspect the affected guide and recapture if it no longer matches. | Correct visible labels and navigation, platform/capability caveats and version caption. |
| Menu font, ImGui, capture backend or fixture | Rebuild the UI-only fixture and inspect its output; update capture provenance if replacing the PNG. | Only bundled redistributable fonts, real menu code, no game assets, private paths or save data. |
| Launcher screens, navigation, file/save workflow or release | Review from the candidate Launcher checkout with `--launcher-source`; open a companion Longshot documentation PR when needed. | Windows/Linux behavior and exact action names against that release, using empty or synthetic state. |
| Implementation-only change with identical visible behavior | Retain the capture only after comparison; record the newer reviewed revision/hashes and the reason for retaining it. | Evidence that the affected controls and written steps still match; no automatic hash refresh. |

### Offline checks

From the Longshot checkout, using only Python's standard library:

```sh
python -I -B scripts/check_guide_screenshots.py
python -I -B scripts/check_guide_screenshots.py --review-sources
python -I -B scripts/check_guide_screenshots.py --review-sources --launcher-source /path/to/LongshotLauncher
python -I -B tests/test_guide_screenshots.py
```

The default check validates PNG signature/header dimensions and SHA-256, manifest fields, and safe local paths.
`--review-sources` also compares each image's `reviewed_source.files` and optional `fixture_files` against local
files. A mismatch exits nonzero and prints `REVIEW REQUIRED` with the screenshot and source filename. Missing
Launcher source prints **NOT CHECKED**, even when local checks pass. None of these commands downloads a checkout,
builds a game, opens a save, rewrites a hash or approves a screenshot.

`captured_source` (or the launcher's original `release_target_commit` and `binary_sha256`), `captured_at`, image
hash and capture conditions describe the original capture. `reviewed_source` describes the latest human review:
its repository, revision and relevant file hashes. When a newer source still matches an old image, preserve the
original capture fields, update only the review record and explain the comparison in `review.notes` and the PR.
If replacing the image, record the new capture and review together. Keep fixture hashes current only after
reviewing the fixture's effect. Do not copy fresh hashes into the manifest merely to make a failure disappear.

### Automatic Longshot review signal

`.forgejo/workflows/guide-screenshots.yml` defines an offline, read-only check on **every pull request** and each
push to `main`, plus manual dispatch. Checking every PR avoids path-filter gaps when a recorded dependency moves
or a shared header changes. It runs source review and the checker's regression tests; it does not build the fixture
or access game files. The ordinary game-free test runner also discovers `tests/test_guide_screenshots.py`.

This PR adds the workflow definition. Whether its status runs on fork PRs and blocks merging depends on the
repository's runner and branch-protection configuration; this PR does not change those settings. After merging,
a maintainer should verify a test PR with a changed recorded UI source fails, then require **Guide images and
local UI source review** in branch protection if the team wants an enforced merge gate. An unchecked status or
missing runner must not be described as an automatic freshness guarantee.

### Launcher counterpart: planned cross-repository integration

A Longshot-only workflow cannot observe a Launcher-only change. This PR does **not** install a workflow in
LongshotLauncher. Until the counterpart exists, the Launcher UI/release author must run the full command with
`--launcher-source` during UI review and before a release, using the actual proposed release checkout. Record
its commit, output and any companion Longshot docs PR in that review. `NOT CHECKED` is not completion of this step.

The proposed Launcher-side PR/release job checks out the candidate Launcher source and an explicit Longshot docs
revision, then runs the same checker from the Longshot checkout with the candidate path as `--launcher-source`.
It must supply that checkout (and fail if unavailable), retain the affected screenshot filenames in its output,
and link any companion docs PR. The Launcher maintainer owns installing that job and deciding whether its status
is required; the release reviewer confirms it ran against the final release commit. Keep the images in Longshot,
not in a duplicated Launcher asset folder. No remote service, webhook or second PR is created by this change.

### Completing a refresh

Include the changed image/manifest and canonical guide text in one Longshot docs PR. Run the offline checks above,
then the website build/check commands in `website/README.md`. Inspect desktop/mobile and light/dark output,
including image readability, alternative text and adjacent instructions. In the PR, state which UI release and
commit were compared, what was recaptured or retained, the checker result (including external checks skipped),
and the rendered pages inspected. A passing integrity check alone is not a visual review.
