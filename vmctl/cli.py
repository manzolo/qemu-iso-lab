"""Argument parser, internal-mode dispatcher, and main() entry point."""
from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Any

import vmctl
from vmctl import disk_inspect, flash, import_dev, lifecycle, ui
from vmctl.errors import VMError


VIDEO_CHOICES = ["safe", "std", "virtio-gl"]
VM_HELP = "VM profile name (from vms/profiles/)"
VIDEO_HELP = "QEMU display variant (defaults to the profile setting)"

# Help text of every public subcommand, filled by _add() while building the parser.
COMMAND_HELP: dict[str, str] = {}

# How `vmctl --help` presents the commands: by what you want to do, not alphabetically.
# Every public subcommand must appear in exactly one group (enforced by tests).
COMMAND_GROUPS: list[tuple[str, str, list[str]]] = [
    ("Discover", "what is configured, what exists on disk, what the host can run",
     ["list", "status", "show", "setup"]),
    ("Install by hand", "boot an installer and drive it yourself",
     ["provision", "fetch-iso", "prep", "install", "install-archinstall", "install-unattended", "install-omarchy"]),
    ("Install unattended", "headless, serial-console driven, ends with the VM installed and provisioned",
     ["bootstrap-unattended", "bootstrap-omarchy", "bootstrap-preseed", "bootstrap-kickstart", "bootstrap-archinstall", "post-install"]),
    ("Run", "use a VM that is already installed",
     ["start", "stop", "shell"]),
    ("Verify", "smoke tests and the local validation matrix",
     ["boot-check", "check-vms"]),
    ("Physical disks", "DESTRUCTIVE, ask for sudo, require --confirm-device",
     ["flash", "import-device"]),
    ("Maintenance", "",
     ["clean", "clean-stale", "delete-iso", "completion"]),
]

TYPICAL_FLOWS = """\
typical flows:
  vmctl list                            what can I run?
  vmctl provision <vm>                  ISO + disk + installer, then click through it
  vmctl start <vm> [--headless]         boot the installed disk (add --background to detach)
  vmctl shell <vm>                      SSH into it (profiles with ssh_provision/cloud_init)
  vmctl bootstrap-unattended <vm>       Ubuntu: unattended install + post-install, no clicks
  vmctl bootstrap-omarchy <vm>          Omarchy: cidata install + NVIDIA post-install
  vmctl bootstrap-preseed <vm>          same for Debian  (kickstart: AlmaLinux, archinstall: Arch)
  vmctl clean <vm>                      remove its disk and generated artifacts
  vmctl <command> --help                all options of one command
  vmtui                                 the same, as a dialog menu

profiles live in vms/profiles/*.json; personal overrides in vms/profiles/local.json (git-ignored).
"""


def _add(subparsers: "argparse._SubParsersAction[argparse.ArgumentParser]", name: str, help: str, **kwargs: Any) -> argparse.ArgumentParser:
    COMMAND_HELP[name] = help
    return subparsers.add_parser(name, help=help, description=help, formatter_class=argparse.RawDescriptionHelpFormatter, **kwargs)


def grouped_command_help() -> str:
    lines = ["QEMU/KVM lab: test VMs from JSON profiles, from ISO download to SSH provisioning.", ""]
    for title, blurb, names in COMMAND_GROUPS:
        lines.append(f"{title}:" + (f"  ({blurb})" if blurb else ""))
        for name in names:
            lines.append(f"  {name:<24}{COMMAND_HELP.get(name, '')}")
        lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vmctl",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=TYPICAL_FLOWS,
    )
    parser.add_argument("--version", action="version", version=f"vmctl {vmctl.__version__}")
    parser.add_argument("--dry-run", action="store_true", help="print commands without executing them")
    subparsers = parser.add_subparsers(dest="command", metavar="<command>", required=True, help=argparse.SUPPRESS)

    p = _add(subparsers, "list", help="list configured VM profiles")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.add_argument("--names", action="store_true", help="emit only the profile names, one per line (for scripts and shell completion)")
    p.set_defaults(func=lifecycle.cmd_list)

    p = _add(subparsers, "status", help="report local artifacts and runtime state per VM")
    p.add_argument("--all", action="store_true", help="show the full catalog, including untouched VMs")
    p.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    p.set_defaults(func=lifecycle.cmd_status)

    p = _add(subparsers, "show", help="print the resolved profile for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--json", action="store_true", help="emit only the JSON body, without a header")
    p.set_defaults(func=lifecycle.cmd_show)

    p = _add(subparsers, "fetch-iso", help="download (or validate) the ISO for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_fetch_iso)

    p = _add(subparsers, "delete-iso", help="remove the cached ISO for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_delete_iso)

    p = _add(subparsers, "prep", help="create disk and EFI vars for one VM, without booting")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_prep)

    p = _add(subparsers, "provision", help="fetch ISO + prep + boot the installer in one step")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.add_argument("--no-start", action="store_true", help="prepare ISO, disk, and firmware without starting the installer")
    p.set_defaults(func=lifecycle.cmd_provision)

    p = _add(subparsers, "install", help="boot the installer for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.add_argument("--cloud-init", action="store_true", help="attach a generated cloud-init seed ISO")
    p.set_defaults(func=lifecycle.cmd_install)

    p = _add(subparsers, "bootstrap-archinstall", help="fully automated Arch install + post-install via serial console (like bootstrap-unattended)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the install to complete (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_archinstall)

    p = _add(subparsers, "bootstrap-omarchy", help="fully automated Omarchy cidata install + SSH post-install")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--spice-port", type=int, help="expose the installer stage via SPICE on 127.0.0.1:PORT")
    p.add_argument("--timeout", type=int, default=600, help="seconds to wait for SSH after installation (default: 600)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_omarchy)

    p = _add(subparsers, "bootstrap-preseed", help="fully automated Debian preseed install + post-install via serial console")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the install to complete (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_preseed)

    p = _add(subparsers, "bootstrap-kickstart", help="fully automated AlmaLinux kickstart install + post-install via serial console")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the install to complete (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_kickstart)

    p = _add(subparsers, "install-archinstall", help="boot the Arch live ISO with a pre-built archinstall config disk")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.set_defaults(func=lifecycle.cmd_install_archinstall)

    p = _add(subparsers, "install-unattended", help="boot the Ubuntu autoinstall flow")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--headless", action="store_true", help="run the Ubuntu autoinstall flow without a display")
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.set_defaults(func=lifecycle.cmd_install_unattended)

    p = _add(subparsers, "install-omarchy", help="boot the official Omarchy ISO with an unattended cidata drive")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--headless", action="store_true", help="run the Omarchy installer without a display")
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.set_defaults(func=lifecycle.cmd_install_omarchy)

    p = _add(subparsers, "bootstrap-unattended", help="run the Ubuntu autoinstall flow headless and exit when it reboots")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--headless", action="store_true", help="run the installer stage without a display")
    p.add_argument("--spice-port", type=int, help="expose the installer stage via SPICE on 127.0.0.1:PORT")
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for the installer to reboot (default: 300)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_unattended)

    p = _add(subparsers, "start", help="boot the installed disk for one VM")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--video", choices=VIDEO_CHOICES, help=VIDEO_HELP)
    p.add_argument("--cloud-init", action="store_true", help="attach a generated cloud-init seed ISO")
    p.add_argument("--headless", action="store_true", help="start the installed guest without a display")
    p.add_argument("--background", action="store_true", help="detach the installed guest into the background")
    p.add_argument("--spice-port", type=int, help="expose a SPICE display on 127.0.0.1:PORT")
    p.set_defaults(func=lifecycle.cmd_start)

    p = _add(subparsers, "stop", help="stop a running VM")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_stop)

    p = _add(subparsers, "shell", help="SSH into a running VM")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_shell)

    p = _add(subparsers, "post-install", help="run post-install SSH provisioning steps")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=300, help="seconds to wait for SSH to become reachable (default: 300)")
    p.set_defaults(func=lifecycle.cmd_post_install)

    p = _add(subparsers, "boot-check", help="boot the VM and watch the serial console for an expected token")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--expect", help="override the expected serial token")
    p.add_argument("--timeout", type=int, help="override the boot-check timeout in seconds")
    p.set_defaults(func=lifecycle.cmd_boot_check)

    p = _add(subparsers, "check-vms", aliases=["test-local"], help="run the local VM validation matrix")
    p.add_argument("vms", nargs="*", help="optional subset of VM profiles to test")
    p.add_argument("--timeout", type=int, default=300, help="seconds for unattended/bootstrap and boot-check flows (default: 300)")
    p.add_argument("--parallel", type=int, default=1, help="number of VMs to test concurrently (default: 1)")
    p.add_argument("--clean-first", action="store_true", help="clean unattended/bootstrap VMs before running the matrix")
    p.add_argument("--no-clean-first", action="store_true", help="skip the unattended/bootstrap cleanup prompt and run with existing artifacts")
    p.set_defaults(func=lifecycle.cmd_test_local)

    p = subparsers.add_parser("_check-vm", help=argparse.SUPPRESS)
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=300, help=argparse.SUPPRESS)
    p.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=lifecycle.cmd_check_vm)

    p = _add(subparsers, "flash", help="copy a VM disk to a physical block device (DESTRUCTIVE; requires sudo)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--device", required=True, help="target block device, e.g. /dev/sdb")
    p.add_argument("--confirm-device", required=True, help="repeat --device exactly to confirm")
    p.add_argument("--force-target", action="store_true", help="wipe an existing partition table on the target before flashing")
    p.set_defaults(func=flash.cmd_flash)

    p = _add(subparsers, "import-device", help="import a physical block device as a VM disk (DESTRUCTIVE; requires sudo)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--device", required=True, help="source block device, e.g. /dev/sdb")
    p.add_argument("--confirm-device", required=True, help="repeat --device exactly to confirm")
    p.set_defaults(func=import_dev.cmd_import_device)

    p = _add(subparsers, "setup", help="verify host prerequisites")
    p.set_defaults(func=lifecycle.cmd_setup)

    p = _add(subparsers, "clean", help="remove artifacts for one VM (or all VMs)")
    p.add_argument("vm", nargs="?", help=VM_HELP)
    p.add_argument("--all", action="store_true", help="clean artifacts for every configured VM")
    p.set_defaults(func=lifecycle.cmd_clean)

    p = _add(subparsers, "clean-stale", help="remove stale runtime state such as dead bootstrap PID files")
    p.add_argument("vm", nargs="?", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_clean_stale)

    p = _add(subparsers, "completion", help="print a shell completion script (eval \"$(vmctl completion zsh)\")")
    p.add_argument("shell", choices=["bash", "zsh"], help="target shell")
    p.set_defaults(func=cmd_completion)

    parser.description = grouped_command_help()
    return parser


def public_commands() -> list[str]:
    return [name for _, _, names in COMMAND_GROUPS for name in names]


def cmd_completion(args: argparse.Namespace) -> int:
    commands = " ".join(public_commands())
    if args.shell == "zsh":
        described = " ".join(f"'{name}:{COMMAND_HELP[name].replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39)).replace(':', chr(92) + ':')}'" for name in public_commands())
        print(f"""#compdef vmctl
# Install:  eval "$(vmctl completion zsh)"   (e.g. in ~/.zshrc)
#     or:   vmctl completion zsh > ~/.zsh/completions/_vmctl   (a dir in $fpath)
_vmctl() {{
  local -a cmds vms
  cmds=({described})
  if (( CURRENT == 2 )); then
    _describe -t commands 'vmctl command' cmds
    _arguments '--dry-run[print commands without executing them]' '--version' '--help'
    return
  fi
  case $words[2] in
    completion) _values 'shell' bash zsh ;;
    check-vms|clean|clean-stale|delete-iso|fetch-iso|prep|provision|install*|bootstrap-*|start|stop|shell|show|post-install|boot-check|flash|import-device)
      vms=(${{(f)"$(vmctl list --names 2>/dev/null)"}})
      _alternative 'vms:VM profile:compadd -a vms' 'options:option:_default' ;;
    *) _default ;;
  esac
}}
compdef _vmctl vmctl""")
        return 0
    print(f"""# Install:  eval "$(vmctl completion bash)"   (e.g. in ~/.bashrc)
_vmctl() {{
  local cur=${{COMP_WORDS[COMP_CWORD]}}
  if (( COMP_CWORD == 1 )); then
    COMPREPLY=($(compgen -W "{commands} --dry-run --version --help" -- "$cur"))
  elif [[ ${{COMP_WORDS[1]}} == completion ]]; then
    COMPREPLY=($(compgen -W "bash zsh" -- "$cur"))
  else
    COMPREPLY=($(compgen -W "$(vmctl list --names 2>/dev/null)" -- "$cur"))
  fi
}}
complete -F _vmctl vmctl""")
    return 0


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
        return int(args.func(args))
    except VMError as exc:
        print(ui.style(f"error: {exc}", ui.RED, ui.BOLD), file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as exc:
        print(ui.style(f"error: command failed with exit code {exc.returncode}", ui.RED, ui.BOLD), file=sys.stderr)
        return exc.returncode
