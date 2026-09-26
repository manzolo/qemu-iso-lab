"""Argument parser, internal-mode dispatcher, and main() entry point."""
from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Any

import vmctl
from vmctl import clone, config, disk_inspect, flash, import_dev, lifecycle, ui
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
     ["bootstrap-unattended", "bootstrap-omarchy", "bootstrap-preseed", "bootstrap-kickstart", "bootstrap-autoyast", "bootstrap-archinstall", "bootstrap-alpine", "bootstrap-pearos", "bootstrap-nixos", "bootstrap-windows", "bootstrap-pfsense", "bootstrap-freebsd", "bootstrap-proxmox", "bootstrap-reactos", "bootstrap-windowsxp", "bootstrap-windows2000", "bootstrap-windowsnt4", "bootstrap-windows98", "post-install", "cancel-install"]),
    ("Run", "use a VM that is already installed",
     ["start", "stop", "shell", "console", "agent", "attach"]),
    ("Libvirt", "hand an installed VM to virt-manager",
     ["export-libvirt", "unexport-libvirt"]),
    ("Network lab", "pfSense router + Pi-hole DNS + clients on an isolated LAN segment",
     ["lab"]),
    ("Groups", "a declared group (netlab, proxmox-lab...) as one stack: start, stop, network map",
     ["group"]),
    ("Verify", "smoke tests and the local validation matrix",
     ["boot-check", "check-vms", "report-pdf"]),
    ("Physical disks", "DESTRUCTIVE, ask for sudo, require --confirm-device",
     ["flash", "import-device"]),
    ("Checkpoints and clones", "full copies of a stopped VM: named checkpoints to go back to, clones as new local profiles",
     ["checkpoint", "clone"]),
    ("Maintenance", "",
     ["clean", "clean-reports", "clean-stale", "delete-iso", "completion"]),
]

TYPICAL_FLOWS = """\
typical flows:
  vmctl list                            what can I run?
  vmctl provision <vm>                  ISO + disk + installer, then click through it
  vmctl start <vm> [--headless]         boot the installed disk (add --background to detach)
  vmctl shell <vm>                      SSH into it (profiles with ssh_provision/cloud_init)
  vmctl attach <vm>                     watch the screen of a headless VM (VNC), even mid-bootstrap
  vmctl console <vm>                    serial console of a background VM (ttyS0 login, pfSense menu); Ctrl-] detaches
  vmctl bootstrap-unattended <vm>       Ubuntu: unattended install + post-install, no clicks
  vmctl bootstrap-omarchy <vm>          Omarchy: cidata install + NVIDIA post-install
  vmctl bootstrap-preseed <vm>          same for Debian  (kickstart: AlmaLinux/Fedora, archinstall: Arch, alpine: Alpine)
  vmctl bootstrap-windows <vm>          Windows 10/11: autounattend.xml install + OpenSSH post-install
  vmctl bootstrap-reactos <vm>          ReactOS: unattend.inf install (text + GUI stage), install only
  vmctl lab install                     network lab: pfSense + Pi-hole + client, then `vmctl lab up`
  vmctl checkpoint create <vm> clean-install   full copy of the stopped VM's disk + EFI vars; restore/list/delete
  vmctl clone <vm> <new-name>           independent copy as a new profile in local.json (own disk, ports, MACs)
  vmctl clean <new-name> --remove-profile   delete a clone: artifacts, checkpoints and its local.json entry
  vmctl clean <vm>                      remove its disk and generated artifacts (checkpoints stay: --checkpoints)
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
    p.add_argument("--groups", action="store_true", help="list the profile groups (categories) that check-vms --group accepts, with their members")
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

    p = _add(subparsers, "bootstrap-archinstall", help="fully automated Arch/CachyOS install + post-install via serial console (like bootstrap-unattended)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the install to complete (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_archinstall)

    p = _add(subparsers, "bootstrap-omarchy", help="fully automated Omarchy cidata install + SSH post-install")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--spice-port", type=int, help="expose the installer stage via SPICE on 127.0.0.1:PORT")
    p.add_argument("--timeout", type=int, default=1800, help="seconds for the installer to finish, then for SSH after it (default: 1800, like the other bootstraps)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_omarchy)

    p = _add(subparsers, "bootstrap-preseed", help="fully automated Debian preseed install + post-install via serial console")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the install to complete (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_preseed)

    p = _add(subparsers, "bootstrap-kickstart", help="fully automated AlmaLinux/Fedora kickstart install + post-install via serial console")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the install to complete (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_kickstart)

    p = _add(subparsers, "bootstrap-autoyast", help="fully automated openSUSE AutoYaST install + post-install via serial console")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=3600, help="seconds to wait for the install to complete (default: 3600)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_autoyast)

    p = _add(subparsers, "bootstrap-alpine", help="fully automated Alpine setup-alpine install + post-install via serial console")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the install to complete (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_alpine)

    p = _add(subparsers, "bootstrap-nixos", help="fully automated NixOS install from the profile's configuration.nix + post-install via serial console")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=3600, help="seconds to wait for the install to complete (default: 3600)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_nixos)

    p = _add(subparsers, "bootstrap-pearos", help="fully automated pearOS NiceC0re install (live squashfs unpack, like its Calamares) + post-install via serial console")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=3600, help="seconds to wait for the install to complete (default: 3600)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_pearos)

    p = _add(subparsers, "bootstrap-windows", help="fully automated Windows 10/11 autounattend install (prompt-free ISO, virtio drivers) + post-install over OpenSSH")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=3600, help="seconds to wait for the install to complete (default: 3600)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_windows)

    p = _add(subparsers, "bootstrap-freebsd", help="FreeBSD disc1 scripted install + SSH verification")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="installer and SSH timeout in seconds (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_freebsd)

    p = _add(subparsers, "bootstrap-proxmox", help="Proxmox VE automated install (answer file on the ISO) + SSH verification")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="installer and SSH timeout in seconds (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_proxmox)

    p = _add(subparsers, "bootstrap-windows98", help="fully unattended Windows 98 install (MSBATCH.INF), install only")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=5400, help="seconds to wait for the install to complete (default: 5400)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_windows98)

    p = _add(subparsers, "bootstrap-windowsnt4", help="fully unattended Windows NT 4.0 install (UNATTEND.TXT from a FreeDOS boot floppy on the CD), install only")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=5400, help="seconds to wait for the install to complete (default: 5400)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_windowsnt4)

    p = _add(subparsers, "bootstrap-windows2000", help="fully unattended Windows 2000 install (WINNT.SIF), install only")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=3600, help="seconds to wait for the install to complete (default: 3600)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_windows2000)

    p = _add(subparsers, "bootstrap-windowsxp", help="fully unattended Windows XP install (WINNT.SIF), install only")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=3600, help="seconds to wait for the install to complete (default: 3600)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_windowsxp)

    p = _add(subparsers, "bootstrap-pfsense", help="fully automated pfSense CE install for the network lab router (scripted bsdinstall, rendered config.xml)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the install to complete (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_pfsense)

    p = _add(subparsers, "bootstrap-reactos", help="fully automated ReactOS install (unattend.inf on a rebuilt BootCD, install only: no SSH server)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=1800, help="seconds to wait for the install to complete (default: 1800)")
    p.set_defaults(func=lifecycle.cmd_bootstrap_reactos)

    p = _add(subparsers, "lab", help="network lab: plan, install [--export] (router -> Pi-hole -> clients), up/down/status/check on plain QEMU, export/unexport/libvirt-test for the libvirt road, clean (disks + artifacts), attach a VM to the LAN")
    p.add_argument("action", choices=["plan", "install", "up", "down", "status", "check", "export", "unexport", "libvirt-test", "clean", "attach"], help="what to do with the lab")
    p.add_argument("vm", nargs="?", help="a lab profile to select the lab (default: the only one); for attach, the VM to connect")
    p.add_argument("--router", help="attach: the lab's pfsense profile when several labs exist")
    p.add_argument("--apply", action="store_true", help="attach: write the networks override into vms/profiles/local.json")
    p.add_argument("--timeout", type=int, default=3600, help="install: seconds per VM install (default: 3600)")
    p.add_argument("--wait", type=int, default=180, help="check/export/unexport: seconds to wait for the GUIs and SSH ports, or for virsh shutdown (default: 180)")
    p.add_argument("--connect", default="qemu:///system", help="export/unexport/libvirt-test/check: libvirt connection URI")
    p.add_argument("--replace", action="store_true", help="export: replace existing stopped libvirt domains with the same names")
    p.add_argument("--keep", action="store_true", help="libvirt-test: leave the lab defined and running in libvirt instead of unexporting it")
    p.add_argument("--export", action="store_true", help="install: once the three VMs are installed, hand the lab to libvirt (same as lab export)")
    p.add_argument("--libvirt", action="store_true", help="check: probe the LAN addresses (host on lab-lan) instead of the 127.0.0.1 forwards")
    p.set_defaults(func=lifecycle.cmd_lab)

    p = _add(subparsers, "group", help="a declared group as one stack: list the groups (--labs: only those on a network segment), status, up (infrastructure first), down (reverse), map (HTML network map, --open), install (what is missing, cumulative), clean")
    p.add_argument("action", choices=["list", "status", "up", "down", "map", "install", "clean", "cluster"], help="what to do with the group (install: what is missing, in start order, then up and the cluster; clean: stop and delete every member's disk; cluster: form the Proxmox cluster of a running stack)")
    p.add_argument("group", nargs="?", help="the group name (meta.groups), e.g. netlab or proxmox-lab")
    p.add_argument("--labs", action="store_true", help="list: only the labs (groups with a member on a segment)")
    p.add_argument("--json", action="store_true", help="list/status: machine-readable output")
    p.add_argument("--open", action="store_true", help="map: open the page in the default browser")
    p.add_argument("--output", help="map: write the page here instead of artifacts/labs/<group>/network.html")
    p.add_argument("--timeout", type=int, default=3600, help="install: seconds per member install (default: 3600)")
    p.add_argument("--yes", action="store_true", help="install/clean: do not ask before deleting disks")
    p.set_defaults(func=lifecycle.cmd_group)

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
    p.add_argument("--timeout", type=int, default=1800, help="seconds for the installer to finish, then for SSH after it (default: 1800, like the other bootstraps)")
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
    p.add_argument("--force", action="store_true", help="skip the graceful power-off and signal QEMU directly (SIGTERM, then SIGKILL)")
    p.set_defaults(func=lifecycle.cmd_stop)

    p = _add(subparsers, "cancel-install", help="cancel a TUI background installation and stop its VM, preserving disk and logs")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_cancel_install)

    for command, handler in (("export-libvirt", lifecycle.cmd_export_libvirt), ("unexport-libvirt", lifecycle.cmd_unexport_libvirt)):
        p = _add(subparsers, command, help="define an installed VM in libvirt" if command == "export-libvirt" else "remove a libvirt definition, preserving the disk")
        p.add_argument("vm", help=VM_HELP)
        p.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS, help="preview without changing files or libvirt")
        p.add_argument("--name", help="libvirt domain name (default: profile name)")
        p.add_argument("--connect", default="qemu:///system", help="libvirt connection URI")
        if command == "export-libvirt":
            p.add_argument("--no-define", action="store_true", help="only generate XML")
            p.add_argument("--replace", action="store_true", help="replace an existing stopped domain")
            p.add_argument("--autostart", action="store_true", help="enable libvirt autostart")
        p.set_defaults(func=handler)

    p = _add(subparsers, "shell", help="SSH into a running VM")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_shell)

    p = _add(subparsers, "attach", help="open the screen of a running headless VM in a VNC viewer (also during a bootstrap)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--viewer", help="viewer command to run; {url}, {host} and {port} are substituted (default: remote-viewer, vncviewer or remmina, whichever exists)")
    p.add_argument("--port", type=int, help="local TCP port for the VNC bridge (default: any free port)")
    p.add_argument("--no-viewer", action="store_true", help="only expose the display on 127.0.0.1 and print the address; Ctrl-C to detach")
    p.add_argument("--wait", type=float, default=0, help="wait up to this many seconds for a newly started VM's display")
    p.set_defaults(func=lifecycle.cmd_attach)

    p = _add(subparsers, "agent", help="talk to the QEMU guest agent of a running VM (no SSH needed)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("action", nargs="?", choices=["info", "ping", "ip", "shutdown"], default="info",
                   help="info (default): guest name and addresses; ping; ip; shutdown")
    p.set_defaults(func=lifecycle.cmd_agent)

    p = _add(subparsers, "console", help="attach the terminal to the serial console of a running background VM (login on ttyS0, pfSense menu); Ctrl-] detaches")
    p.add_argument("vm", help=VM_HELP)
    p.set_defaults(func=lifecycle.cmd_console)

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
    p.add_argument("vms", nargs="*", help="optional subset of VM profiles to test (the full matrix skips meta.status experimental profiles; naming one runs it)")
    p.add_argument("--group", action="append", metavar="NAME",
                   help="run a category instead of the whole matrix: ubuntu, ubuntu-lts, ubuntu-flavors, windows-retro, netlab, smoke, "
                        "a family (debian, fedora, rhel, windows...), a role (desktop, server) or an install flow (bootstrap-preseed...). "
                        "Repeat to add another; vmctl list --groups shows them all. Like the full matrix, a group skips experimental profiles")
    p.add_argument("--timeout", type=int, default=300, help="seconds for unattended/bootstrap and boot-check flows (default: 300)")
    p.add_argument("--parallel", default="1", metavar="N|auto",
                   help="VMs to test concurrently: a number, or 'auto' to start as many as the host's free RAM and CPUs "
                        "allow (guest RAM + QEMU overhead per VM, a reserve for the host); default: 1")
    p.add_argument("--clean-first", action="store_true", help="clean unattended/bootstrap VMs before running the matrix")
    p.add_argument("--no-clean-first", action="store_true", help="skip the unattended/bootstrap cleanup prompt and run with existing artifacts")
    p.add_argument("--restore", action="store_true", help="stash existing VM artifacts, run the matrix on a virgin state, then restore them (non-destructive alternative to --clean-first)")
    p.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS, help="preview without running the matrix")
    p.add_argument("--report", nargs="?", const="", metavar="DIR", help="write a self-contained HTML report (default: artifacts/check-vms/<timestamp>)")
    p.add_argument("--open", action="store_true", help="generate and open the report with xdg-open")
    p.add_argument("--document", action="store_true",
                   help="with --report: keep a screenshot timeline of every row (one frame every 30 s, changed screens only) "
                        "and write one PDF per profile, in English and Italian, under <report>/pdf/")
    p.set_defaults(func=lifecycle.cmd_test_local)

    p = _add(subparsers, "report-pdf", help="one PDF per profile (facts, outcome, screenshot timeline) from a check-vms report")
    p.add_argument("report_dir", nargs="?", help="report directory (default: the newest artifacts/check-vms/<timestamp> with results)")
    p.add_argument("--lang", default="en,it", help="comma-separated languages: en, it (default: en,it)")
    p.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS, help="list the files without writing them")
    p.set_defaults(func=lifecycle.cmd_report_pdf)

    p = subparsers.add_parser("_check-vm", help=argparse.SUPPRESS)
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--timeout", type=int, default=300, help=argparse.SUPPRESS)
    p.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    p.add_argument("--report-dir", dest="_report_dir", help=argparse.SUPPRESS)
    p.add_argument("--document", action="store_true", help=argparse.SUPPRESS)
    p.set_defaults(func=lifecycle.cmd_check_vm)

    p = _add(subparsers, "flash", help="copy a VM disk, repair GPT and offer optional NTFS expansion (DESTRUCTIVE; requires sudo)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--device", required=True, help="target block device, e.g. /dev/sdb")
    p.add_argument("--confirm-device", required=True, help="repeat --device exactly to confirm")
    p.add_argument("--force-target", action="store_true", help="wipe an existing partition table on the target before flashing")
    flash.add_copy_options(p)
    flash.add_expansion_options(p)
    p.set_defaults(func=flash.cmd_flash)

    p = _add(subparsers, "import-device", help="import a physical block device as a VM disk (DESTRUCTIVE; requires sudo)")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("--device", required=True, help="source block device, e.g. /dev/sdb")
    p.add_argument("--confirm-device", required=True, help="repeat --device exactly to confirm")
    import_dev.add_import_options(p)
    p.set_defaults(func=import_dev.cmd_import_device)

    p = _add(subparsers, "setup", help="verify host prerequisites; --install installs the missing ones")
    p.add_argument("--install", nargs="*", metavar="NAME", default=None,
                   help="install these tools (names as `vmctl setup` prints them, plus ovmf and textual), "
                        "or every missing one when none is named; asks first")
    p.add_argument("--yes", action="store_true", help="with --install: do not ask before running the install commands")
    p.add_argument("--verbose", "-v", action="store_true", help="one line per tool, with what it is for, instead of one per group")
    p.set_defaults(func=lifecycle.cmd_setup)

    p = _add(subparsers, "clean", help="force-stop and remove artifacts for one VM (or all VMs); checkpoints are kept unless --checkpoints")
    p.add_argument("vm", nargs="?", help=VM_HELP)
    p.add_argument("--all", action="store_true", help="clean artifacts for every configured VM")
    p.add_argument("--checkpoints", action="store_true", help="also remove the VM's checkpoints (artifacts/<vm>/checkpoints)")
    p.add_argument("--remove-profile", action="store_true",
                   help="also delete the profile from vms/profiles/local.json: for a clone or a profile that exists only there (checkpoints go too); tracked profiles are refused")
    p.set_defaults(func=lifecycle.cmd_clean)

    p = _add(subparsers, "checkpoint", help="create/list/restore/delete named copies of a stopped VM's disk + EFI vars (full qemu-img copy, survives clean)",
             epilog="""examples:
  vmctl checkpoint create debian-server clean-install --note "fresh install, verified"
  vmctl checkpoint list debian-server
  vmctl checkpoint restore debian-server clean-install        asks for confirmation; --yes for scripts
  vmctl checkpoint delete debian-server before-update --yes

The VM must be stopped, not installing and not defined in libvirt. A checkpoint is a full copy
under artifacts/<vm>/checkpoints/<name>/ (disk in the VM's format, nvram.fd for EFI profiles,
the state.json record, manifest.json): it needs no other file and stays valid whatever happens
to the current disk. Restore converts into a staging directory first and swaps with renames,
so a failure leaves the current disk in place. TPM state is not handled (profiles declaring
tpm are refused). See docs/CHECKPOINTS.md.""")
    p.add_argument("action", choices=["create", "list", "restore", "delete"], help="what to do")
    p.add_argument("vm", help=VM_HELP)
    p.add_argument("name", nargs="?", help="checkpoint name: letters, digits, '.', '_', '-' (create/restore/delete)")
    p.add_argument("--note", help="create: a free-text note kept in the manifest and shown by list")
    p.add_argument("--compress", action="store_true", help="create: compressed qcow2 copy (smaller, slower to write and to restore)")
    p.add_argument("--replace", action="store_true", help="create: overwrite an existing checkpoint with the same name")
    p.add_argument("--yes", action="store_true", help="restore/delete: do not ask for confirmation")
    p.add_argument("--json", action="store_true", help="list: machine-readable output")
    p.set_defaults(func=lifecycle.cmd_checkpoint)

    p = _add(subparsers, "clone", help="independent copy of a stopped VM as a new profile in local.json: full disk copy, EFI vars, SSH key, its own ports and MACs",
             epilog="""examples:
  vmctl clone debian-server debian-server-2
  vmctl clone debian-server debian-server-2 --identity regenerate     new hostname, machine-id, SSH host keys (Linux, over SSH)
  vmctl clone ubuntu-gnome-24.04 ubuntu-test --ssh-port 2400
  vmctl --dry-run clone debian-server debian-server-2                 the plan and the profile, nothing written

The origin must be stopped, not installing and not defined in libvirt; network lab members cannot
be cloned. The clone's profile is a complete copy with every path under artifacts/<new-name>/, the
first free host ports from 2300 for SSH and every forward, explicit MACs dropped (the default MAC
follows the disk path) and meta.clone_of. Copied: disk (qemu-img convert, no backing file), EFI
vars, the generated SSH key pair, state.json. Not copied: PID files, sockets, logs, checkpoints,
installer seeds. By default the guest keeps the origin's hostname, /etc/machine-id, SSH host keys
and static addresses (--identity keep); --identity regenerate fixes the first three for the Linux
guests this lab provisions and is refused, with advice, for Windows, pfSense, ReactOS, FreeBSD and
NixOS. A failure leaves the origin untouched and publishes no clone. See docs/CLONE.md.""")
    p.add_argument("vm", help="the origin: " + VM_HELP)
    p.add_argument("destination", help="new profile name (lowercase letters, digits, '.', '-')")
    p.add_argument("--ssh-port", type=int, help="host port for the clone's SSH forward (default: first free from 2300)")
    p.add_argument("--identity", choices=list(clone.IDENTITY_CHOICES), default="keep",
                   help="keep the guest identity (default) or regenerate hostname, machine-id and SSH host keys over SSH")
    p.add_argument("--timeout", type=int, default=300, help="regenerate: seconds to wait for the clone's SSH (default: 300)")
    p.set_defaults(func=lifecycle.cmd_clone)

    p = _add(subparsers, "clean-reports", help="remove old check-vms report directories, keeping the newest ones")
    p.add_argument("--keep", type=int, default=5, help="how many of the newest reports to keep (default: 5)")
    p.add_argument("--older-than", type=int, metavar="DAYS", help="remove only reports older than DAYS days")
    p.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS, help="list what would be removed without deleting it")
    p.set_defaults(func=lifecycle.cmd_clean_reports)

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
    export-libvirt|unexport-libvirt|lab|check-vms|clean|clean-stale|delete-iso|fetch-iso|prep|provision|install*|bootstrap-*|cancel-install|start|stop|shell|console|attach|show|post-install|boot-check|flash|import-device)
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
    if mode in {"list-empty-devices", "list-target-devices"}:
        p = argparse.ArgumentParser(prog=f"vmctl {mode}")
        p.add_argument("--json", action="store_true", help="include disk identity and partition details")
        args = p.parse_args(argv)
        if mode == "list-empty-devices":
            return disk_inspect.cmd_list_empty_devices(args)
        return disk_inspect.cmd_list_target_devices(args)
    if mode == "flash-helper":
        p = argparse.ArgumentParser(prog="vmctl flash-helper")
        p.add_argument("--vm", required=True)
        p.add_argument("--device", required=True)
        p.add_argument("--confirm-device", required=True)
        p.add_argument("--force-target", action="store_true")
        flash.add_copy_options(p)
        flash.add_expansion_options(p)
        return flash.cmd_flash_helper(p.parse_args(argv))
    if mode == "import-helper":
        p = argparse.ArgumentParser(prog="vmctl import-helper")
        p.add_argument("--vm", required=True)
        p.add_argument("--device", required=True)
        p.add_argument("--confirm-device", required=True)
        import_dev.add_import_options(p)
        return import_dev.cmd_import_helper(p.parse_args(argv))
    raise VMError(f"unknown internal mode: {mode}")


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] in INTERNAL_MODES:
        try:
            return dispatch_internal(sys.argv[1], sys.argv[2:])
        except VMError as exc:
            print(ui.style(f"error: {exc}", ui.RED, ui.BOLD), file=sys.stderr)
            return 1
        except subprocess.CalledProcessError as exc:
            print(ui.style(f"error: command failed with exit code {exc.returncode}", ui.RED, ui.BOLD), file=sys.stderr)
            return exc.returncode

    parser = build_parser()
    args = parser.parse_args()
    if getattr(args, "vm", None):
        args.vm = config.canonical_vm_name(args.vm)
    if getattr(args, "vms", None):
        args.vms = [config.canonical_vm_name(name) for name in args.vms]

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
