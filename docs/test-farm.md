# Lend your PC to the Test Farm

Most changes are written and measured on one PC. Before they merge they need a run on the hardware they could break:
another GPU maker, Linux or Windows, a smaller card. The Test Farm lets you lend your PC for that. You run a small app,
the **farm agent**. When a maintainer has approved a pull request, your agent builds it next to `main` from your own
copy of the game, plays the same scenes on both, and posts the numbers on the pull request. You don't have to sit
there while it runs.

The farm is new. Its server, the agent and the design all live in
[Beanstalk](https://git.magicbean.dev/MagicBeanDev/Beanstalk) (the design is the
[spec](https://git.magicbean.dev/MagicBeanDev/Beanstalk/wiki/Spec)). For now only Linux agents take jobs from the
farm. Windows agents can test a pull request you pick yourself.

## Is it safe?

Testing a pull request means running its code on your PC. The farm is built around that fact:

- **Nothing runs until a maintainer approves it.** A maintainer reads the change and approves one exact commit with
  `/beanstalk`. If the author pushes again, the approval is gone and nothing runs until someone approves the new
  commit. Nothing ever runs on its own, not even the team's own pull requests.
- **Changes to the build need two maintainers.** That covers the build scripts, the toolchain list and the farm's own
  files.
- **The test plan is data, not a script.** It says which scenes to play and what to measure, and nothing in it can
  run a command, whether an AI model or a person wrote it.
- **Everything runs in a sandbox.** On Linux the build and the game run under bubblewrap with no network, no access to
  your desktop session, read-only access to your game files and one folder to write in. The agent downloads the
  toolchains first, outside the sandbox, and checks each one against `tools.lock`.
- **Your agent checks every job.** Each job comes with a signature from the farm's server. Your agent checks it, and
  checks that the code it downloaded is exactly the approved commit.
- **You set the rules.** Your policy file says which jobs to take and when (see [Your policy](#your-policy)). By default
  it takes only maintainer-reviewed jobs.

What a sandbox can't fix: graphics drivers are large, and a bug in one can let code escape any sandbox. That risk is
small but real. If it matters to you, run the agent under a separate user account on your PC, and keep
`min_tier = "reviewed"`.

## What leaves your PC

- **Sent:** the report. It holds frame times, memory use, crashes, build times, a short log with your user name, home
  folder and computer name taken out, and your hardware at model level: GPU, driver version, CPU, RAM, OS.
- **Kept on your PC:** your game files, keys and saves, and every build of the game. Screenshots stay too, unless you
  turn on `upload_screenshots` and the pull request asks for them.
- **Never:** a serial number, or anything that would let someone rebuild the game.

## What you need

- A PC that can build and play Longshot: see the [system requirements](/requirements/) and
  [CONTRIBUTING.md](../CONTRIBUTING.md#development-setup). If you can build and play `main`, you're ready.
- Your own dump, as for any build.
- About 120 GB of free disk. The agent keeps a warm build of `main` so that each pull request only builds what it
  changed.
- Linux with `bwrap` (bubblewrap) installed, to take jobs from the farm. Most distributions have it as `bubblewrap`.
- An account on git.magicbean.dev. You sign in with Discord, and the first sign-in creates the account.

## Set up once

1. **Get the agent.** Download it from the
   [Beanstalk releases](https://git.magicbean.dev/MagicBeanDev/Beanstalk/releases): the AppImage on Linux, the setup
   or the portable zip on Windows. Until the first release, build it from source with the .NET 10 SDK:

   ```
   git clone https://git.magicbean.dev/MagicBeanDev/Beanstalk.git
   cd Beanstalk/agent
   dotnet run --project App
   ```

2. **Open it and read the first page.** It explains the risk above. Then click **Sign in**, then **Sign in with
   Discord** on git.magicbean.dev, and approve. The sign-in stays in the agent's folder, never anywhere else.
3. **Set it up.** Choose your game folders (`romfs` and `exefs`). The agent checks your dump is the supported version
   and reads your hardware. You can also choose one of your saves for scenes that need you to be in the game. The save
   is copied into the sandbox for each run and never uploaded. Without one, the agent skips those scenes.
4. **Check your policy,** then click **Start**. The agent builds `main` once to warm its caches, which takes a while the
   first time, then waits for jobs.

The same steps work from a terminal:

```
beanstalk-agent login                                    sign in with Discord
beanstalk-agent setup --romfs <romfs> --exefs <exefs>    check the dump, read the hardware, write the policy
beanstalk-agent serve                                    take jobs until you stop it
beanstalk-agent status                                   what it's doing, and recent jobs
```

## Your policy

The policy file is `policy.toml` in the agent's folder. The app's settings page edits the same file. Linux keeps it in
`~/.local/share/beanstalk/` and Windows in `%LOCALAPPDATA%\Beanstalk\`.

```toml
min_tier = "reviewed"        # reviewed: a maintainer approved this exact commit (the default)
                             # double-reviewed: only commits two maintainers approved
                             # untrusted is never allowed, whatever this says
allowed_authors = []         # optional: only these authors' pull requests
run_when = "idle"            # idle | always | schedule
schedule = "Mon-Fri 22:00-07:00"
max_jobs_per_day = 4
disk_budget_gb = 120
upload_screenshots = false
```

You can pause the agent from its window or the tray icon at any time. A running job stops cleanly, and the farm gives
it to another tester.

## Test one pull request yourself

To try the agent, or to test a change on a PC that doesn't take farm jobs (Windows for now), test one pull request
directly:

```
beanstalk-agent run --pr 123
```

This builds the pull request and `main` side by side, plays the test plan on both and prints the comparison. It posts
nothing. You choose what runs, so only use it on pull requests you trust.

## For pull request authors

A BigBean starts the farm on your pull request with a comment. Commands start with `/beanstalk`, or mention the
farm's account, **@Beanstalk**:

| Comment | What it does |
| --- | --- |
| `/beanstalk` or `@Beanstalk` | Approves the current commit and starts testing it (BigBeans). On a commit that already ran or was cancelled, runs it again with the same plan. |
| `@Beanstalk <what to test, in words>` | Starts it with those instructions for the plan, for example `@Beanstalk check the water in Zora's Domain`. |
| `@Beanstalk only on Steam Deck` | Hardware is chosen in words too: the plan targets PCs by tag, such as `amd`, `radv`, `windows` or `steamdeck`. |
| `/beanstalk plan <what to test, in words>` | Writes a new plan with these instructions and starts again. For example: `/beanstalk plan check VRAM in Kakariko at night`. |
| `@Beanstalk revoke` | Withdraws the approval and stops queued and running jobs. |
| `@Beanstalk cancel` | Stops queued and running jobs (BigBeans, or the pull request's author). |

The farm reacts 👀 when a BigBean's comment starts it. Only members of BigBeans can start it, on anyone's pull
request including their own. Anyone else can still ask: the farm replies, and a BigBean approves the request by reacting
👍 to it. A new push needs a new `@Beanstalk`: if the pull request changes before a request is approved, the farm
edits its reply to say so and the 👍 no longer counts.

**The plan is written for you.** When a maintainer starts the farm, an AI model reads your pull request: its title,
description and changed files. It writes the test plan: which hardware should report, where in the game to go, how
long to measure, and what counts as a failure. The farm posts that plan on the pull request in plain words and says it
has started. The plan is checked like any other: it can only choose from the scenes, probes and places the agent
already knows. If the AI can't write a valid plan, the farm uses the default plan and says why.

Want something particular tested? Say so in the pull request's description, or add `.farm/test.toml`. The planner
reads it as a hint from you. The schema and an example are in Beanstalk's
[docs/test-plan.md](https://git.magicbean.dev/MagicBeanDev/Beanstalk/src/branch/main/docs/test-plan.md). A plan can't
include saves or any other files from the game.

Results show up in two places on the pull request:

- a status check per target, such as `beanstalk/any: 1/1 pass` or `beanstalk/steamdeck: 1/1 pass`;
- one comment, which starts with the plan and fills in as reports come in. Each row links to the full report on the
  [dashboard](https://beanstalk.magicbean.dev).

Every number is measured against `main` on the same PC in the same session, which takes out most of the noise from
different hardware.
