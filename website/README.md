# Longshot website preview

Astro/Starlight front page and player documentation for Longshot by Team MagicBean.
The game build is independent of this directory. This branch adds source only: there
is no automatic deployment or publishing workflow.

## Local preview

Use Node 22.23.4 or newer with the pinned lockfile. From `website/`:

```sh
npm ci
npm run build:preview
npm run preview -- --host 127.0.0.1 --port 4321
```

Open http://127.0.0.1:4321/. `npm run dev` provides live editing.
The project base is `/`, for its own domain (longshot.magicbean.dev).
`DOCS_SITE_URL` sets the canonical HTTPS origin; its default is `https://magicbean.dev`,
which is metadata, not an enabled deployment or a claim that this site is hosted there.

`npm run build:preview` builds and checks with edit links to this fork's
`docs/longshot-website` branch. Normal `npm run build` and `npm run check` also accept
`DOCS_SOURCE_REPOSITORY` and `DOCS_SOURCE_BRANCH` for another Forgejo fork/branch.
Project and issue links use https://git.magicbean.dev/MagicBeanDev/Longshot.

## Design and interaction

The front page has Guides, Help, Development, Team and About navigation, site search, light/dark
controls, and team credits. The main Grappling Hook Bean becomes the Glowing Fairy
Bean after three clicks, taps or keyboard button activations. Reload resets it.
A failed image load keeps the original artwork and allows a retry.
The hero artwork gently bobs over six seconds, matching the main MagicBean homepage.
Hovering or focusing the bean pauses it; reduced-motion preferences disable the sway.
The button and focus outline stay stationary, including after the fairy swap.

The homepage's bean punchline changes once per page load from ten short lines.
Session storage prevents an immediate repeat across reloads in the same tab when
available. Blocked storage still allows a random line; no JavaScript keeps the
original “Slightly emotional beans.” There is no timed rotation or animation.

At narrower widths the main links use an Explore Longshot disclosure, including a
no-JavaScript fallback. With JavaScript, Escape, an outside click or choosing a link
closes it. Shared-memory GPUs have a separate checker choice; their memory remains
a caution rather than being compared to dedicated VRAM. GPU changes update the
automatic choice until the user chooses a memory type themselves; Reset restores it.

The team logo, Longshot icon and supplied bean illustrations are copied unchanged
from `../assets/branding/`; their provenance is recorded there. The upstream README's
own `../assets/longshot.png` is preserved separately.

Body text and controls use self-hosted Atkinson Hyperlegible Next, weights 200–800,
upright and italic. Libertinus Serif Semibold remains for large headings; code stays
monospace. Libertinus is served as full-character WOFF2 files to reduce font downloads;
the original TTFs remain alongside them. `src/assets/fonts/libertinus/WOFF2-PROVENANCE.json`
records the encoder, source hashes and reproduction command. No encoder is required
for normal website builds. Font provenance and original notices are retained in
`src/assets/fonts/` and `public/fonts/`. No external font service or game-extracted
fonts are used.

## Team page

The team page (`/about/team/`) lists the people with the most commits in Longshot (Hookshot's history included),
collected from Forgejo when the site is built (`src/lib/contributors.ts`). There is nothing to add by hand: a merged
change puts you there at the next build.

## Content and source guards

`content-sources.ts` lists the imported pages, each site path with the file it is made from. Edit their original files in `docs/`,
`README.md` or `CONTRIBUTING.md`, not `src/content/docs/generated/`. Content preparation
copies only the explicitly selected pages and artwork. Other relative references
link to the team's Forgejo source tree; website instructions link to this fork branch. New internal documents are not automatically
published.

The System requirements page (`src/content/docs/compatibility.mdx`) is a copy of the root README's "What you need":
when one changes, change the other to match.

The progress board and the front page's release and activity panels are collected from Forgejo when the site is
built; see `PROGRESS.md`.

The reference manual, contributor guide and saves guide are generated directly from
`README.md`, `CONTRIBUTING.md` and `docs/saves.md`. They have one maintained source;
the generated copies are ignored by Git and rebuilt automatically. Historical
acknowledgements and literal migration commands are preserved, not rebranded.

The manual's presentation transform puts installation first, adds platform shortcuts
and folds advanced reference sections into native disclosures. It preserves the
source text and heading anchors; linked sections open automatically. Expand/collapse
controls and print handling keep the full reference accessible. The getting-started
guide uses cards while retaining its original source in `docs/`.

The manual renders the README's minimum/recommended specs table; the System requirements page has its own copy
(see above). Requirements without a published minimum or separate recommended tier are labelled explicitly; the table
does not establish performance targets or replace GPU-status caveats.

Save and update guides use Easy beans for everyday launcher steps and Advanced beans
for optional reference details. Their canonical sources remain `docs/saves.md` and
`docs/updates-and-saves.md`; generated cards and disclosures preserve existing anchors.

The graphics guide imports choices and defaults from the README's in-game menu table
at build time. Section names and order come from `gpu/vk/vk_menu.cpp`; mismatched,
missing or malformed rows stop the build. No network request or running game is needed.
Edit those original sources, not the generated cards. Restart badges reflect explicit
README annotations; absence of a badge is not a promise about every build or mode.
Search and section filters enhance native disclosures; without JavaScript every card
remains available. Setting links clear filters and printing includes the whole catalogue.

Common issues uses seven symptom cards with short checklists and a nested “Still stuck?”
disclosure for reporting details, issue search and Discord. The steps and links remain
in `docs/common-issues.md`; `format-common-issues.mjs` supplies presentation labels only.
Existing topic anchors open their cards through the shared manual navigation. Native
disclosures work without JavaScript, and the shared print handling expands every card.

## Validation and limits

Public pages use neutral game and console wording. Imported menu descriptions and activity text pass through
`src/lib/public-copy.ts` before publication; source records and links remain intact. `npm run check` rejects the
listed game-brand terms in generated page prose and the public progress feed. Literal commands, required save
paths, technical product names and third-party attribution are preserved. Update public wording and its tests if
an upstream description introduces a new term; never rename a real file path or runtime setting to hide it.

`npm run check` verifies generated routes, links, anchors, edit destinations, artwork
bytes, search output, source guards and the website's test suite. `build:preview`
runs both the build and checks. These do not build or launch the game.

Browser access was unavailable during implementation. Rendered desktop/mobile,
keyboard, and light/dark visual checks remain outstanding. Generated HTML and the
compiled Easter-egg script were checked separately; those are not browser checks.

## Download links

`worker/index.ts` answers `/download` on longshot.magicbean.dev (everything else is the built site): permanent links to
the newest [Longshot Launcher](https://git.magicbean.dev/MagicBeanDev/LongshotLauncher/releases), for READMEs, release
notes and chat. Forgejo's `/releases/latest` covers stable only; these cover each channel.

| Link | Goes to |
|---|---|
| `/download` | a page: the launcher for the visitor's system (by its User-Agent), from the best channel there is, and every channel |
| `/download/launcher/<os>` | that file of the best channel there is: stable, else rc, else nightly |
| `/download/launcher/<channel>` | the newest release page of `stable`, `rc` or `nightly` |
| `/download/launcher/<channel>/<os>` | that file of the channel's newest release, such as `/download/launcher/stable/windows` |

`<os>` is `windows` (`…-windows-x64-setup.exe`), `windows-portable` (`…-windows-x64-portable.zip`) or `linux`
(`…-x86_64.AppImage`). A link is a `302` to the file on git.magicbean.dev; a channel or file that isn't there is a `404`
page that says so and names the newest launcher there is. The channels follow the launcher's own rules
(`Core/ReleaseManifest.cs`, `scripts/release.py newest`), and the release list is kept 60 seconds.
`npx wrangler dev` (after `npm run build`) serves the site with the Worker; `tests/download.test.ts` checks the channel
rules.

## The report inbox

`worker/report.ts` answers `POST /report`: the [Longshot Launcher](https://git.magicbean.dev/MagicBeanDev/LongshotLauncher)'s
**Send report** (after a build stops or the game crashes) files the report here as an issue on
[Longshot](https://git.magicbean.dev/MagicBeanDev/Longshot/issues), so players need no Forgejo account. What the
launcher sends, and what it shows the player first, is in the launcher's `docs/launcher-reports.md`.

- **One issue per problem.** The report's kind and signature (a crash's code and the module it was in, the same on every
  PC) make a 10-digit key, written in the title: `[report 4fd0273e9e] GameCrashed: 0xc0000005 nvoglv64.dll+0x1234`.
  A report whose key has an **open** issue is a comment there; otherwise it opens a new issue. So: keep an issue open
  while its reports are useful, and close it when the problem is fixed. A crash that comes back then opens a new issue.
- **The answer** is `200 {"id": "#123", "url": "<the issue or the comment>"}`, which the launcher shows the player. Any
  other status is "not sent", and the launcher offers the report to copy instead: `400` a report it can't read, `413` too
  large, `429` too many, `502` Forgejo failed, `503` the inbox is off.
- **Limits.** The body is at most 6 MB (256 KB of text and a 4 MB PNG). Each address may send 3 reports a minute, and
  everyone together 20 a minute (`ratelimits` in `wrangler.jsonc`). The address is only the rate limiter's key: nothing
  logs or keeps it.
- **Nothing in a report is trusted.** The kind must be one the launcher knows, the signature and version are cut to
  one short line, `@mentions` are broken so a report can't ping anyone, control characters go, and a picture must be
  a PNG. Forgejo renders the Markdown as it does any comment (no scripts).
- **Only the site files reports.** A PR preview (`pr-<n>-longshot-website...workers.dev`) checks the report and answers
  `{"id": "dry-run"}` without filing it, so the launcher can be tried against a preview with
  `LONGSHOT_REPORT_URL=https://pr-<n>-longshot-website.<subdomain>.workers.dev/report`. `npx wrangler dev` counts as
  the site (it serves the route's host), so locally, with a token in `.dev.vars`, set `REPORT_REPO` to a repository of
  your own there.

### Setting it up

1. **A bot account** on git.magicbean.dev (such as `longshot-reports`), so reports aren't filed in a person's name.
   Give it the **Triage** role (or a team with Issues: Write) on Longshot: without it, Forgejo files the issue
   but drops its labels.
2. **Its token:** the bot's Settings → Applications → Generate token, with the scope **write:issue** and nothing else.
3. **The secret,** from `website/` with the Cloudflare account signed in (`npx wrangler login`):
   `npx wrangler secret put REPORT_FORGEJO_TOKEN`. A secret stays with the Worker across deploys; it is never in the
   repository or the workflow.
4. **The labels** in `REPORT_LABELS` (`wrangler.jsonc`, comma-separated, now `kind/bug,source/report`): create
   `source/report` on Longshot. A label the repository doesn't have is left out.

Until step 3, the inbox answers `503` and the launcher keeps offering the report to copy.

**Turning it off**, at once and without a deploy: `npx wrangler secret put REPORTS_OFF` with the value `1` (back on:
`npx wrangler secret delete REPORTS_OFF`). Deleting `REPORT_FORGEJO_TOKEN` does the same. A **leaked token** is
revoked in the bot's settings and replaced with step 2 and 3; it can only write issues and comments.

A PR preview is a version of the same Worker and has the same secrets: the review that lets a PR preview at all (see
`.forgejo/workflows/website.yml`) is what keeps the token from code that would send it elsewhere.

`tests/report.test.ts` checks what the inbox accepts and what it files, against a stand-in Forgejo.
