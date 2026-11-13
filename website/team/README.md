# Team magicbean roster

Each person has one JSON file here. The team page reads these files at build time;
there is no second member list to maintain, no browser token and no account signup
on the website. Profiles are opt-in; this is not an automatic contributor ranking.

## Add yourself

1. Branch from the website branch containing this directory. While the site is on
   the preview fork, target `bibiburl/Longshot`, branch `docs/longshot-website`.
   Once merged upstream, target the branch maintaining the site there.
2. Add `your-handle.json` here, using lowercase letters, digits and hyphens for the
   filename. Use the example below, replacing the name and profile. Optional fields
   can be omitted. A display name or handle is enough; no legal name is required.
3. Run `npm run build:preview` from `website/`, then push your branch to your fork
   and open a pull request against the website branch. Maintainers review your
   profile link and membership before merging. If you cannot run the website build,
   state that in the PR so a reviewer can check it.

```json
{
  "name": "Your display name",
  "profile": "https://git.magicbean.dev/your-handle",
  "focus": ["Graphics", "Testing"],
  "bio": "A short introduction, with room for a little bean humour."
}
```

Use a direct HTTPS **user profile** on MagicBean Git, GitHub, GitLab or Codeberg,
not a repository, sign-in link or URL containing credentials/query parameters.
Other Git hosts can be added to `src/lib/team.mjs` after review. The link must be
yours: URL validation checks format, not account ownership. A maintainer should
confirm ownership through the submitting Git account or another established team
channel. Profiles requiring sign-in are valid, but visitors may need to sign in.

Name: 1–60 characters. Bio: optional, 1–240 characters. Focus: optional, up to three
plain-text labels of 1–32 characters each. No markup. Do not add someone else without
their consent. Never put access tokens, email addresses or other private contact
details in a profile. Only the fields above are accepted; duplicate profiles fail
the build. Cards use initials rather than fetching third-party profile images.

To update your card, edit your file. To leave the displayed roster, delete your file
in a PR. Merged changes appear after the website is rebuilt and deployed; pushing a
profile does not deploy the site automatically. Git history retains past revisions.

The preview shows a clearly labelled example beside the first member. The example
is not counted as a profile and disappears once two real profiles are listed.
