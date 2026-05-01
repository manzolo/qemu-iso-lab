"""Argument parser, internal-mode dispatcher, and main() entry point."""
from __future__ import annotations

import argparse
import subprocess
import sys

from vmctl import disk_inspect, flash, import_dev, lifecycle, ui
from vmctl.errors import VMError


VIDEO_CHOICES = ["safe", "std", "virtio-gl"]
VM_HELP = "VM profile name (from vms/profiles/)"
VIDEO_HELP = "QEMU display variant (defaults to the profile setting)"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="vmctl")
    parser.add_argument("--dry-run", action="store_true", help="print commands without executing them")
    subparsers = parser.add_subparsers(dest="command", required=True)

    p = subparsers.add_parser("list", help="list configured VM profiles")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.set_defaults(func=lifecycle.cmd_list)

    p = subparsers.add_parser("status", help="report local artifacts and runtime state per VM")
    p.add_argument("--all", action="store_true", help="show the full catalog, including untouched VMs")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.set_defaults(func=lifecycle.cmd_status)

    p = subparsers.add_parser("show", help="print the resolved profile for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--json", action="store_true", help="emit only the JSON body, without a header")
    p.set_defaults(func=lifecycle.cmd_show)

    p = subparsers.add_parser("fetch-iso", help="download (or validate) the ISO for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_fetch_iso)

    p = subparsers.add_parser("delete-iso", help="remove the cached ISO for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_delete_iso)

    p = subparsers.add_parser("prep", help="create disk and EFI vars for one VM, without booting")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_prep)

    p = subparsers.add_parser("provision", help="fetch ISO + prep + boot the installer in one step")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.add_argument("--no-start", action="store_true", help="prepare ISO, disk, and firmware without starting the installer")
    p.set_defaults(func=lifecycle.cmd_provision)

    p = subparsers.add_parser("install", help="boot the installer for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.add_argument("--cloud-init", action="store_true", help="attach a generated cloud-init seed ISO")
    p.set_defaults(func=lifecycle.cmd_install)

    p = subparsers.add_parser("install-unattended", help="boot the unattended installer (cloud-init/autoinstall)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--headless", action="store_true", help="run the unattended installer without a display")
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.set_defaults(func=lifecycle.cmd_install_unattended)

    p = subparsers.add_parser("bootstrap-unattended", help="run the unattended installer headless and exit when it reboots")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--headless", action="store_true", help="run the installer stage without a display")
    p.add_argument("--spice-port", type=int, help="expose the installer stage via SPICE on 127.0.0.1:PORT")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for the installer to reboot (default: 300)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_unattended)

    p = subparsers.add_parser("start", help="boot the installed disk for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--cloud-init", action="store_true", help="attach a generated cloud-init seed ISO")
    p.add_argument("--headless", action="store_true", help="start the installed guest without a display")
    p.add_argument("--background", action="store_true", help="detach the installed guest into the background")
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.set_defaults(func=lifecycle.cmd_start)

    p = subparsers.add_parser("stop", help="stop a running VM")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_stop)

    p = subparsers.add_parser("shell", help="SSH into a running VM")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_shell)

    p = subparsers.add_parser("post-install", help="run post-install SSH provisioning steps")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for SSH to become reachable (default: 300)")
    p.set_defaults(func=lifecycle.cmd_post_install)

    p = subparsers.add_parser("boot-check", help="boot the VM and watch the serial console for an expected token")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--expect", help="override the expected serial token")
    p.add_argument("--timeout", type=int, help="override the boot-check timeout in seconds")
    p.set_defaults(func=lifecycle.cmd_boot_check)

    p = subparsers.add_parser("flash", help="copy a VM disk to a physical block device (DESTRUCTIVE; requires sudo)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--device", required=True, help="target block device, e.g. /dev/sdb")
    p.add_argument("--confirm-device", required=True, help="repeat --device exactly to confirm")
    p.add_argument("--force-target", action="store_true", help="wipe an existing partition table on the target before flashing")
    p.set_defaults(func=flash.cmd_flash)

    p = subparsers.add_parser("import-device", help="import a physical block device as a VM disk (DESTRUCTIVE; requires sudo)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--device", required=True, help="source block device, e.g. /dev/sdb")
    p.add_argument("--confirm-device", required=True, help="repeat --device exactly to confirm")
    p.set_defaults(func=import_dev.cmd_import_device)

    p = subparsers.add_parser("setup", help="verify host prerequisites")
    p.set_defaults(func=lifecycle.cmd_setup)

    p = subparsers.add_parser("clean", help="remove artifacts for one VM (or all VMs)")
    p.add_argument("vm", nargs="?", help=VM_HELP)
    p.add_argument("--all", action="store_true", help="clean artifacts for every configured VM")
    p.set_defaults(func=lifecycle.cmd_clean)

    return parser


INTERNAL_MODES = {
    "flash-helper",
    "import-helper",
    "list-empty-devices",
    "list-target-devices",
}


def dispatch_internal(mode: str, argv: list[str]) -> int:
    if mode == "list-empty-devices":
        return disk_inspect.cmd_list_empty_devices(argparse.Namespace())
    if mode == "list-target-devices":
        return disk_inspect.cmd_list_target_devices(argparse.Namespace())
    if mode == "flash-helper":
        p = argparse.ArgumentParser(prog="vmctl flash-helper")
        p.add_argument("--vm", required=True)
        p.add_argument("--device", required=True)
        p.add_argument("--confirm-device", required=True)
        p.add_argument("--force-target", action="store_true")
        return flash.cmd_flash_helper(p.parse_args(argv))
    if mode == "import-helper":
        p = argparse.ArgumentParser(prog="vmctl import-helper")
        p.add_argument("--vm", required=True)
        p.add_argument("--device", required=True)
        p.add_argument("--confirm-device", required=True)
        return import_dev.cmd_import_helper(p.parse_args(argv))
    raise VMError(f"unknown internal mode: {mode}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in INTERNAL_MODES:
        try:
            return dispatch_internal(sys.argv[1], sys.argv[2:])
        except VMError as exc:
            print(ui.style(f"error: {exc}", ui.RED, ui.BOLD), file=sys.stderr)
            return 1

    parser = build_parser()
    args = parser.parse_args()

    if args.command == "clean" and not args.all and not args.vm:
        parser.error("clean requires a VM name or --all")

    try:
        return args.func(args)
    except VMError as exc:
        print(ui.style(f"error: {exc}", ui.RED, ui.BOLD), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(ui.style(f"error: command failed with exit code {exc.returncode}", ui.RED, ui.BOLD), file=sys.stderr)
        return exc.returncode
