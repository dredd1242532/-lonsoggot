# Longshot progress feed

The public source is `MagicBeanDev/Longshot` on MagicBean's Forgejo instance.
The collector uses its `/api/v1/repos/MagicBeanDev/Longshot` API and follows only
same-endpoint pagination links. It includes all open issues and pull requests,
closed items updated within 90 days, up to 20 published releases and the latest
stable release. No stable release is represented explicitly, without borrowing
another project's releases. Previous repository curation is not reused.

## Collection and hosting

`npm run progress:sync` writes the tracked `public/progress-feed.json`. Run it
before building when a new snapshot is wanted. Normal builds do not fetch the API.
Without a valid snapshot the page reports unavailable data, not an empty tracker.
Collection fails as a whole if any required page fails or a pagination limit is hit.

`npm run progress:serve` optionally serves `/Longshot/progress-feed.json` at
`127.0.0.1:4322`. It caches complete snapshots for five minutes, coalesces refreshes
and retains the last complete snapshot on failure. A host can reverse-proxy this
route, or set `DOCS_PROGRESS_FEED_URL` at build time and `PROGRESS_ALLOWED_ORIGIN`
on the feed server. `PROGRESS_HOST` and `PROGRESS_PORT` configure the listener.
HTTPS termination belongs to the host. `FORGEJO_TOKEN` is optional and server-only;
it is never sent to browsers or written to the snapshot. The collector rejects
private repositories and redirects. Public collection needs no account token.

No deployment or refresh schedule is installed. The browser reloads the feed every
five minutes while visible, but a static file changes only after it is replaced.
Collection timestamps describe data freshness, not merely a successful HTTP request.

## Editorial maintenance

Keys in `progress-curation.json` use `issue:123`, `pull:456` or `release:789` (the release ID, not tag).
Each entry needs `title`, `summary`, `reviewedAt` (ISO date), `sourceUpdatedAt` (exact API timestamp), and `sources`
(public URLs under `https://git.magicbean.dev/MagicBeanDev/Longshot/`). Optional `platforms` contains `Windows`, `Linux`,
`Steam Deck`, or `Other`; optional `area` is `Graphics`, `Performance`, `Controls`, `Audio`, `Setup`, or
`Documentation`, `Crashes or startup`, or `Sound, controls or saves`; optional `workaround` is plain text.
Optional `impact` is `Crashes`, `Save integrity`, `Cannot start`, or `Blocked progression`; it enables the reviewed
serious-issue highlight only when the report explicitly matches the current stable version. Do not add speculative fixes, dates or progress percentages.
Review timestamps appear with curated advice. If a source's update timestamp changes the entry falls back to
the original title and neutral status explanation. The collector rejects malformed editorial entries.

Only metadata, labels, selected report-form version/category values and reviewed prose are published. Issue/PR bodies, comments, attachments, author accounts
and downloaded assets are excluded. Text is rendered with textContent, never interpreted as HTML/Markdown.
Links are constructed from the fixed public upstream repository and validated again in the browser.

## Modules for future updates

| Module | Source | Purpose |
|---|---|---|
| Development at a glance | `src/components/ProgressOverview.astro`, `src/lib/progress-overview.mjs` | Explain open reports, review proposals, recent merges and stable releases. |
| Current version watch | `src/lib/progress-watch.mjs` | Match exact report versions, group reported areas and separate other/unknown versions. |
| Searchable work | `src/components/ProgressBoard.astro`, `src/lib/progress-board.ts`, `src/lib/progress.mjs` | Search, filters, progressive results, freshness and plain-language status explanations. |
| Reviewed highlights | `progress-curation.json` | Sourced summaries, platform tags and serious reported impacts, invalidated by source changes. |
| Help and status guide | `src/content/docs/progress.mdx` | Explain what the data means and link players to practical help. |
| Shared feed | `scripts/progress-feed.mjs`, `scripts/serve-progress.mjs`, `scripts/sync-progress.mjs` | Collect public data, follow verified cursor links, cache and retain complete snapshots. |

The overview is descriptive, not a roadmap commitment. No module treats counts as unique bugs, developer speed,
completion percentages or affected-player totals. The current-version watch intentionally misses unstructured
reports and reports updated only in comments; source links remain essential. Editorial summaries are reviewed
against reports, not claims of independent reproduction.

Issue-search links (including front-page queries) move the complete search panel
ahead of the development overview and version watch, then focus and position the
input after layout. Feed updates stay below search and cannot push it down the page.
Opening Progress normally retains its overview-first order; background refreshes
do not repeat the focus/scroll action.

## Validation

Run `npm run build:preview` to build, check links and source guards, and run the
website tests. Tests cover normalization, pagination, unsafe source URLs, private
repositories, curation expiry, cache failures, version matching, search and filters.
A source/build check does not replace desktop/mobile, keyboard or theme review.
