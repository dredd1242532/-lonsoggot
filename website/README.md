# Longshot website preview

Astro/Starlight front page and player documentation for Longshot by team magicbean.
The game build is independent of this directory. This branch adds source only: there
is no automatic deployment or publishing workflow.

## Local preview

Use Node 22.23.4 or newer with the pinned lockfile. From `website/`:

```sh
npm ci
npm run build:preview
npm run preview -- --host 127.0.0.1 --port 4321
```

Open http://127.0.0.1:4321/Hookshot/. `npm run dev` provides live editing.
The `/Hookshot/` base is retained so existing preview links keep working.
`DOCS_SITE_URL` sets the canonical HTTPS origin; its default is `https://magicbean.dev`,
which is metadata, not an enabled deployment or a claim that this site is hosted there.

`npm run build:preview` builds and checks with edit links to this fork's
`docs/longshot-website` branch. Normal `npm run build` and `npm run check` also accept
`DOCS_SOURCE_REPOSITORY` and `DOCS_SOURCE_BRANCH` for another Forgejo fork/branch.
Project and issue links use https://git.magicbean.dev/MagicBeanDev/Longshot.

## Design and interaction

The front page has Guides, Development and Help navigation, site search, light/dark
controls, and team credits. The main Grappling Hook Bean becomes the Glowing Fairy
Bean after three clicks, taps or keyboard button activations. Reload resets it.
A failed image load keeps the original artwork and allows a retry.

The team logo, Longshot icon and supplied bean illustrations are copied unchanged
from `../assets/branding/`; their provenance is recorded there. The upstream README's
own `../assets/longshot.png` is preserved separately.

Body text and controls use self-hosted Atkinson Hyperlegible Next, weights 200–800,
upright and italic. Libertinus Serif Semibold remains for large headings; code stays
monospace. Font provenance and original notices are retained in `src/assets/fonts/`
and `public/fonts/`. No external font service or game-extracted fonts are used.

## Content and source guards

`content-sources.mjs` lists the imported pages. Edit their original files in `docs/`,
`README.md` or `CONTRIBUTING.md`, not `src/content/docs/generated/`. Content preparation
copies only the explicitly selected pages and artwork. Other relative references
link to the team's Forgejo source tree; website instructions link to this fork branch. New internal documents are not automatically
published.

The compatibility rules were reviewed against Longshot's requirements and project
status on November 12, 2026. The requirements match the earlier snapshot; GPU caveats
remain applicable. `compatibility-review.json` records hashes of those source sections.
Builds and checks stop if they change. Review the calculator rules, labels and advice
before updating hashes; never refresh them merely to make a build pass.

The checked-in `public/progress-feed.json` is a dated snapshot of public Hookshot
reports, releases and merged changes. Historical links and attribution stay intact.
It does not represent current Longshot activity. The front page renders it at build
time; the progress page periodically reloads the same file. Its existing GitHub
collector can be run deliberately with `npm run progress:sync`, but no collection
schedule or GitHub Pages workflow is installed by this branch. See `PROGRESS.md` for
the inherited feed's schema and editorial rules.

## Validation and limits

`npm run check` verifies generated routes, links, anchors, edit destinations, artwork
bytes, search output, source guards and the website's test suite. `build:preview`
runs both the build and checks. These do not build or launch the game.

Browser access was unavailable during implementation. Rendered desktop/mobile,
keyboard, and light/dark visual checks remain outstanding. Generated HTML and the
compiled Easter-egg script were checked separately; those are not browser checks.
