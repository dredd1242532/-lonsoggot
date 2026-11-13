# Starting as a Longshot developer

From a fresh clone to your first pull request, in order. Each step links to the page that has the detail; this one
only puts them in order. **The rule with no exceptions applies throughout: no game files, keys or anything made from
them leave your PC** ([CONTRIBUTING.md](../CONTRIBUTING.md#the-rule-with-no-exceptions)).

## 1. Clone and run the setup

```
git clone --depth 1 https://git.magicbean.dev/MagicBeanDev/Longshot.git
cd Longshot
python -I scripts/dev_setup.py
```

`dev_setup.py` checks each step below, says what is missing, and offers to do it; it installs nothing without
asking. `--check` only reports (and changes nothing), `--yes` does every missing step, `--skip graphify,checks`
leaves steps out. Run it again at any time: what is done shows `ok`.

| Step | What it checks | Where the detail is |
|---|---|---|
| `python` | Python 3.10+ and `requirements.txt`'s packages | [CONTRIBUTING.md](../CONTRIBUTING.md#development-setup) |
| `compiler` | llvm-mingw on Windows, clang and lld on Linux | [CONTRIBUTING.md](../CONTRIBUTING.md#development-setup) |
| `headers` | the Vulkan headers some checks compile against (skipped without them, not failed) | [development.md](development.md#use) |
| `signin` | the team's one sign-in with Discord: git.magicbean.dev, the team chat and test requests | [forge.md](forge.md) |
| `fork` | your fork and its remote `forge-fork`, where your branches go | [forge.md](forge.md#set-up-pushing) |
| `graphify` | the code graph (below) | this page |
| `checks` | `python -I -B tests/run_all.py`, the game-free checks | [AGENTS.md](../AGENTS.md#make-the-change) |

A check that fails on your PC may fail on `main` too: run the same check on a clean `main` before you look for the
cause in your setup.

To build and play the game you also need your own dump and a first build:
[CONTRIBUTING.md](../CONTRIBUTING.md#development-setup).

## 2. The code graph (graphify)

[graphify](https://github.com/Graphify-Labs/graphify) (PyPI `graphifyy`, Apache-2.0) builds a graph of the code's
functions, classes and files and how they call and include each other. It answers what grep answers badly, for you
and for an agent:

```
graphify explain "ini_path()"                       what calls it, what it calls
graphify path "menu_init()" "game_module_path()"    how one reaches the other
graphify affected "gpu/vk/vk_menu.cpp"              what a change there touches
graphify query "pipeline cache" --budget 1500       the code around a subject
graphify god-nodes                                  the most connected code: the hubs
```

A name in several files (`save()`) lists them with their ids: ask again with the id. `graphify-out/graph.html` shows
the graph in a browser.

- **It reads the code only, on your PC.** `graphify update` parses the source (no LLM, no network), and git hooks
  rebuild it after each commit, checkout and merge.
- **It never reads the game.** [.graphifyignore](../.graphifyignore) leaves out everything `.gitignore` lists as the
  game's (`work/`, `romfs/`, `*.nso`, ...), as well as `third_party/` and the generated shader headers;
  `tests/test_dev_setup.py` keeps the two lists the same. Don't build a graph with the ignore file changed or
  bypassed, and don't give graphify an LLM key for `extract` or `label` on this repository.
- **It stays on your PC.** `graphify-out/` is in `.gitignore`. It is only code, but share the branch, not the graph:
  anyone can build their own in a minute.
- It is static structure: it misses data flow, function pointers and the GPU replay's tables. Confirm in the source
  before you edit or report something.

## 3. Pick something and claim it

- The open work, scored and sorted, is in [worklist.md](worklist.md): take from the top what fits your hardware.
  How to weigh it is in [workflow.md](workflow.md).
- **Read before claiming** (`python -I scripts/team_chat.py read`) so two people don't fix the same thing, then post
  the claim. The team chat is the Matrix room `#agent-updates:magicbean.dev`, mirrored to Discord `#dev-agents`
  ([AGENTS.md](../AGENTS.md#team-chat)).

## 4. Make the change, and test it against a base

- What trips people up (checks that read the source, the renderer's versioned interface, generated files, two
  platforms) is in [AGENTS.md](../AGENTS.md#make-the-change).
- **Test in the game without a new full build:** `scripts/dev_packs.py` builds `main` once as a base, then each change
  as a small pack over it, in seconds for a renderer change
  ([development.md](development.md#testing-a-change-against-a-base)). Bases, packs and runtimes are built from the
  game: they never leave your PC.

## 5. Open the pull request

```
git push forge-fork <branch>
python -I scripts/forge.py pr create <branch> --title "fix(vk): ..." --body-file <file>    opens as work in progress
python -I scripts/forge.py pr ready <n>                                                  once tested and measured
```

The title is a Conventional Commit, the description follows the template and opens with why the PR is needed, and a
PR stays work in progress until it is tested and measured ([forge.md](forge.md), [CONTRIBUTING.md](../CONTRIBUTING.md#commits-and-pull-requests)).

## 6. Testing on hardware you don't have, and for others

- **Ask:** a PR that needs a run on another GPU maker, OS, VRAM size or a Steam Deck gets a test request:
  `python -I scripts/test_requests.py request --pr <n> --summary "..." --needs "..."`, or `/request-testing` in Claude
  Code.
- **Help:** `python -I scripts/test_requests.py list` shows the open requests your PC could take; `/testing` in Claude
  Code claims one, builds the PR, walks you through the test and posts the results on the PR.

The whole flow, by hand or with an agent: [test-requests.md](test-requests.md).

## With an AI agent

Cursor and Claude Code read [AGENTS.md](../AGENTS.md) and the skills in `.claude/skills/` (`/testing`,
`/request-testing`, `/team-updates`). With the code graph built, an agent can use `graphify query`, `explain`, `path`
and `affected` to find its way before it reads widely.
