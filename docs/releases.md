# Releases and updates

How Longshot and the Longshot Launcher are released on git.magicbean.dev, and how the launcher finds, checks and installs
them. The release design is shared with [MagicBeanDev/LongshotLauncher](https://git.magicbean.dev/MagicBeanDev/LongshotLauncher/src/branch/main/docs/releases.md);
the optional Windows Authenticode preparation below applies to Longshot's builder workflow only.
How to make a release, step by step, is in each repository's **RELEASING.md**.

Longshot's numbers start at **v3.0.0**; the launcher's are its own and start at **v1.0.0**. Nothing here reads or writes
GitHub, and nothing is compatible with Hookshot's 2.x updates (NotRexed/Hookshot's history is Hookshot's).

## In short

| | |
|---|---|
| **A release** | A Forgejo release of the repository: a tag, a page with the notes, and the product's files with `SHA256SUMS` and `SHA256SUMS.sig`. Nothing else: no package registry, no pointer files |
| **Two products** | Longshot releases its **builders** (Windows and Linux). The launcher releases **itself**. Each from its own repository, with its own versions |
| **Three channels each** | `stable` (tag `vX.Y.Z`), `rc` (tag `vX.Y.Z-rc.N`, and every stable), `nightly` (every push to `main`). Read from the release list |
| **One key** | CI signs `SHA256SUMS` with the release key (ECDSA P-256). The launcher has the public key built in |
| **tools.lock** | The builders' build tools, Windows' and Linux's, are not in Longshot's releases: `tools.lock` names each one (URLs, SHA-256, size) |
| **Tags, not release PRs** | A maintainer releases by pushing a tag. CI checks the number against the commits, builds, signs and publishes. Notes by git-cliff |
| **Updates** | The launcher installs and updates the builder of each build, and updates itself, from these releases. The builders and the game look for no update themselves |

## What a release is

Each version is one Forgejo release, made by `.forgejo/workflows/release.yml`:

| Longshot (MagicBeanDev/Longshot) | The launcher (MagicBeanDev/LongshotLauncher) |
|---|---|
| `LongshotBuilder-<v>-windows-x64.zip`: the Windows builder (its setup as `LongshotBuilder.exe`, the builder and Longshot's sources in `LongshotBuilder\`, `tools.lock`, `VERSION`, and `recomplauncher.json` at the top) | `LongshotLauncher-<v>-windows-x64-setup.exe`: one self-contained program that installs itself (below); `LongshotLauncher-<v>-windows-x64-portable.zip`: the same program as `LongshotLauncher.exe` with its `portable` file |
| `Longshot-<v>-x86_64.AppImage`: the Linux builder (the builder, its Python, Longshot's sources, the Vulkan back end, its `tools.lock` and `recomplauncher.json`); its build tools are not inside | `LongshotLauncher-<v>-x86_64.AppImage` |
| `tools.lock`: the tools the builders download | |
| `SHA256SUMS`, `SHA256SUMS.sig` | `SHA256SUMS`, `SHA256SUMS.sig` |

`SHA256SUMS` is `sha256sum`'s format, one `<64 hex>  <name>` line per file. `SHA256SUMS.sig` is the base64 of the DER ECDSA
P-256 signature of `SHA256SUMS`'s exact bytes, by the release key. A release's files never change: a fix is the next version.

Release files are Forgejo attachments: `[attachment] MAX_SIZE` (2048 MB on git.magicbean.dev now) caps each file, and
`[repository.release] ALLOWED_TYPES` must stay empty (every type) for `.AppImage`, `.exe`, `.lock` and `SHA256SUMS`. The
site is behind Cloudflare, which refuses uploads over 100 MB: CI uploads to the organization variable `RELEASE_UPLOAD_API` (`FORGEJO_UPLOAD_API` to `release.py`), the instance's address
without Cloudflare in front, when it is set (RELEASING.md, One-time setup). Downloads are not limited.

CI makes each release with the job's own token (`forgejo.token`, given to `release.py` as `RELEASE_TOKEN`), with no
secret and no bot account: on a push or a tag in the
repository, Forgejo 17 gives it write access to that repository's every unit and to no other repository
(`routers/api/v1/permissions/repo_access.go`; read-only for a pull request from a fork). That covers every call
`release.py` makes: create a release, upload its files, edit and publish the draft, delete a nightly's release and its tag
(a tag that isn't protected). Each repository releases into itself only, so no other token or fallback is needed. The token ends with
the job. Forgejo 17 ignores a workflow's `permissions:` key, so the workflows have none.

## The channels

The channels are read from the repository's release list (`GET /api/v1/repos/<repo>/releases`), by the same rules in the
launcher (`Core/ReleaseManifest.cs`, `Newest`) and in `scripts/release.py` (`newest`, `channels`):

| Channel | Offers | For |
|---|---|---|
| `stable` | the newest `vX.Y.Z` that is published and not a pre-release | playing |
| `rc` | the newest `vX.Y.Z-rc.N` or `vX.Y.Z` that is published | trying the next version before it is stable |
| `nightly` | the newest `nightly-<count>-<8 hex>` that is published, by its count | every change, untested |

- A **draft** is on no channel. CI creates each release as a draft, uploads every file, and publishes it last, so a run
  that fails half way shows players nothing.
- **Pulling** a bad version is deleting its release, or setting it back to a draft: its channel then offers the one before
  by itself.
- The **nightlies** past the newest 5 are deleted, with their tags, by the run that makes a new one. Nothing else is pruned.
- Anything else in the list is on no channel: a 2.x tag, a git describe name. (The builders' tools are not releases:
  they are packages, below.)
- A nightly's count is the number of commits: for Longshot since the fork (e2a4d45, "Rename the project to Longshot"), for
  the launcher since its first commit.

**Each build has its channel, and the launcher has its own.** In the launcher, *Settings > Channel for new builds* picks
the channel a new build follows (each build keeps its own). *Settings > Launcher updates* picks the channel the launcher
updates **itself** from: LongshotLauncher's stable, rc or nightly, independent of any build's. It defaults to the channel
of the launcher the player installed (a stable launcher follows stable).

**No downgrades, no replays.** There is no pointer file to replay: the list is what it is. The launcher never updates a
build or itself to a lower version than the one it has on that channel, and never goes down by itself; going down is the
player's choice (building another channel's version, or `--rollback`). A server that serves an old list can only hold
updates back, not undo them.

## Signing

One release key signs every release of both products, in CI, from the organization secret `RELEASE_SIGNING_KEY`.

1. CI writes `SHA256SUMS` over the release's files, signs it (`scripts/release.py sign`), then checks the signature with
   the key the launcher is built with (`verify`, from `Core/ReleaseTrust.cs` or Longshot's `scripts/release_keys.json`):
   a key the launcher does not trust stops the release in CI, not in every player's launcher.
2. The launcher checks the signature of `SHA256SUMS` with its built-in key (`SignedSums`), then each file it downloads
   against the SHA-256 `SHA256SUMS` gives, and, for the Windows builder, each tool against the SHA-256 `tools.lock` gives.
   A release without a valid signature is refused, and the window says why.

**Why ECDSA P-256:** the launcher checks signatures with .NET's own crypto (no package; .NET 10 has no Ed25519), and CI and
maintainers sign with the `openssl` command line, so `scripts/release.py` needs no Python package. Hand-written signature
code is how updaters get broken.

**Why one key in CI:** it is simple and every release, nightlies included, is signed the same way. The risk is a leaked
secret or runner: the key would then sign anything. A second, **offline** key for stables only (the launcher requiring it
for `vX.Y.Z`) can be added later without changing the files: a second signature file beside `SHA256SUMS.sig`.

Rotating the key: RELEASING.md, "Rotate the key".

### Windows Authenticode (Longshot)

The release signature above and Windows code signing solve different problems:

| Signature | What checks it | What it proves |
|---|---|---|
| `SHA256SUMS.sig`, with the project's P-256 key | the launcher, or `release.py verify` | the release files match the checksums approved by that key; the checked `tools.lock` in turn pins the downloaded tools |
| Authenticode, embedded in an executable | Windows | the executable has not changed in the areas covered by Authenticode, and its signer chains to a trusted publisher certificate |

Neither signature proves that the build faithfully used the named source, or that the program is harmless. The
project's release key is **not** a Windows publisher certificate. The merged release design uses one key and
`SHA256SUMS`, not the earlier proposal's `longshot-release.json`, signed channel pointers or nightly/release key split.

Authenticode can replace an **unknown publisher** with a verified publisher name. It does **not** promise acceptance
by SmartScreen, Smart App Control, Defender or another antivirus. Microsoft distinguishes file-hash reputation from
publisher reputation; new signed programs can still warn, and EV certificates no longer bypass SmartScreen. See
[Microsoft's reputation guidance](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation).
Signing the builder or launcher does not sign the player's locally compiled `longshot.exe` or `longshot_vk.dll`.
Smart App Control can also block locally built unsigned programs: this is a separate limit, not advice to turn
security features off.

#### Coverage and order

Longshot's `.forgejo/workflows/build-windows.yml` has an optional **Authenticode** step. It signs two project
executables, not every `.exe` in a folder:

| Distributed Windows file | Where it comes from | This step |
|---|---|---|
| `LongshotBuilder.exe` at the zip's top | the `LongshotBuilder-Setup.exe` step compiles `builder/setup/setup.c` with the pinned llvm-mingw; `builder_package.py zip` copies it under this name | signs the setup before the copy |
| `LongshotBuilder/LongshotBuilder-Setup.exe` | `builder_package.py package` copies the same setup for the builder's Repair | the same signed bytes |
| `LongshotBuilder/LongshotBuilder.exe` | the **Longshot Builder** step's `dotnet publish` of the WPF project | signs its final apphost |
| .NET runtime executables/libraries in the publish folder; executables in downloaded `tools.lock` archives | Microsoft and the listed upstream tools | untouched; not represented as Longshot's code |
| `LongshotLauncher-<v>-windows-x64-setup.exe` and `LongshotLauncher.exe` in the portable zip | the **other repository's** `release.yml` runs `scripts/package.sh`: `dotnet publish` for `win-x64`, then two copies of that single-file program | not changed by Longshot's step; needs its own integration |

Signing happens **after** compilation and `dotnet publish`, **before** the package folder and zip are made.
`release.yml` then hashes that zip and signs `SHA256SUMS` as before. Do not change an executable after signing,
or sign a release file after making its checksums. No parts list or in-game updater is involved any more;
`Updater.cs` only rebuilds a game folder with the installed builder's own tools or opens the launcher download page.

#### Optional configuration, not a signing-required release policy

Leave `AUTHENTICODE_CERTIFICATE_SHA1` unset until maintainers choose a publisher and signing arrangement.
A release without it logs exactly **`not signed: no certificate configured`**, succeeds as before, and ships
unsigned Windows executables. This is not a claim that Authenticode is already configured or that releases must
be signed. Once a channel is promised to be code-signed, maintainers must make missing configuration fatal too;
this optional preparation deliberately does not make that policy decision.

To enable this certificate-store route:

1. Provision the chosen code-signing certificate in the signing account's `CurrentUser\My` Windows store, with
   its private key kept by its hardware token/HSM or compatible provider. Test noninteractive signing with that
   provider first. This route does not export or import a PFX, install a trusted root, or implement Azure/SignPath's
   service APIs. Those services need their own integration if selected.
2. Supply a **release-only** Windows runner labelled `windows-signing`, otherwise equipped like `windows-2022`.
   Do not give its account or key to PR runners. **A label is not an access control:** runner admission and the
   provider's authorization/approval policy must prevent untrusted workflows from using the identity, even if a
   PR asks for this label. Do not configure the secret until that isolation is in place.
3. Set the repository Actions secret `AUTHENTICODE_CERTIFICATE_SHA1` to the certificate's exact 40-hex
   thumbprint, without spaces. This is only a certificate selector, not a private key or a substitute for provider
   authorization. No signing credentials belong in the repository or in workflow command arguments.
4. Release from the repository's main/tag push as usual. `release.yml` passes the secret explicitly (not
   `secrets: inherit`) and selects the signing runner only when configured. The checked-out commit must match
   the triggering push. Pull requests, including fork and same-repository PRs, and manual build dispatches do
   not sign or receive this secret in a signing step.

`scripts/authenticode.py` uses **Windows' built-in Windows PowerShell**, addressed under `SystemRoot`, not a
downloaded tool or a command from the checkout's `PATH`. No new download, action or dependency pin is needed:
`tools.lock` and `scripts/tools.py` remain unchanged, and the signing tool is not installed on players' PCs.
Existing compiler downloads still use `tools.py`'s size/SHA-256 checks.

It requests SHA-256 Authenticode and a timestamp from `http://timestamp.digicert.com`, then checks the file again:
Windows must report `Valid`, an embedded `Authenticode` signature, the configured certificate, and a timestamp
certificate. Failure, a missing executable, or an invalid configured thumbprint fails the job before packaging.
Child output and provider errors are not echoed, since they can contain secrets. The private key stays with the
provider. The two setup copies are byte-for-byte copies of the signed setup.

This uses the Windows cmdlet's Authenticode timestamp protocol, not a newly added RFC 3161 client.
[Microsoft documents that its underlying API requires an HTTP URL](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/set-authenticodesignature#-timestampserver);
HTTPS can leave a signed file without a timestamp. The timestamp is cryptographically signed; the step rejects a
missing timestamp. The selected CA/provider and timestamp service still need a real Windows test before enabling
this in releases. An offline self-signed **stand-in** can prove embedding and tamper detection, not public publisher
trust, Windows prompts, this PowerShell/provider path, or the timestamp service.

The game-free decision and command checks are `python -I -B tests/test_authenticode.py`.
The optional Linux mechanics stand-in is
`python -I -B tests/authenticode_standin.py --osslsigncode /path/to/osslsigncode`
(also needs `openssl`, `clang` and `lld-link`). It downloads nothing, uses temporary keys without changing a trust
store, verifies the signed PE in all three package paths, and rejects a modified code section. It is not a test of
the Windows PowerShell signing step or a timestamp service.

#### Certificate and ownership decisions

For public downloads the team needs a code-signing certificate accepted by Windows' trust chain, not the
project's existing PEM key and not a self-signed test certificate. Ordinary organization-validated (**OV**) and
extended-validation (**EV**) certificates both supply publisher identity. EV adds stricter identity checks, but
is not a way to buy an immediate SmartScreen bypass.

The [CA/Browser Forum requirements, sections 6.2.7.4.1–6.2.7.4.2](https://cabforum.org/working-groups/code-signing/requirements/)
require suitable hardware protection for subscriber keys (including non-EV certificates since June 2023).
Depending on the CA this can be an approved token, HSM or cloud signing service; cloud storage specifically is
not mandatory for every certificate. A downloadable exportable private-key PFX is not the normal procurement
plan for a newly issued public certificate. Confirm the CA's hardware, provider and automated-signing terms
before buying anything.

**Estimates, not quotes (November 2026):**

| Option | Estimated cost | Decision still needed |
|---|---|---|
| OV from a commercial CA | USD 150–300/year | eligible legal identity; token/cloud costs may be extra |
| EV from a commercial CA | USD 400/year and up | whether its extra identity validation is needed for a reason other than SmartScreen |
| Azure Artifact Signing | about USD 9.99/month | identity/region eligibility, account ownership and a service-specific integration |
| SignPath Foundation | free if accepted | acceptance for Longshot and Forgejo, build provenance and manual approvals; the publisher would be SignPath Foundation |
| Stay unsigned for now | no certificate charge | keep the warning and trust limits explicit; no promise of a verified Windows publisher |

The paid estimates are from [Microsoft's signing comparison](https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/code-signing-options).
Prices, taxes and service limits can change. Microsoft's comparison and its
[Artifact Signing prerequisites](https://learn.microsoft.com/en-us/azure/artifact-signing/quickstart#prerequisites)
currently list different organization regions: check the service's current eligibility with Microsoft, rather than
treating the shorter list as settled. Individuals are listed as USA/Canada in both.
[SignPath's conditions](https://signpath.org/terms.html) require a code-signing policy, named author/reviewer/approver
roles, MFA, privacy/uninstall provisions and verifiable builds with manual approval. No acceptance for Longshot is
asserted here.

Maintainers must choose the legal publisher, pay or apply, name the certificate/account holder and a backup
custodian, and decide who approves releases and handles renewals/revocations. Prefer project-controlled accounts
and least-privilege signing access with MFA and an audit trail; do not nominate an individual without consent.
The setup's fixed `1.0.0` resources and missing `ProductVersion`/copyright strings still need release-versioned
metadata and an agreed legal identity. This step does not invent that identity or change those resources.

**Rotation:** before expiry, obtain the replacement through the chosen CA/service, provision it without exporting
its protected key, and try a release candidate on a clean Windows PC. Confirm the expected subject, chain,
timestamp and both setup copies. Change the certificate selector secret, publish the new expected publisher
details, then retire the old signing access after the transition. Do not rewrite already published files or
checksums. A valid timestamp can preserve validation after ordinary certificate expiry; revocation and policy
changes are different, and reputation continuity is not guaranteed. For suspected compromise, stop signing and
publication, revoke with the CA, replace credentials and tell players which releases are affected. Rotating this
certificate does **not** rotate `RELEASE_SIGNING_KEY`; that separate procedure is in RELEASING.md.

Players can inspect **Properties > Digital Signatures > Details**, or run
`Get-AuthenticodeSignature -LiteralPath .\LongshotBuilder.exe` in PowerShell. Once a publisher is agreed, compare
its subject with the project's published policy; a `Valid` signature alone does not identify the intended project.
Missing signatures are expected while this optional step is unconfigured. Report incorrect malware detections
through [Microsoft's submission portal](https://www.microsoft.com/en-us/wdsi/filesubmission), not by switching off
protection. See [Get-AuthenticodeSignature](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.security/get-authenticodesignature)
for what Windows reports.


## tools.lock (Longshot)

The builders need build tools that are not Longshot's own code. Windows (`windows-x64` lines): llvm-mingw, Python with the
prompt packages, emsdk, the CPM cache (FFmpeg, Vulkan and SPIR-V headers, VMA, dav1d), wgpu-native, the prebuilt files of
`third_party` (a64emit.dll, lld-link.exe, SDL2/SDL3, zbic.dll), and the play profile when there is one (optional: without
it the game is built all the same, a little slower in play). Linux (`linux-x64` lines): LLVM's clang
and lld (`emsdk`), the glibc 2.35 `sysroot`, `ffmpeg` with dav1d, `sdl2` and the Vulkan, SPIR-V and VMA headers (`cpm`).
They are not in the release: `tools.lock` names them.

```
tool <name> <version> <system> <sha256> <size> <dest> <prefix> <url> [<url> ...]
tool llvm-mingw 20261104 windows-x64 5521…40f 191037046 LongshotBuilder/tools/llvm-mingw llvm-mingw-20261104-ucrt-x86_64 https://git-cdn.magicbean.dev/api/packages/…-llvm-mingw.zip https://git.magicbean.dev/api/packages/…-llvm-mingw.zip https://github.com/mstorsjo/…
```

- **Installing.** The Windows builder's setup (`builder/setup/setup.c`), the Linux AppImage (`builder/linux/linux_tools.py`)
  and the launcher (`Core/ToolsLock.cs`, `Toolchains`) download each tool of their system from the first URL that answers
  with the right bytes. They check its size and SHA-256 and unpack the archive's
  `<prefix>` folder as `<dest>` in the install. A `<dest>` the builder already has (the Windows zip's
  `LongshotBuilder/tools/third_party`, the sources the `third-party` tool's prebuilt files go among) gets the tool's files
  merged in, file by file; a file both have is refused, never replaced. A tool another installed version has, with the
  same SHA-256 and unpacked the same way, is taken from it and not downloaded again (the Windows setup excepts a merged
  tool: it comes from its archive each time). `scripts/tools.py install` does the same, for CI and developers.
- **Where the archives are: Packages.** Every tool is downloaded first from Longshot's copy, a file of a **generic
  package** of the MagicBeanDev organization (its *Packages* tab): the package `<name>-<system>` (`emsdk-windows-x64`,
  `emsdk-linux-x64`: a tool's Windows and Linux builds have versions of their own), its version the tool's, the file
  `<sha256>-<name>.zip`, at `https://git.magicbean.dev/api/packages/MagicBeanDev/generic/<name>-<system>/<version>/<file>`.
  Forgejo keeps packages in Cloudflare R2, and `git-cdn.magicbean.dev` serves the same paths from Cloudflare's edge
  (cached), so a tool's first URL is its `git-cdn.magicbean.dev` link and its second the same file on git.magicbean.dev
  (`scripts/tools.py check --require` holds both, in that order). Anyone downloads them with no account. A package version's files never change (Forgejo refuses a second upload of a
  name), and nothing prunes them, so an older version's tools stay downloadable. Where an archive came from (upstream for
  llvm-mingw, NotRexed/Hookshot's `builder-parts` release for those taken from Hookshot) is its fallback URL, so no install
  depends on a repository we do not control alone. An archive Longshot makes itself (the CPM cache with dav1d, the
  prebuilt `third_party` files, the Linux tools, a play profile) is only in Packages, put there by `scripts/tools.py`.
- **Bumping a tool** is a pull request that edits `tools.lock`, with the archive uploaded by `scripts/tools.py mirror`,
  `add` or `assemble`: a new package version, then the line (RELEASING.md, "Bump a tool"). The release workflow refuses a
  `tools.lock` whose package files are not there with their size and SHA-256, or that lacks a tool the
  builder needs (`tools.py check --require`), in its first job, before anything is built.
- **Today's lock.** Windows: Python, emsdk and wgpu-native are taken as they are from Hookshot v2.1.0's Windows builder
  (NotRexed/Hookshot's `builder-parts` release), with their SHA-256; llvm-mingw is upstream's; the CPM cache is Hookshot
  v2.1.0's with dav1d added (`scripts/windows/dav1d.sh`), and `third-party` the prebuilt files of Hookshot v2.1.0's
  `third_party` part (`assemble third-party`). Linux: `scripts/linux/deps.sh tools`'s. There is no play profile yet: the
  lock has no `profile` line, and `check --require` does not ask for one.
- **Linux.** The AppImage carries only the builder: its Python (the tools' download and `make_game.py` run on it,
  and its packages follow `requirements.txt`, which changes with the version), Longshot's sources and the prebuilt Vulkan
  back end. Each `linux-x64` tool's `dest` is where the AppImage had it (`usr/lib/longshot/emsdk`, `usr/share/longshot/cpm`,
  ...). The launcher unpacks them beside the unpacked AppImage, as on Windows. An AppImage started by itself
  (`builder/linux/AppRun`) finds them there, or in the launcher's copy of the same version, or in
  `$XDG_DATA_HOME/longshot/builder/toolchains/<v>/`; what is missing its first `--cli` downloads there, its progress on
  stderr, linking a tool another version there has. Once there, no connection is needed.
  `scripts/linux/deps.sh tools` makes the Linux archives from its pinned downloads (`scripts/tools.py pack`: modes and
  relative links kept).

## How the launcher reads it

`Core/Releases.cs`, `ReleaseManifest.cs`, `ReleaseTrust.cs`, `ToolsLock.cs`, `ToolsSource.cs`, `Toolchains.cs`,
`SelfUpdate.cs`:

1. **At start, and on Retry.** It reads Longshot's release list and its own. For each of Longshot's channels and for its
   own launcher channel, it takes the newest version (rules above). It opens that release's `SHA256SUMS` and checks the
   signature. A channel whose newest release is refused offers nothing, and the home says why.
2. **Builds.** Each build is offered its channel's newest Longshot version when that is newer.
   - Windows: building a version downloads its `LongshotBuilder-<v>-windows-x64.zip` and its `tools.lock`, each checked
     against `SHA256SUMS`, then installs the zip and each tool (above) into `<home>/toolchains/<v>.part`. The folder is
     renamed when complete.
   - Linux: it downloads `Longshot-<v>-x86_64.AppImage`, checks it, and unpacks it with its own `--appimage-extract`, then
     installs the `linux-x64` tools of the `tools.lock` inside it (covered by the AppImage's checksum) into the same folder,
     each checked by its SHA-256, as on Windows. An AppImage with no `tools.lock` inside (one that carried its tools) needs
     none.
3. **Itself.** The launcher's own channel offers a newer launcher, or, after the player picked another channel, that
   channel's newest. *Update the launcher* downloads it beside the current file as `<name>.new` and checks it against
   `SHA256SUMS`. It starts it with `--self-check` (it must print `LongshotLauncher <version>` and exit 0 within a minute),
   then swaps the files:
   - Linux: one `rename()` over the AppImage.
   - Windows: the running exe is renamed aside, then the new one moved in; on failure the old one is put back.

   It then starts the new launcher and closes. `--rollback` puts the previous launcher back (`<name>.old`). A build from a
   checkout is `dev` and never updates itself.

### Installing the launcher

There is no installer program: the launcher installs itself (the launcher's README, *Installing*; `Core/Installer.cs`,
`LauncherLayout.cs`).

- **Windows, the setup file.** Its first start asks: **Install** copies it to
  `%LOCALAPPDATA%\Programs\LongshotLauncher\LongshotLauncher.exe` for the user (no administrator rights), with a Start
  menu shortcut and an *Apps & features* entry, its data in `%LOCALAPPDATA%\LongshotLauncher`; **Run portable** puts a
  file named `portable` beside the program and keeps everything in `LongshotLauncher-data` beside it, with no registry
  or shortcut. `--install` installs without asking; `--uninstall` (what *Apps & features* runs) removes the program, its
  shortcuts and its entry, and asks before deleting its data (never a build's game folder or the saves).
- **Windows, the portable zip.** `LongshotLauncher.exe` with the `portable` marker already beside it, so it never asks.
- **Linux.** The AppImage needs no installing. *Settings > Applications menu > Add to applications menu* writes a
  `.desktop` entry that starts the AppImage where it is. Portable is the AppImage convention: a folder named
  `<AppImage>.home` beside it holds its data.
- **Updates keep the kind.** An installed launcher updates from the release's setup file, a portable one from the
  portable zip, in place beside its `portable` file; the file keeps its name.

`LONGSHOT_LAUNCHER_TRUST=<public key .pem>` trusts one more key, to try a release signed with a test key.

## CI

Both repositories have the same workflows in `.forgejo/workflows/`, on Forgejo Actions:

| Workflow | Longshot | The launcher |
|---|---|---|
| `ci.yml` | every pull request and push to main: the PR title, every game-free check (`tests/run_all.py`, on Linux) | the PR title, compile, `test_launcher_core.py`, `test_release.py` |
| `release.yml` | tags `v*` and pushes to main: the version, the two builders (`build-linux.yml`, `build-windows.yml`), sign and publish | tags `v*` and pushes to main: the version, the launcher (`scripts/package.sh`), sign and publish |
| `third-party.yml` | the third_party rule (`scripts/check_third_party.py`) | (none) |

Longshot also keeps `build-linux.yml` and `build-windows.yml` (also run on pull requests that change code) and
`vulkan-dlss.yml` (the upscalers' build checks). A tag on a commit CI has released already (its nightly, or a stable's
release candidate) is still built again, since every file carries its version (a release never ships a file that names
another), but `release.py released-as` finds that release, checks its signed `SHA256SUMS` (one that does not check stops
the run), and the build checks that ship no file and passed in its run are not run again. Every job runs on Linux: Longshot's Windows files are cross-built there,
and what only Windows can check is a tester's (RELEASING.md, *Needs a Windows tester*). The runners are in Longshot's [docs/forgejo-runners.md](https://git.magicbean.dev/MagicBeanDev/Longshot/src/branch/main/docs/forgejo-runners.md).

## The version a tag must have

Pull requests are squash-merged, and their titles are Conventional Commits, so the commits on `main` say what changed. For
a tag, `scripts/release.py check-tag` (in `release.yml`, before anything is built) refuses:

- a name that is not `vX.Y.Z` or `vX.Y.Z-rc.N` of the product's numbers;
- a tag that is not on `main`;
- a tag that is not above every release there is (a stable `vX.Y.Z` is above its own `vX.Y.Z-rc.N`, so a stable tagged on
  the same commit as a release candidate is the normal way);
- a number lower than the commits since the last stable call for:

| Since the last stable | At least |
|---|---|
| a `!` after the type (`feat(vk)!:`) or a `BREAKING CHANGE:` footer | the next major (`v4.0.0` after `v3.2.1`) |
| a `feat` | the next minor (`v3.3.0`) |
| `fix`, `perf`, or anything else | the next patch (`v3.2.2`) |

The first release of each product only needs its first number (Longshot `v3.0.0`, the launcher `v1.0.0`).
`python -I scripts/release.py next` prints the number and the notes before tagging.

## Release notes

git-cliff writes them at release time from the commit titles since the previous tag, with the repository's `cliff.toml`.
For a stable, that is since the previous stable, so its notes cover its release candidates. The headings are Features,
Fixes, Performance, Reverted, Documentation, Code, Build and Tests; `ci` and `chore` are left out. The notes go on the
release page only: nothing is committed back to `main`, and there is no changelog file to keep up (Hookshot's is in
NotRexed/Hookshot).

`release.py publish` puts a header above the notes (`release_page`), so a player who lands on a release page downloads the
right thing:

- **The launcher's releases** start with big download links to that release's own files: *Download for Windows* (the
  installer), *Download for Linux* (the AppImage), and a small link to the portable zip, all through git-cdn.magicbean.dev
  (the same paths as git.magicbean.dev, from Cloudflare's edge).
- **Longshot's releases** start with "To play, get the Longshot Launcher": the same big links, to the newest launcher of
  the **same channel** (an rc to the launcher's rc, a nightly to its nightly, a stable to its stable) through the links
  below, then "Don't download the files below": they are the builders the launcher installs.

## Download links

Forgejo's `/releases/latest` covers stable only. longshot.magicbean.dev (the Longshot website, `website/worker/index.ts` in MagicBeanDev/Longshot) has permanent links to
the newest launcher of each channel, read from LongshotLauncher's release list by the rules above (cached 60 seconds):

| Link | Goes to |
|---|---|
| `https://longshot.magicbean.dev/download` | a page: the launcher for the visitor's system, from the best channel there is, and every channel |
| `https://longshot.magicbean.dev/download/launcher/<os>` | that file of the best channel there is: stable, else rc, else nightly |
| `https://longshot.magicbean.dev/download/launcher/<channel>` | the channel's newest release page |
| `https://longshot.magicbean.dev/download/launcher/<channel>/<os>` | that file of the channel's newest release |

`<channel>` is `stable`, `rc` or `nightly`; `<os>` is `windows` (the setup), `windows-portable` (the zip) or `linux` (the
AppImage). A channel with no release yet answers with a page that says so and offers the newest there is. The READMEs'
download buttons and Longshot's release pages use these links; there are none for Longshot's builder files, which only
the launcher downloads.

## RecompLauncher (Longshot)

[RecompLauncher](https://recomplauncher.com) is the other way to install Longshot. It installs a recomp from a release
file, then runs its `recomplauncher.json` (the game it needs, how to build and start it). Longshot keeps that file
(`recomplauncher.json`, `tests/test_recomplauncher_json.py`) inside both builders, which are the same files the launcher
installs:

| File | What RecompLauncher does with it |
|---|---|
| `LongshotBuilder-<v>-windows-x64.zip` | *prepare* runs `LongshotBuilder.exe --cli --prepare --game <your copy>`: the setup installs the builder and the tools of the `tools.lock` beside it (no release lookup), then builds; *start* runs it with `--play` |
| `Longshot-<v>-x86_64.AppImage` | the same `--cli --prepare` and `--play` |

**What RecompLauncher cannot do yet.** It finds releases only on GitHub. Its guide says it "installs and updates your recomp
straight from your GitHub releases", its `repo` is a GitHub `owner/name`, and its schema has no field for another forge or
a URL. So `recomplauncher.json` names no `repo`, and RecompLauncher cannot find Longshot by itself. It needs either a
GitHub repository that mirrors these releases (there is none), or Forgejo support in RecompLauncher (a request to its
makers). Until then a player can add a downloaded zip or AppImage to RecompLauncher by hand, as a local build.

So there are two ways to install: **the Longshot Launcher** (recommended: it follows channels, checks signatures and
updates everything), or **RecompLauncher**, with the same builder files.

## Why this design

- **Forgejo releases, not a package store and pointer files.** A release is what Forgejo already shows people: a tag, notes
  and files. The channels follow from the list, so there is nothing else to publish, keep in step or replay. Drafts make a
  release appear all at once.
- **A signature on the checksums.** HTTPS says a file came from git.magicbean.dev. A stolen token, a compromised runner or a
  mistake on the server would otherwise reach every player's PC, and the launcher runs what it installs. One signature
  over `SHA256SUMS` covers every file, however large.
- **tools.lock, not the tools in every release.** The tools change rarely and weigh gigabytes. A lock file in the repository
  says exactly which bytes each version needs; reviewers see a tool change as a one-line diff; installs share a tool
  across versions; and a release holds only Longshot's own files.
- **Tags and computed numbers, not release PRs.** A maintainer tags; CI checks the number against what was merged and
  writes the notes. Nothing is committed back to `main`, so there is no release branch or bot commit to review.
- **The builders do not update themselves.** One updater, the launcher's, with one set of rules, instead of three (the
  game, the Windows builder, the Linux builder). The builders have no window any more; their `--cli --update <folder>`
  rebuilds a game with their own tools.
