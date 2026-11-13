# Forgejo Actions runners

Longshot's CI runs on Forgejo Actions (`.forgejo/workflows/`), on runners the team registers with git.magicbean.dev. Every
job runs on Linux: **no Windows runner is needed**, and none is registered. These are manual steps for an admin of the
instance or of MagicBeanDev; nothing here needs a game.

The labels below are requirements, not a hardware inventory or evidence that a job passed. Check the repository's
Actions page and the admin's runner list for current availability.

## Security comes first

A runner executes repository code, including build scripts and changed workflows. Its labels route work; they are
not access controls. [Forgejo can select instance, owner/organization and repository runners](https://forgejo.org/docs/v17.0/user/actions/security/),
and [trusted PR authors can change the workflow that runs](https://forgejo.org/docs/v17.0/user/actions/security-pull-request/).
Do not register a personal hardware machine on the instance, MagicBeanDev organization or shared Longshot repository.
The ordinary build-runner steps below are **not** instructions for attaching a volunteer's PC.

The hardware pilot belongs only in an owner's fork or dedicated control repository where the owner trusts **every**
workflow and everyone who may change or dispatch one. Do not approve untrusted PR workflows there. Keep release and
signing jobs elsewhere. Host-mode controllers have no isolation from their account: use a separate, disposable
environment with no personal files, game dump, saves, SSH keys or publishing secrets. Prefer a dedicated device;
a separate account on a daily-use PC does not remove shared-kernel or GPU-driver risk.

Forgejo's automatic job token still exists in the controller; GitHub's `permissions` key does not restrict it on
Forgejo. The pilot does not pass it, a registration credential or the host environment to its fixture containers.
The owner must review Podman's configuration, image, OCI hooks and GPU/CDI mappings too. A digest pins an image; it
does not establish trust. Never weaken confinement or expose a container-engine socket to make a failing pilot run.

## Turn Actions on

Repository *Settings > Units > Actions* (or the API's `has_actions`), on MagicBeanDev/Longshot and
MagicBeanDev/LongshotLauncher. The secrets and the variable the release workflows read are in [RELEASING.md](../RELEASING.md), *One-time setup*.

## The labels the workflows ask for

| Label | Used by | What it must be |
|---|---|---|
| `ubuntu-latest` | `release.yml`, `ci.yml` (its title check and its Linux checks run on `ubuntu-24.04`), `third-party.yml`, the launcher's `ci.yml` and `release.yml` | Linux, Docker: e.g. `ubuntu-latest:docker://ghcr.io/catthehacker/ubuntu:act-24.04` (Node for the actions, git, Python 3, curl, sudo and apt) |
| `ubuntu-24.04` | `ci.yml`'s tests (with the Windows Builder's compile and --cli checks), `build-linux.yml`, `build-windows.yml` (all of it), `vulkan-dlss.yml` | the same image; the AppImage job builds inside Ubuntu 22.04 itself (`scripts/linux/deps.sh` runs in its container) and needs Docker or enough disk (about 30 GB). The Windows jobs install Wine and Xvfb with apt, and download llvm-mingw's Linux build and the .NET 8 SDK |
| `quick` | the short jobs of 5 minutes or less: `ci.yml`'s title check and actionlint, `pr-description.yml`, `third-party.yml`'s check, `release.yml`'s *The version* and *Sign and publish*; the launcher's title check, *The version* and *Sign and publish* | the same image. A runner kept for them only (label `quick`, capacity 1) means they never wait behind a build; the other runners carry `quick` too and take them when they have room |

### Windows files, built on Linux

There is no `windows-*` label, and a job asking for one would queue forever (`tests/test_windows_on_linux.py` fails on
one). `build-windows.yml` makes the Windows builder's zip on Linux: the setup with llvm-mingw's Linux build of
`tools.lock`'s release (`scripts/builder_package.py llvm-mingw --host linux`, its SHA-256 checked), its icon, manifest
and version with that llvm-mingw's `windres`; the builder (command-line only) with `dotnet publish -r win-x64 -p:EnableWindowsTargeting=true`
(self-contained, as before). `scripts/check_windows_files.py` checks what it made as Windows files and starts the setup
and the builder under Wine. The Windows Vulkan back end (also with DLSS, `vulkan-dlss.yml`) is built with `RX_TARGET=windows`.
What only a Windows PC can check is in RELEASING.md, *Needs a Windows tester*.

## Register a runner

1. Download [forgejo-runner](https://code.forgejo.org/forgejo/runner/releases) for the machine.
2. In MagicBeanDev's *Settings > Actions > Runners*, *Create new runner*, and copy the token.
3. `forgejo-runner register --instance https://git.magicbean.dev --token <token> --name <name> --labels
   'ubuntu-latest:docker://ghcr.io/catthehacker/ubuntu:act-24.04,ubuntu-24.04:docker://ghcr.io/catthehacker/ubuntu:act-24.04,quick:docker://ghcr.io/catthehacker/ubuntu:act-24.04'`
4. Run it as a service (`forgejo-runner daemon`). One Linux runner is enough to start; the builder jobs are long (an hour
   and more) and want 4+ cores, 16 GB of memory and about 50 GB of disk.
5. Keep one runner for the quick jobs: register a second one with `--labels 'quick:docker://ghcr.io/catthehacker/ubuntu:act-24.04'`
   only and `capacity: 1` in its config (it can share the machine and its Docker), so a pull request's checks start at once
   however many builds are queued. A job that takes longer than a few minutes does not belong on `quick`.

## Actions from outside

The workflows name actions by full URL: `https://github.com/actions/...` pinned by commit, and
`https://data.forgejo.org/forgejo/upload-artifact@v4` / `download-artifact@v4` (GitHub's v4 and later refuse a server
that is not github.com). The runners must reach github.com and data.forgejo.org, or those actions must be mirrored and
the URLs changed. `actions/cache` needs the runner's cache server (on by default in forgejo-runner).

Another project's repository is not checked out with `actions/checkout`'s `repository:`: on Forgejo that names a
repository of this instance (`github.server_url`), not GitHub's. `scripts/fetch_commit.sh <URL> <commit> <folder>` fetches
it by its full URL and commit, with no token. The actions are JavaScript (Node 24): a job in a `container:` without Node
(the AppImage's `ubuntu:22.04`) installs Node.js's own build, pinned by its SHA-256, before its first action.

An artifact goes to Forgejo, and comes back, by the address Forgejo signs its links with: its `ROOT_URL`
(git.magicbean.dev, behind Cloudflare), whatever address the runner registered with; Forgejo has no setting for another.
The actions send an artifact in 8 MB blocks, under Cloudflare's 100 MB limit, but a block that fails is not sent again,
so each upload and download step is tried once more when it fails.

## Uploads larger than 100 MB

The release workflows upload the builders (hundreds of MB) to the release. git.magicbean.dev is behind Cloudflare, which
cuts a request body at 100 MB, so a runner on the server's own network should upload to the Forgejo it can reach
directly: set the organization variable `RELEASE_UPLOAD_API` to that address's API (e.g. `http://magicbean-git:3000/api/v1`; a variable can't start with `FORGEJO_`).
RELEASING.md, One-time setup, has the rest (the attachment limits in `app.ini`).

## Opt-in game-free hardware pilot

`hardware-fixtures.yml` is manual only and disabled unless `HARDWARE_TESTS_ENABLED=true`. It refuses the upstream
repository and any ref except the owner's `main`. The required `approved_sha` must be the full SHA of that dispatch,
and the job checks that remote `main` still has that SHA immediately before execution. A queued approval for an older
head is refused. All checkout actions use fixed commits and do not retain credentials.

This first slice tests only an owner-reviewed default-branch snapshot, **not an arbitrary PR head**. Review all code
that executes before placing a candidate on the owner's `main`; report its resulting SHA, not the PR's old SHA. It
does not consume the test queue or turn a PR label into approval. A future PR service needs a separate trusted
controller, approval bound to the current full PR SHA, revocation on pushes, a final head recheck and reviewed result
publication. `testing wanted` alone is not that approval. Keep using [test requests](test-requests.md) meanwhile.

### What the job needs

| Setting | What the owner supplies |
|---|---|
| Runner label | `longshot-hardware-linux`, registered to that control repository only; no ordinary build labels |
| `HARDWARE_TESTS_ENABLED` | Repository variable, unset/off by default; literal `true` enables manual dispatch |
| `HARDWARE_TEST_IMAGE` | Repository variable: an already present, reviewed Linux image named with `@sha256:` and its full digest; never a floating tag |
| `HARDWARE_TEST_DEVICE` | Repository variable: one `/dev/dri/renderD...` node for Mesa, or one `nvidia.com/gpu=...` CDI device, never `all` |
| `approved_sha` | Dispatch input: full reviewed SHA of the owner's current `main` |
| `device_name` | Dispatch input: a unique substring of the intended Vulkan GPU name; each fixture must report that same device |

The controller needs Linux, Bash, Git, Python 3, Node.js 24 for checkout, and local **rootless** Podman with cgroup v2
resource delegation and the `crun` runtime (`keep-groups`). Limit it to one job at a time. The image needs Python 3,
GCC with C++20, `tar`, the Vulkan loader, `vulkaninfo`, the matching driver and Khronos validation. Use a reviewed,
minimal image with no credentials, image volumes or extra services. Provision it before a job; `--pull=never` refuses
a missing image. NVIDIA's CDI configuration must expose only the intended adapter and its required driver files.
Driver/OS combinations, especially NVIDIA CDI and SteamOS, need their own confinement trial before activation.

The headers are the same `Vulkan-Headers` commit as `ci.yml` (v1.4.345). `tools.lock` and `scripts/tools.py` describe
the Windows builder's shipped tools, not this Linux GPU/driver environment; do not install those archives as a
substitute. Building the Windows release files on Linux does not provision a hardware test image. No SDK license is
accepted and no system package is installed by this workflow.

### What runs and what leaves

`scripts/hardware_fixtures.py --run` probes `vulkaninfo --summary`, then runs the existing write-back fixture (five
authored cases) and buffer/image copy-span fixture (one case). Both already select a device explicitly. The offscreen
aspect fixture is deliberately excluded until it has an explicit selector. See #67 for the separate software-driver
proposal. No dump, game, window, swapchain, game build or speed comparison is involved.

The probe has no source mount. Each fixture gets only six regular tracked files exported from the approved commit,
plus pinned Vulkan headers, read-only. It never sees the checkout's `.git`, untracked files, the owner's home, a
display socket or a writable host directory. Each container has no network, no inherited environment or capabilities,
no new privileges, a read-only root, a private 512 MiB temporary filesystem, 2 GiB RAM with no extra swap allowance,
two CPU cores' quota and 128 processes. The probe is limited to 30 seconds; each compile-and-run to 150 seconds.
Podman's own deadline survives loss of the controller, and cleanup removes only that invocation's unique container.
The workflow has a ten-minute limit; a cleanup failure is a failure requiring owner inspection.

Only one small Markdown file is uploaded, using the eight columns of [tested configurations](tested-configurations.md).
It contains the full tested SHA, image digest, selected GPU/driver/API, each fixture's fixed status and case count.
CPU/RAM are marked not collected. Raw command output is bounded to 1 MiB per command, parsed in the controller and
never printed or uploaded; device UUIDs, arbitrary log text and directories are excluded. Missing metadata, software
devices, ambiguous selection, skips, missing/repeated cases or missing validation activation fail rather than count as
passes. Review the rows before posting them to an issue or the shared table. Passing these authored cases does not
prove that the game works, that results cannot be forged, or that a GPU driver cannot crash or escape its container.

To check the reporting path **without probing any GPU or starting Podman**, from a checkout:

```sh
python3 -I -B scripts/hardware_fixtures.py --no-gpu --commit "$(git rev-parse HEAD)" --output hardware-results.md
python3 -I -B tests/test_hardware_fixtures.py
```

The output path must be new. `--no-gpu` is the default; its success means only that a `not_run` row was written, never
that hardware passed. `--run` additionally requires `--image`, `--gpu-device`, `--device-name` and `--headers` (the
Vulkan-Headers `include` directory). This Linux-only command installs and registers nothing.

Keep this check optional, never required for merging. An offline runner, skipped job, missing prerequisite or timeout
is not a pass: stop or cancel the request and fall back to the human queue. The owner chooses availability and must
inspect cleanup after cancellation or a driver fault. Disable the repository variable and stop the runner to withdraw
the machine. A real isolation/cleanup/publication trial and maintainer agreement come before registration; game-based
automation needs a separate policy decision.

