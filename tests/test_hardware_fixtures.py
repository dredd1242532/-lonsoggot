"""Hardware pilot parsing, bounds and workflow guards: no GPU or container runtime used."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("hardware_fixtures", ROOT / "scripts/hardware_fixtures.py")
hardware = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hardware)
SHA = "a" * 40
IMAGE = "localhost/fixture@sha256:" + "b" * 64
SUMMARY = """VK_LAYER_KHRONOS_validation Khronos Validation Layer 1.4.345 version 1
GPU0:
    apiVersion = 1.3.280
    driverVersion = 1.2.3
    deviceType = PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU
    deviceName = Authored Intel GPU
    driverName = Intel open-source Mesa driver
    driverInfo = Mesa 1.2.3
    deviceUUID = never-copy-this-value
"""
DEVICE = hardware.parse_device(SUMMARY, "Intel")


def fixture_output(index):
    fixture = hardware.FIXTURES[index]
    name = "Device: " + DEVICE["deviceName"] if index == 0 else "GPU: " + DEVICE["deviceName"] + "; Vulkan 1.3.280; driver 0x123"
    return "\n".join((*hardware.ACTIVATION, name, *fixture[2], fixture[3])) + "\n"


class DeviceParsing(unittest.TestCase):
    def test_selected_device_and_driver(self):
        self.assertEqual(DEVICE["deviceName"], "Authored Intel GPU")
        self.assertEqual(DEVICE["driverInfo"], "Mesa 1.2.3")
        self.assertNotIn("deviceUUID", DEVICE)

    def test_multiple_adapters_selects_only_one(self):
        two = SUMMARY + SUMMARY.replace("GPU0:", "GPU1:").replace("Authored Intel GPU", "Authored NVIDIA GPU")
        self.assertEqual(hardware.parse_device(two, "NVIDIA")["deviceName"], "Authored NVIDIA GPU")
        with self.assertRaises(hardware.Refused):
            hardware.parse_device(two, "Authored")

    def test_unavailable_ambiguous_and_cpu_are_not_passes(self):
        for output in ("", SUMMARY * 2, SUMMARY.replace("INTEGRATED_GPU", "CPU"), SUMMARY.replace("INTEGRATED_GPU", "VIRTUAL_GPU")):
            with self.subTest(output=output), self.assertRaises(hardware.Refused):
                hardware.parse_device(output, "Intel")

    def test_missing_repeated_and_unsafe_fields_refuse(self):
        for output in (SUMMARY.replace("    driverInfo = Mesa 1.2.3\n", ""),
                       SUMMARY + "    deviceName = other\n", SUMMARY.replace("Authored Intel GPU", "GPU | <script>"),
                       SUMMARY.replace("Authored Intel GPU", "x" * 161), SUMMARY.replace("Authored Intel GPU", "GPU\x1b[31m")):
            with self.subTest(output=output), self.assertRaises(hardware.Refused):
                hardware.parse_device(output, "")

    def test_validation_layer_is_required(self):
        with self.assertRaises(hardware.Refused):
            hardware.parse_device(SUMMARY.split("\n", 1)[1], "Intel")


class FixtureParsing(unittest.TestCase):
    def test_every_authored_case_and_identity(self):
        self.assertEqual([hardware.check_fixture(f, 0, fixture_output(i), DEVICE)
                          for i, f in enumerate(hardware.FIXTURES)], [5, 1])

    def test_skip_and_exit_77_refuse(self):
        for code, extra in ((77, ""), (1, ""), (0, "SKIP: unsupported"), (0, "UNAVAILABLE: loader"), (0, "FAIL: wrong pixels")):
            with self.subTest(code=code, extra=extra), self.assertRaises(hardware.Refused):
                hardware.check_fixture(hardware.FIXTURES[0], code, fixture_output(0) + extra, DEVICE)

    def test_validation_errors_refuse(self):
        for error in ("VUID-vkQueueSubmit-test", "Validation Error: bad", "validation: bad"):
            with self.subTest(error=error), self.assertRaises(hardware.Refused):
                hardware.check_fixture(hardware.FIXTURES[0], 0, fixture_output(0) + error, DEVICE)

    def test_every_case_must_appear_exactly_once(self):
        for i, fixture in enumerate(hardware.FIXTURES):
            for case in (*fixture[2], fixture[3]):
                for output in (fixture_output(i).replace(case, ""), fixture_output(i) + case + "\n"):
                    with self.subTest(case=case), self.assertRaises(hardware.Refused):
                        hardware.check_fixture(fixture, 0, output, DEVICE)

    def test_both_activation_records_required(self):
        for marker in hardware.ACTIVATION:
            with self.subTest(marker=marker), self.assertRaises(hardware.Refused):
                hardware.check_fixture(hardware.FIXTURES[0], 0, fixture_output(0).replace(marker, ""), DEVICE)

    def test_wrong_or_repeated_selected_device_refuses(self):
        for output in (fixture_output(0).replace("Authored Intel GPU", "Other GPU"),
                       fixture_output(0) + "Device: Authored Intel GPU\n"):
            with self.subTest(output=output), self.assertRaises(hardware.Refused):
                hardware.check_fixture(hardware.FIXTURES[0], 0, output, DEVICE)


class ContainerContract(unittest.TestCase):
    def test_boundaries_and_no_secret_environment(self):
        with mock.patch.dict(os.environ, {"GITHUB_TOKEN": "never-copy-this-token", "HTTPS_PROXY": "never-copy-this-proxy"}):
            command = hardware.container_command(IMAGE, "/dev/dri/renderD128", "owned", 150,
                                                  [("/safe/source.tar", "/input/source.tar")], ["vulkaninfo", "--summary"])
        for value in ("--network=none", "--http-proxy=false", "--unsetenv-all", "--no-hosts", "--security-opt=mask=/etc/resolv.conf",
                      "--read-only", "--read-only-tmpfs=false", "--image-volume=ignore", "--cap-drop=all",
                      "--security-opt=no-new-privileges", "--userns=keep-id", "--pids-limit=128", "--memory=2g",
                      "--memory-swap=2g", "--cpus=2", "--ulimit=core=0:0", "--log-driver=none", "--timeout=150",
                      "--pull=never", "--entrypoint=/usr/bin/env", "-i"):
            self.assertIn(value, command)
        self.assertIn("type=bind,src=/safe/source.tar,dst=/input/source.tar,ro=true", command)
        self.assertNotIn("--privileged", command)
        self.assertNotIn("never-copy-this", " ".join(command))
        self.assertNotIn("DISPLAY=", " ".join(command))

    def test_digest_and_single_device_required(self):
        for image, device in (("localhost/fixture:latest", "/dev/dri/renderD128"),
                              (IMAGE, "/dev/dri"), (IMAGE, "nvidia.com/gpu=all"), (IMAGE, "/dev/sda")):
            with self.subTest(image=image, device=device), self.assertRaises(hardware.Refused):
                hardware.container_command(image, device, "owned", 30, [], [])
        hardware.container_command(IMAGE, "nvidia.com/gpu=0", "owned", 30, [], [])

    def test_cleanup_runs_after_failure(self):
        with mock.patch.object(hardware, "capture", side_effect=hardware.Refused("command timed out")), \
                mock.patch.object(hardware.subprocess, "run", return_value=mock.Mock(returncode=0)) as cleanup:
            with self.assertRaises(hardware.Refused):
                hardware.container(IMAGE, "/dev/dri/renderD128", 30, [], ["vulkaninfo", "--summary"])
        args = cleanup.call_args.args[0]
        self.assertEqual(args[:6], ["podman", "--remote=false", "rm", "--force", "--ignore", "--time=2"])
        self.assertTrue(args[-1].startswith("longshot-fixture-"))

    def test_cleanup_failure_is_not_success(self):
        with mock.patch.object(hardware, "capture", return_value=(0, "")), \
                mock.patch.object(hardware.subprocess, "run", return_value=mock.Mock(returncode=1)):
            with self.assertRaisesRegex(hardware.Refused, "cleanup failed"):
                hardware.container(IMAGE, "/dev/dri/renderD128", 30, [], [])

    @unittest.skipUnless(sys.platform == "linux", "Linux process-group and pipe bounds")
    def test_capture_exit_output_limit_and_timeout(self):
        self.assertEqual(hardware.capture([sys.executable, "-I", "-c", "print('bounded')"], 5), (0, "bounded\n"))
        with mock.patch.object(hardware, "OUTPUT_LIMIT", 32), self.assertRaisesRegex(hardware.Refused, "output limit"):
            hardware.capture([sys.executable, "-I", "-c", "print('x' * 4096)"], 5)
        with self.assertRaisesRegex(hardware.Refused, "timed out"):
            hardware.capture([sys.executable, "-I", "-c", "import time; time.sleep(30)"], 0.1)

    def test_export_excludes_untracked_and_rejects_symlinks(self):
        tree = "\n".join("100644 blob " + SHA + "    123\t" + path for path in hardware.SOURCES)
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hardware, "capture") as capture:
            output = Path(tmp) / "source.tar"
            output.write_bytes(b"test archive stand-in")
            capture.side_effect = [(0, tree), (0, "")]
            hardware.export_source(SHA, output)
            archive_args = capture.call_args_list[-1].args[0]
            self.assertEqual(archive_args[-len(hardware.SOURCES):], list(hardware.SOURCES))
            capture.side_effect = [(0, tree.replace("100644", "120000", 1))]
            with self.assertRaises(hardware.Refused):
                hardware.export_source(SHA, output)
            capture.side_effect = [(0, tree.replace("    123\t", "    9000000\t", 1))]
            with self.assertRaisesRegex(hardware.Refused, "size limit"):
                hardware.export_source(SHA, output)


class Reporting(unittest.TestCase):
    def test_row_has_existing_columns_without_raw_probe_output(self):
        text = hardware.report(SHA, DEVICE, [("copy-span", "pass", 1, "core validation active")], IMAGE)
        self.assertIn(hardware.TABLE, text)
        row = text.splitlines()[-1]
        self.assertEqual(len(row.split("|")), 10)
        self.assertIn(SHA, row)
        self.assertIn("1 authored cases passed", row)
        self.assertNotIn("never-copy-this-value", text)

    def test_no_gpu_never_calls_probe_or_container(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hardware, "capture", return_value=(0, SHA + "\n")) as capture, \
                mock.patch.object(hardware, "container", side_effect=AssertionError("GPU access forbidden")):
            output = Path(tmp) / "result.md"
            self.assertEqual(hardware.main(["--commit", SHA, "--output", str(output), "--no-gpu"]), 0)
            text = output.read_text(encoding="utf-8")
            self.assertIn("not_run", text)
            self.assertIn("not probed", text)
            self.assertNotIn("authored cases passed", text)
            self.assertEqual(capture.call_args.args[0], ["git", "rev-parse", "HEAD"])
            self.assertEqual(capture.call_count, 1)

    def test_run_orchestration_with_authored_command_results(self):
        # Stand-ins only: exercises controller flow, not Podman, a driver or GPU.
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hardware.platform, "system", return_value="Linux"), \
                mock.patch.object(hardware.os, "geteuid", return_value=1000, create=True), \
                mock.patch.object(hardware, "capture", side_effect=[(0, SHA), (0, "true v2")]), \
                mock.patch.object(hardware, "export_source") as export, \
                mock.patch.object(hardware, "container", side_effect=[
                    (0, SUMMARY), (0, fixture_output(0)), (0, fixture_output(1))]) as container:
            headers = Path(tmp) / "include"
            (headers / "vulkan").mkdir(parents=True)
            (headers / "vulkan/vulkan_core.h").write_text("authored header stand-in", encoding="utf-8")
            output = Path(tmp) / "result.md"
            args = ["--run", "--commit", SHA, "--output", str(output), "--image", IMAGE,
                    "--gpu-device", "/dev/dri/renderD128", "--device-name", "Intel", "--headers", str(headers)]
            self.assertEqual(hardware.main(args), 0)
            self.assertEqual(container.call_count, 3)
            self.assertEqual(export.call_args.args[0], SHA)
            self.assertEqual(container.call_args_list[0].args[3], [])  # Probe has no source mount.
            self.assertEqual(container.call_args_list[1].args[2], 150)
            self.assertIn(DEVICE["deviceName"], container.call_args_list[2].args[4])
            text = output.read_text(encoding="utf-8")
            self.assertIn("5 authored cases passed", text)
            self.assertIn("1 authored cases passed", text)
            self.assertNotIn("not_run", text)

    def test_missing_runtime_records_failure_without_false_counts(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hardware.platform, "system", return_value="Linux"), \
                mock.patch.object(hardware.os, "geteuid", return_value=1000, create=True), \
                mock.patch.object(hardware, "capture", side_effect=[(0, SHA), OSError("secret host path")]):
            headers = Path(tmp) / "include"
            (headers / "vulkan").mkdir(parents=True)
            (headers / "vulkan/vulkan_core.h").write_text("authored header stand-in", encoding="utf-8")
            output = Path(tmp) / "result.md"
            self.assertEqual(hardware.main(["--run", "--commit", SHA, "--output", str(output),
                                            "--image", IMAGE, "--gpu-device", "/dev/dri/renderD128",
                                            "--device-name", "Intel", "--headers", str(headers)]), 1)
            text = output.read_text(encoding="utf-8")
            self.assertIn("fail", text)
            self.assertNotIn("authored cases passed", text)
            self.assertNotIn("secret host path", text)
            self.assertNotIn("GPU execution not requested", text)

    def test_wrong_checkout_is_never_reported_as_pass(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hardware, "capture", return_value=(0, "b" * 40 + "\n")):
            output = Path(tmp) / "result.md"
            self.assertEqual(hardware.main(["--commit", SHA, "--output", str(output)]), 1)
            self.assertIn("checkout differs", output.read_text(encoding="utf-8"))

    def test_result_write_failure_does_not_expose_paths(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hardware, "capture", return_value=(0, SHA)):
            output = Path(tmp) / "missing-private-directory" / "result.md"
            with mock.patch("sys.stderr") as stderr:
                self.assertEqual(hardware.main(["--commit", SHA, "--output", str(output)]), 1)
            self.assertNotIn("missing-private-directory", str(stderr.write.call_args_list))

    def test_never_overwrites_old_result(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(hardware, "capture", side_effect=AssertionError("must not run")):
            output = Path(tmp) / "result.md"
            output.write_text("old evidence", encoding="utf-8")
            with self.assertRaises(SystemExit):
                hardware.main(["--commit", SHA, "--output", str(output)])
            self.assertEqual(output.read_text(encoding="utf-8"), "old evidence")

    def test_sha_must_be_full_and_cannot_be_a_ref(self):
        for value in ("main", "a" * 39, "a" * 41, "a" * 39 + "\n", "--help"):
            with self.subTest(value=value), self.assertRaises(hardware.argparse.ArgumentTypeError):
                hardware.commit_sha(value)


class Workflow(unittest.TestCase):
    def test_only_owner_default_dispatch_can_select_hardware(self):
        text = (ROOT / ".forgejo/workflows/hardware-fixtures.yml").read_text(encoding="utf-8")
        triggers = text.split("\non:\n", 1)[1].split("\nconcurrency:", 1)[0]
        self.assertIn("  workflow_dispatch:", triggers)
        for event in ("pull_request", "pull_request_target", "push:", "schedule:"):
            self.assertNotIn(event, triggers)
        for guard in ("github.ref == 'refs/heads/main'", "github.repository != 'MagicBeanDev/Longshot'",
                      "vars.HARDWARE_TESTS_ENABLED == 'true'", "inputs.approved_sha == github.sha",
                      "runs-on: longshot-hardware-linux", "timeout-minutes: 10", "cancel-in-progress: false",
                      'git ls-remote --exit-code', 'test "${current%%$\'\\t\'*}" = "$APPROVED_SHA"'):
            self.assertIn(guard, text)
        self.assertNotIn("secrets.", text)
        self.assertNotIn("permissions:", text)  # Forgejo does not enforce GitHub's permissions key.

    def test_actions_headers_and_single_artifact_match_main_conventions(self):
        text = (ROOT / ".forgejo/workflows/hardware-fixtures.yml").read_text(encoding="utf-8")
        self.assertEqual(text.count("persist-credentials: false"), 2)
        self.assertIn("ref: ${{ github.sha }}", text)
        self.assertIn("github-server-url: https://github.com", text)
        self.assertIn("74d8a6cb930c68ef617b202c3ff3c59d919e086b", text)
        self.assertIn("checkout@3d3c42e5aac5ba805825da76410c181273ba90b1", text)
        self.assertIn("upload-artifact@16871d9e8cfcf27ff31822cac382bbb5450f1e1e", text)
        self.assertIn("path: ${{ steps.fixtures.outputs.report }}", text)
        self.assertIn("if-no-files-found: error", text)
        self.assertIn("mktemp -d", text)
        self.assertNotIn("${{ inputs.", text.split("        run: |", 1)[1])


if __name__ == "__main__":
    unittest.main()
