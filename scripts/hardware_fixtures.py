"""Opt-in, game-free Linux GPU fixtures; default: write a not-run row without touching a GPU.

Only use --run on an owner-reviewed commit in an isolated runner account. The
controller exports just six tracked fixture inputs, then compiles and runs them
in bounded, offline rootless Podman containers.
See docs/forgejo-runners.md for the allocation boundary and remaining driver risk.
"""
import argparse
import datetime
import os
from pathlib import Path
import platform
import re
import selectors
import signal
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
LAYER = "VK_LAYER_KHRONOS_validation"
ACTIVATION = ('Insert instance layer "' + LAYER + '"', 'Inserted device layer "' + LAYER + '"')
FIXTURES = (
    ("test_wb_diff_observations_gpu.py", ("--run", "--device"), tuple(
        "PASS: " + name + ", guest/shadow parity and feedback guards" for name in (
            "off", "mixed chunks", "reused clean result", "zero count", "65535-wide 2D grid")),
     "PASS: authored GPU write-back observations"),
    ("test_vulkan_copy_span.py", ("--gpu", "--gpu-device"), (
        "PASS: 24-byte production copy layout, four GPU texels correct and padding untouched",),
     "Khronos validation: enabled"),
)
SOURCES = tuple("tests/" + fixture[0] for fixture in FIXTURES) + (
    "tests/wb_diff_observations_gpu.cpp", "tests/vulkan_copy_span_gpu.cpp",
    "gpu/vk/shaders/wb_diff_comp_spv.h", "gpu/vk/vk_xfer.cpp",
)
OUTPUT_LIMIT = 1024 * 1024
TABLE = "| Date | Build | OS / desktop | GPU / driver | CPU / RAM | Settings | Where | Result |\n" \
        "|---|---|---|---|---|---|---|---|\n"


class Refused(ValueError):
    """A fixed, public reason, never child output or a host path."""


def text_field(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9 ()._+,:-]{1,160}", value):
        raise Refused("invalid device metadata")
    return value


def commit_sha(value):
    if not re.fullmatch(r"[0-9a-f]{40}", value):
        raise argparse.ArgumentTypeError("use the full, lower-case commit SHA")
    return value


def check_output(code, output):
    if code:
        raise Refused("command failed or timed out")
    if re.search(r"^\s*(?:SKIP|UNAVAILABLE|FAIL)\b|validation error|\bVUID-|^validation:", output, re.I | re.M):
        raise Refused("fixture unavailable, failed or reported a validation error")


def parse_device(output, wanted):
    """Read only public properties from vulkaninfo --summary, not paths or UUIDs."""
    devices = []
    current = None
    keys = ("deviceName", "deviceType", "driverName", "driverInfo", "driverVersion", "apiVersion")
    for line in output.splitlines():
        if re.fullmatch(r"GPU\d+:", line.strip()):
            current = {}
            devices.append(current)
        match = re.fullmatch(r"\s*(\w+)\s*=\s*(.*?)\s*", line)
        if current is not None and match and match[1] in keys:
            if match[1] in current:
                raise Refused("repeated device metadata")
            current[match[1]] = text_field(match[2])
    matches = [d for d in devices if wanted.casefold() in d.get("deviceName", "").casefold()]
    if len(matches) != 1:
        raise Refused("device name did not select exactly one GPU")
    device = matches[0]
    if not all(device.get(key) for key in keys):
        raise Refused("incomplete device metadata")
    if device["deviceType"] not in ("PHYSICAL_DEVICE_TYPE_DISCRETE_GPU", "PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU"):
        raise Refused("selected device is not a hardware GPU")
    if not re.search(r"^" + LAYER + r"\s", output, re.M):
        raise Refused("Khronos validation layer unavailable")
    return device


def check_fixture(fixture, code, output, device):
    check_output(code, output)
    if not all(marker in output for marker in ACTIVATION):
        raise Refused("validation activation not confirmed")
    names = re.findall(r"^(?:Device|GPU): (.+?)(?:; Vulkan |$)", output, re.M)
    if names != [device["deviceName"]]:
        raise Refused("fixture ran on a different or ambiguous GPU")
    lines = output.splitlines()
    if any(lines.count(case) != 1 for case in fixture[2]) or lines.count(fixture[3]) != 1:
        raise Refused("missing or repeated fixture cases")
    return len(fixture[2])


def capture(command, seconds, env=None):
    """Bound wall time and output before decoding; never relay child output to Actions."""
    with subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True) as child:
        data = bytearray()
        deadline = time.monotonic() + seconds
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(child.stdout, selectors.EVENT_READ)
                while selector.get_map():
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise Refused("command timed out")
                    for key, _ in selector.select(left):
                        chunk = os.read(key.fileobj.fileno(), 8192)
                        if not chunk:
                            selector.unregister(key.fileobj)
                        elif len(data) + len(chunk) > OUTPUT_LIMIT:
                            raise Refused("command output limit exceeded")
                        else:
                            data.extend(chunk)
            code = child.wait(timeout=max(0.01, deadline - time.monotonic()))
        except BaseException:
            # Also reap a command which closed stdout but did not exit. A Podman
            # container has its own conmon deadline and is removed by container().
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait()
            raise
    return code, data.decode("utf-8", "replace")


def container_command(image, gpu_device, name, seconds, mounts, command):
    if len(image) > 256 or not re.fullmatch(r"[a-z0-9][a-z0-9./:_-]*@sha256:[0-9a-f]{64}", image):
        raise Refused("image must be pinned by SHA-256 digest")
    if not re.fullmatch(r"/dev/dri/renderD[0-9]+|nvidia\.com/gpu=(?:[0-9]+|GPU-[0-9a-fA-F-]+)", gpu_device):
        raise Refused("select one render node or one NVIDIA CDI GPU")
    args = ["podman", "--remote=false", "run", "--name", name, "--pull=never", "--rm",
            "--network=none", "--http-proxy=false", "--no-hosts", "--hostname=fixture",
            "--security-opt=mask=/etc/resolv.conf",
            "--read-only", "--read-only-tmpfs=false", "--image-volume=ignore", "--unsetenv-all",
            "--cap-drop=all", "--security-opt=no-new-privileges", "--userns=keep-id", "--group-add=keep-groups",
            "--pids-limit=128", "--memory=2g", "--memory-swap=2g", "--cpus=2", "--ulimit=core=0:0",
            "--tmpfs=/tmp:rw,exec,nosuid,nodev,size=512m,mode=1777", "--log-driver=none",
            "--timeout=" + str(seconds), "--stop-timeout=2", "--device=" + gpu_device,
            "--entrypoint=/usr/bin/env"]
    for source, target in mounts:
        if any(c in str(source) for c in (",", "\n", "\r")):
            raise Refused("unsupported input path")
        args += ["--mount", "type=bind,src=%s,dst=%s,ro=true" % (source, target)]
    # No inherited token, proxy, display socket, home, or image environment.
    return args + [image, "-i", "PATH=/usr/bin:/bin", "HOME=/tmp/home", "TMPDIR=/tmp", "LC_ALL=C",
                   "VK_INSTANCE_LAYERS=" + LAYER, "VK_LOADER_DEBUG=layer", "VK_LAYER_SETTINGS_PATH=/tmp", *command]


def container(image, gpu_device, seconds, mounts, command):
    name = "longshot-fixture-" + uuid.uuid4().hex
    args = container_command(image, gpu_device, name, seconds, mounts, command)
    try:
        return capture(args, seconds + 15)
    finally:
        # Owned name only. Do not print removal errors (paths and child data).
        try:
            cleanup = subprocess.run(["podman", "--remote=false", "rm", "--force", "--ignore", "--time=2", name],
                                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=10)
            if cleanup.returncode:
                raise Refused("container cleanup failed; owner must inspect it")
        except (OSError, subprocess.TimeoutExpired):
            raise Refused("container cleanup unavailable; owner must inspect it") from None


def export_source(commit, destination):
    # No checkout, .git, untracked files, dump or arbitrary directory mount. These
    # exact inputs are all the two existing fixture drivers need.
    code, tree = capture(["git", "ls-tree", "-rl", commit, "--", *SOURCES], 10)
    records = [line.split("\t", 1) for line in tree.splitlines()]
    if code or {r[1] for r in records if len(r) == 2} != set(SOURCES) or any(
            len(r) != 2 or not re.fullmatch(r"100(?:644|755) blob [0-9a-f]{40} +[0-9]+", r[0]) for r in records):
        raise Refused("fixture source must be six tracked regular files")
    if sum(int(r[0].split()[-1]) for r in records) > 8 * 1024 * 1024:
        raise Refused("fixture source exceeded its size limit")
    code, _ = capture(["git", "archive", "--format=tar", "--output=" + str(destination), commit, "--", *SOURCES], 15)
    if code or destination.stat().st_size > 16 * 1024 * 1024:
        raise Refused("fixture source export failed or exceeded its limit")


def report(commit, device, rows, image=None):
    date = datetime.datetime.now(datetime.timezone.utc).date().isoformat()
    gpu = "not probed" if device is None else "%s; %s %s; driver %s; Vulkan %s" % (
        device["deviceName"], device["driverName"], device["driverInfo"], device["driverVersion"], device["apiVersion"])
    settings = ("GPU execution refused" if any(row[1] == "fail" for row in rows) else "GPU execution not requested") \
        if image is None else "offline rootless container; core validation; " + image
    out = ["# Game-free hardware fixtures", "", "Review before copying these rows into tested configurations. "
           "No game, frame-rate or full-renderer result is claimed.", "", TABLE.rstrip()]
    for name, status, cases, reason in rows:
        result = "%s; %s" % (status, reason)
        if cases is not None:
            result += "; %d authored cases passed" % cases
        values = (date, commit, platform.system() + "; no window", gpu, "not collected", settings, name, result)
        # Only selected metadata and fixed controller messages reach this table.
        out.append("| " + " | ".join(value.replace("|", "&#124;").replace("\n", " ") for value in values) + " |")
    return "\n".join(out) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", type=commit_sha, required=True, help="full owner-approved checkout SHA")
    parser.add_argument("--output", type=Path, required=True, help="new Markdown result file; never a raw log")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--run", action="store_true", help="explicitly run on a GPU (Linux, rootless Podman)")
    mode.add_argument("--no-gpu", action="store_true", help="default: report not_run without probing hardware")
    parser.add_argument("--image", help="owner-provisioned local image, pinned with @sha256:")
    parser.add_argument("--gpu-device", help="one render node or NVIDIA CDI device, never all devices")
    parser.add_argument("--device-name", help="unique hardware device-name substring, checked against each fixture")
    parser.add_argument("--headers", type=Path, help="pinned Vulkan-Headers include directory")
    args = parser.parse_args(argv)
    if args.run and not all((args.image, args.gpu_device, args.device_name, args.headers)):
        parser.error("--run requires --image, --gpu-device, --device-name and --headers")
    if args.output.exists():
        parser.error("--output must not exist")
    rows = [("hardware fixtures", "not_run", None, "GPU probe and fixtures disabled")]
    device, image, failed = None, None, False
    try:
        code, head = capture(["git", "rev-parse", "HEAD"], 10)
        if code or head.strip() != args.commit:
            raise Refused("checkout differs from approved commit")
        if args.run:
            rows = []
            if platform.system() != "Linux" or os.geteuid() == 0:
                raise Refused("requires Linux and a non-root runner account")
            container_command(args.image, args.gpu_device, "preflight", 30, [], [])
            text_field(args.device_name)
            image = args.image
            if not (args.headers / "vulkan/vulkan_core.h").is_file():
                raise Refused("pinned Vulkan headers unavailable")
            code, info = capture(["podman", "--remote=false", "info", "--format",
                                  "{{.Host.Security.Rootless}} {{.Host.CgroupsVersion}}"], 15)
            if code or info.strip() != "true v2":
                raise Refused("requires rootless Podman and cgroup v2 limits")
            code, output = container(image, args.gpu_device, 30, [], ["vulkaninfo", "--summary"])
            check_output(code, output)
            device = parse_device(output, args.device_name)
            with tempfile.TemporaryDirectory(prefix="longshot-hardware-") as tmp:
                archive = Path(tmp) / "source.tar"
                export_source(args.commit, archive)
                mounts = [(archive, "/input/source.tar"), (args.headers.resolve(), "/headers")]
                setup = ('mkdir -p /tmp/home /tmp/source && '
                         'tar -xf /input/source.tar -C /tmp/source --no-same-owner --no-same-permissions && '
                         'printf "%s\\n" "khronos_validation.debug_action = VK_DBG_LAYER_ACTION_LOG_MSG" '
                         '"khronos_validation.log_filename = stdout" "khronos_validation.report_flags = error" '
                         '> /tmp/vk_layer_settings.txt && cd /tmp/source && exec "$@"')
                for fixture in FIXTURES:
                    command = ["/bin/sh", "-c", setup, "fixture", "python3", "-I", "-B", "tests/" + fixture[0],
                               "--cxx", "g++", "--headers", "/headers", *fixture[1], device["deviceName"]]
                    code, output = container(image, args.gpu_device, 150, mounts, command)
                    try:
                        cases = check_fixture(fixture, code, output, device)
                        rows.append((fixture[0], "pass", cases, "core validation active; no reported errors"))
                    except Refused as error:
                        rows.append((fixture[0], "fail", None, str(error)))
                        failed = True
    except (Refused, OSError, subprocess.TimeoutExpired) as error:
        reason = str(error) if isinstance(error, Refused) else "required command or input unavailable"
        rows.append(("hardware preflight or execution", "fail", None, reason))
        failed = True
    try:
        with args.output.open("x", encoding="utf-8") as stream:
            stream.write(report(args.commit, device, rows, image))
    except OSError:
        print("hardware fixtures: cannot write a new result file", file=sys.stderr)
        return 1
    print("hardware fixtures: " + ("failed" if failed else "passed" if args.run else "not_run (no GPU access)"))
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
