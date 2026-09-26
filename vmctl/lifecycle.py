"""VM lifecycle commands: list, status, install, start, stop, post-install, ..."""
from __future__ import annotations

import argparse
import copy

import json
import os
import shutil
import signal
import socket
import subprocess
import re
import shlex
import sys
import time
from pathlib import Path
from typing import Any, Iterator

from vmctl import alpine, archinstall, autoyast, checkpoint, clone, cloud_init, config, freebsd, guest_agent, haiku, host_setup, iso, labs, libvirt, netlab, nixos, omarchy, pearos, pfsense, preseed, kickstart, proxmox, pvecluster, qemu, reactos, report, profiledoc, runtime, scheduler, ssh, state, ui, vmstate, windows, windows98, windowsnt4, windowsxp
from vmctl.errors import VMError
from vmctl import tui_jobs


# --- background-VM tracking ----------------------------------------------------

ACPI_POWEROFF_GRACE_SEC = 60
REBOOT_SETTLE_SEC = 10


def bootstrap_pid_path(name: str) -> Path:
    return runtime.vm_artifact_base(name) / "runtime" / "bootstrap-start.pid"


def serial_log_path(name: str) -> Path:
    """Everything the guest prints on its serial console while running in the background (`vmctl console` log)."""
    return runtime.resolve_path(f"artifacts/{name}/logs/serial.log")


def bootstrap_log_path(name: str) -> Path:
    return runtime.vm_artifact_base(name) / "logs" / "bootstrap-start.log"


def check_vm_stdout_log_path(name: str) -> Path:
    return runtime.vm_artifact_base(name) / "logs" / "check-vms.stdout.log"


def check_vm_stderr_log_path(name: str) -> Path:
    return runtime.vm_artifact_base(name) / "logs" / "check-vms.stderr.log"


def phase_stdout_log_path(name: str, phase: str) -> Path:
    return runtime.vm_artifact_base(name) / "logs" / f"{phase}.stdout.log"


def phase_stderr_log_path(name: str, phase: str) -> Path:
    return runtime.vm_artifact_base(name) / "logs" / f"{phase}.stderr.log"


def companion_stderr_log_path(log_path: Path) -> Path:
    if log_path.suffix:
        return log_path.with_name(f"{log_path.stem}.stderr{log_path.suffix}")
    return log_path.with_name(f"{log_path.name}.stderr.log")


def announce_phase_logs(name: str, phase: str) -> tuple[Path, Path]:
    stdout_log = phase_stdout_log_path(name, phase)
    stderr_log = phase_stderr_log_path(name, phase)
    ui.print_kv("stdout", ui.pretty_path(stdout_log))
    ui.print_kv("stderr", ui.pretty_path(stderr_log))
    ui.print_note(f"tail -f {ui.pretty_path(stdout_log)}")
    return stdout_log, stderr_log




def read_pid_file(path: Path) -> int | None:
    if not path.is_file():
        return None
    try:
        value = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise VMError(f"Unable to read PID file '{path}': {exc}") from exc
    if not value:
        return None
    try:
        pid = int(value)
    except ValueError as exc:
        raise VMError(f"Invalid PID file '{path}': {value!r}") from exc
    return pid if pid > 0 else None


def process_cmdline(pid: int) -> str | None:
    path = Path("/proc") / str(pid) / "cmdline"
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    return raw.replace(b"\x00", b" ").decode(errors="replace").strip()


def local_tcp_port_open(port: int, host: str = "127.0.0.1", timeout_sec: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


def vm_ssh_host_port(vm: dict[str, Any]) -> int | None:
    ssh_cfg = cloud_init.ssh_access_config(vm)
    if ssh_cfg is None or not ssh_cfg.get("ssh_host_port"):
        return None
    return int(ssh_cfg["ssh_host_port"])


def vm_owner_paths(name: str, vm: dict[str, Any]) -> list[str]:
    """The paths only this VM's own QEMU can carry: its artifact directory and its disk."""
    paths = [str(runtime.vm_artifact_base(name))]
    disk = vm.get("disk", {}).get("path")
    if disk:
        paths.append(str(runtime.resolve_path(disk)))
    return paths


def find_qemu_process_by_hostfwd_port(port: int, owner_paths: list[str] | None = None) -> tuple[int | None, str | None]:
    """A QEMU forwarding this host port, and with `owner_paths`, only if it is this VM's own.

    The port is not proof of ownership: any process on the host can forward it, and another lab or
    another checkout using the same number would otherwise be taken for ours — `vmctl stop` would
    then power off someone else's VM. Callers that know the VM pass its paths; callers that only
    want to know who holds the port pass nothing.
    """
    needles = (f":127.0.0.1:{port}-:22", f":{port}-:22")
    for pid, cmdline in iter_qemu_processes():
        if not any(needle in cmdline for needle in needles):
            continue
        if owner_paths is not None and not any(path in cmdline for path in owner_paths):
            continue  # same host port, someone else's VM: never ours to stop
        return pid, cmdline
    return None, None


def iter_qemu_processes() -> Iterator[tuple[int, str]]:
    """Every running QEMU as (pid, command line); the one place that reads /proc."""
    for proc_dir in Path("/proc").iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            raw = (proc_dir / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode(errors="replace").strip()
        if "qemu-system-x86_64" in cmdline:
            yield int(proc_dir.name), cmdline


def find_qemu_process_by_disk_path(disk_path: Path) -> tuple[int | None, str | None]:
    needle = str(disk_path)
    for pid, cmdline in iter_qemu_processes():
        if needle in cmdline:
            return pid, cmdline
    return None, None


def is_bootstrap_vm_running(name: str) -> tuple[bool, int | None, str | None]:
    pid = read_pid_file(bootstrap_pid_path(name))
    if pid is None:
        return (False, None, None)
    cmdline = process_cmdline(pid)
    if not cmdline or "qemu-system-x86_64" not in cmdline:
        return (False, pid, cmdline)
    return (True, pid, cmdline)


def cleanup_stale_bootstrap_pid(name: str, dry_run: bool = False, emit: bool = False) -> bool:
    pid_path = bootstrap_pid_path(name)
    running, pid, _ = is_bootstrap_vm_running(name)
    if running or pid is None or not pid_path.exists():
        return False
    if emit:
        ui.print_status("warn", f"Removing stale bootstrap PID file for '{name}'", ok=False)
    if not dry_run:
        pid_path.unlink()
    return True


def warn_foreign_hostfwd(port: int | None, name: str) -> None:
    """Say who really holds the port instead of reporting nothing found."""
    if port is None:
        return
    pid, _ = find_qemu_process_by_hostfwd_port(port)
    if pid is not None:
        ui.print_status("warn", f"Host port {port} is forwarded by another QEMU (pid {pid}), "
                                f"not by '{name}': it is not this lab's VM and will not be touched", ok=False)


def vm_runtime_status(name: str, vm: dict[str, Any]) -> tuple[str, str]:
    cleanup_stale_bootstrap_pid(name, emit=False)
    running, pid, cmdline = is_bootstrap_vm_running(name)
    if running and pid is not None:
        return (f"tracked:{pid}", "-")

    disk_path = runtime.resolve_path(vm["disk"]["path"])
    pid, cmdline = find_qemu_process_by_disk_path(disk_path)
    if pid is not None:
        ssh_cfg = cloud_init.ssh_access_config(vm)
        if ssh_cfg is not None and ssh_cfg.get("ssh_host_port"):
            port = int(ssh_cfg["ssh_host_port"])
            needles = (f":127.0.0.1:{port}-:22", f":{port}-:22")
            if cmdline and any(needle in cmdline for needle in needles):
                return (f"hostfwd:{port}", f"pid={pid}")
        return (f"running:{pid}", "-")

    ssh_cfg = cloud_init.ssh_access_config(vm)
    if ssh_cfg is None or not ssh_cfg.get("ssh_host_port"):
        return ("-", "-")

    port = int(ssh_cfg["ssh_host_port"])
    return (f"closed:{port}", "-")


def stop_qemu_process(
    pid: int,
    header: str,
    description: str,
    pid_path: Path | None = None,
    dry_run: bool = False,
    qmp_socket: Path | None = None,
    ssh_poweroff_cmd: list[str] | None = None,
    grace_sec: int | None = None,
    agent_vm: dict[str, Any] | None = None,
    force: bool = False,
) -> int:
    grace = ACPI_POWEROFF_GRACE_SEC if grace_sec is None else grace_sec

    def finalize_stop(message: str) -> int:
        if pid_path is not None and pid_path.exists():
            pid_path.unlink()
        if qmp_socket is not None and qmp_socket.exists():
            qmp_socket.unlink()
        ui.print_status("ok", message)
        return 0

    ui.print_header(header)
    ui.print_kv("pid", str(pid))
    cmdline = process_cmdline(pid)
    if cmdline:
        ui.print_kv("cmd", cmdline)
    if dry_run:
        ui.print_status("ok", f"Would stop {description} (pid {pid})")
        return 0

    if force:
        # Each graceful attempt waits out its own grace period: on a Windows profile that is three
        # times 300 s before the first signal, and a guest stuck in its installer answers none of
        # them. Force goes straight to the process, still SIGTERM first so QEMU closes the qcow2.
        ui.print_status("warn", "Force: skipping the graceful power-off (unsaved guest changes are lost)", ok=False)

    # The guest agent asks the operating system directly, so it works where ACPI is ignored
    # and where there is no SSH server to fall back on (Windows 7).
    if not force and agent_vm is not None and guest_agent.enabled(agent_vm):
        ui.print_note("Asking the guest to power off (QEMU guest agent)...")
        try:
            guest_agent.shutdown(agent_vm)
        except VMError as exc:
            ui.print_status("warn", f"Guest agent shutdown failed: {exc}", ok=False)
        else:
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                if process_cmdline(pid) is None:
                    return finalize_stop(f"Stopped {description} (guest agent powered it off)")
                time.sleep(1)
            ui.print_status("warn", f"{description} is still up after the agent shutdown", ok=False)
    if not force and qmp_socket is not None and qmp_socket.exists():
        ui.print_note("Asking the guest to power off (ACPI, via QMP)...")
        if qemu.qmp_command(qmp_socket, "system_powerdown"):
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                if process_cmdline(pid) is None:
                    return finalize_stop(f"Stopped {description} (guest powered off cleanly)")
                time.sleep(1)
            ui.print_status("warn", f"{description} ignored the ACPI power-off for {grace}s", ok=False)
        else:
            ui.print_status("warn", "QMP power-off request failed", ok=False)

        if ssh_poweroff_cmd:
            # Second chance for provisioned guests: a plain `systemctl poweroff` over SSH.
            ui.print_note("Asking the guest to power off over SSH...")
            try:
                subprocess.run(ssh_poweroff_cmd, check=False, timeout=20,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except (OSError, subprocess.TimeoutExpired):
                pass
            deadline = time.monotonic() + grace
            while time.monotonic() < deadline:
                if process_cmdline(pid) is None:
                    return finalize_stop(f"Stopped {description} (guest powered off over SSH)")
                time.sleep(1)
            ui.print_status("warn", f"{description} still running after the SSH power-off request", ok=False)

        ui.print_status("warn", "Sending SIGTERM (files written in the last seconds may be lost)", ok=False)

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        raise VMError(f"Failed to stop process {pid}: {exc}") from exc

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process_cmdline(pid) is None:
            return finalize_stop(f"Stopped {description}")
        time.sleep(1)

    ui.print_status("warn", f"{description} did not exit after SIGTERM; sending SIGKILL", ok=False)
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError as exc:
        raise VMError(f"Failed to force-stop process {pid}: {exc}") from exc

    kill_deadline = time.monotonic() + 5
    while time.monotonic() < kill_deadline:
        if process_cmdline(pid) is None:
            return finalize_stop(f"Force-stopped {description}")
        time.sleep(0.5)

    raise VMError(f"Timed out force-stopping {description} (pid {pid})")


def prepare_background_vm_slot(name: str, dry_run: bool = False) -> tuple[Path, Path]:
    pid_path = bootstrap_pid_path(name)
    log_path = bootstrap_log_path(name)
    running, pid, _ = is_bootstrap_vm_running(name)
    if running:
        raise VMError(f"Background VM for '{name}' is already running (pid {pid})")
    cleanup_stale_bootstrap_pid(name, dry_run=dry_run, emit=True)
    return (pid_path, log_path)


def ensure_vm_disk(vm: dict[str, Any], dry_run: bool = False) -> Path:
    runtime.require_command("qemu-img")
    disk = vm["disk"]
    disk_path = runtime.resolve_path(disk["path"])
    if not disk_path.exists():
        runtime.ensure_parent(disk_path)
        cmd = ["qemu-img", "create", "-f", disk["format"]]
        if disk.get("subformat"):
            cmd += ["-o", f"subformat={disk['subformat']}"]
        cmd += [str(disk_path), disk["size"]]
        runtime.run(cmd, dry_run=dry_run, quiet=True)
        ui.print_status("ok", f"Created disk: {ui.pretty_path(disk_path)}")
    else:
        ui.print_status("ok", f"Disk ready: {ui.pretty_path(disk_path)}")
    for extra in qemu.extra_disks(vm):
        extra_path = runtime.resolve_path(extra["path"])
        if extra_path.exists():
            continue
        runtime.ensure_parent(extra_path)
        runtime.run(["qemu-img", "create", "-f", extra["format"], str(extra_path), extra["size"]],
                    dry_run=dry_run, quiet=True)
        ui.print_status("ok", f"Created disk: {ui.pretty_path(extra_path)}")
    return disk_path


def reset_vm_nvram(vm: dict[str, Any], dry_run: bool = False) -> None:
    fw = vm.get("firmware", {})
    if fw.get("type") != "efi":
        return
    vars_path = runtime.resolve_path(fw["vars_path"])
    if not vars_path.exists():
        return
    ui.print_status("warn", f"Resetting EFI vars store: {ui.pretty_path(vars_path)}", ok=False)
    if not dry_run:
        vars_path.unlink()


def local_test_mode(vm: dict[str, Any]) -> tuple[str, str]:
    meta = vm.get("meta", {})
    role = str(meta.get("role") or "").strip()
    if role == "import-template":
        return ("skip", "import-template profile")
    if omarchy.omarchy_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-omarchy", "Omarchy cidata install + post-install")
        return ("skip", "Omarchy cidata install without SSH post-install")
    if cloud_init.autoinstall_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-unattended", "autoinstall + post-install")
        return ("skip", "autoinstall without SSH post-install")
    if archinstall.archinstall_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-archinstall", "archinstall + post-install")
        return ("skip", "archinstall without SSH post-install")
    if preseed.preseed_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-preseed", "preseed + post-install")
        return ("skip", "preseed without SSH post-install")
    if kickstart.kickstart_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-kickstart", "kickstart + post-install")
        return ("skip", "kickstart without SSH post-install")
    if autoyast.autoyast_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-autoyast", "AutoYaST + post-install")
        return ("skip", "autoyast_config without SSH post-install")
    if alpine.alpine_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-alpine", "setup-alpine + post-install")
        return ("skip", "alpine_config without SSH post-install")
    if pearos.pearos_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-pearos", "live squashfs unpack + post-install")
        return ("skip", "pearos_config without SSH post-install")
    if nixos.nixos_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-nixos", "declarative nixos-install + post-install")
        return ("skip", "nixos_config without SSH post-install")
    if freebsd.freebsd_config(vm) is not None:
        return ("bootstrap-freebsd", "FreeBSD bsdinstall + SSH verification")
    if haiku.haiku_config(vm) is not None:
        return ("bootstrap-haiku", "Haiku live install driven over QMP + SSH verification")
    if proxmox.proxmox_config(vm) is not None:
        return ("bootstrap-proxmox", "Proxmox VE automated install + SSH verification")
    if pfsense.pfsense_config(vm) is not None:
        return ("bootstrap-pfsense", "pfSense scripted install (network lab router)")
    if reactos.reactos_config(vm) is not None:
        return ("bootstrap-reactos", "unattend.inf install only (no SSH server on ReactOS)")
    if windowsxp.windowsxp_config(vm) is not None:
        command = "bootstrap-windows2000" if windowsxp.config_section(vm) == "windows2000_config" else "bootstrap-windowsxp"
        return (command, f"WINNT.SIF install only (no SSH server on {windowsxp.product_name(vm)})")
    if windows98.windows98_config(vm) is not None:
        return ("bootstrap-windows98", "MSBATCH.INF install only (no SSH server on Windows 98)")
    if windowsnt4.windowsnt4_config(vm) is not None:
        return ("bootstrap-windowsnt4", "UNATTEND.TXT install only (no SSH server on Windows NT 4.0)")
    if windows.windows_config(vm) is not None:
        if cloud_init.ssh_access_config(vm) is not None:
            return ("bootstrap-windows", "autounattend + post-install")
        return ("bootstrap-windows", "autounattend install only (no SSH server: Windows 7)")
    ci = vm.get("ci", {})
    if isinstance(ci, dict) and ci.get("expect"):
        return ("boot-check", "serial boot expectation")
    return ("skip", "missing ci.expect for boot-check")


def pick_free_local_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def prepare_vm_for_local_test(vm_name: str, vm: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    prepared = copy.deepcopy(vm)
    disk_path = runtime.resolve_path(prepared["disk"]["path"])
    disk_pid, _ = find_qemu_process_by_disk_path(disk_path)
    if disk_pid is not None:
        return prepared, f"disk already in use by qemu pid {disk_pid}"

    notes: list[str] = []
    # Extra slirp forwards (the network lab router exposes its members' ports): another VM of the
    # matrix may hold the same host port while installing, so move the host side to a free port.
    # The guest side stays (the pfSense NAT rules point at it), only the host address changes.
    for spec in prepared.get("networks", []) or []:
        if not isinstance(spec, dict) or spec.get("type", "user") != "user":
            continue
        for fwd in spec.get("hostfwd", []) or []:
            if isinstance(fwd, dict) and fwd.get("host_port") and local_tcp_port_open(int(fwd["host_port"])):
                new_port = pick_free_local_port()
                notes.append(f"host port {fwd['host_port']} busy, forwarding {new_port} instead")
                fwd["host_port"] = new_port

    ssh_cfg = cloud_init.ssh_access_config(prepared)
    if ssh_cfg is not None and ssh_cfg.get("ssh_host_port"):
        port = int(ssh_cfg["ssh_host_port"])
        if local_tcp_port_open(port):
            new_port = pick_free_local_port()
            ssh_cfg["ssh_host_port"] = new_port
            notes.append(f"SSH host port {port} busy, using {new_port} for check-vms")
    return prepared, ("; ".join(notes) if notes else None)


def resolved_vm(args: argparse.Namespace, cfg: dict[str, Any]) -> dict[str, Any]:
    override = getattr(args, "_vm_override", None)
    if override is not None:
        return copy.deepcopy(override)
    return config.get_vm(cfg, args.vm)


def automation_accel(vm: dict[str, Any]) -> str:
    if os.environ.get("GITHUB_ACTIONS") == "true":
        ci = vm.get("ci", {})
        if isinstance(ci, dict):
            accel = str(ci.get("accel") or "").strip()
            if accel in {"kvm", "tcg"}:
                return accel
    return "kvm"


def ci_boot_accel(vm: dict[str, Any], default: str = "kvm") -> str:
    ci = vm.get("ci", {})
    if os.environ.get("GITHUB_ACTIONS") == "true" and isinstance(ci, dict):
        accel = str(ci.get("accel") or "").strip()
        if accel in {"kvm", "tcg"}:
            return accel
    return default


def local_test_prereq_skip(vm_name: str, vm: dict[str, Any]) -> str | None:
    # Windows and other ISOs without a public download URL: nothing to fetch, nothing to test.
    iso_path = runtime.resolve_path(vm["iso"])
    if not iso_path.exists() and not iso.iso_url_candidates(vm, allow_discovery=False):
        return f"skipped: ISO {iso_path.name} is not present and vmctl cannot download it (see iso_help: vmctl fetch-iso {vm_name})"

    ci = vm.get("ci", {})
    if not isinstance(ci, dict):
        return None
    if ci.get("boot_from") != "disk":
        return None

    disk_path = runtime.resolve_path(vm["disk"]["path"])
    if not disk_path.exists():
        return f"disk boot-check skipped: missing disk image for '{vm_name}'"

    try:
        actual_size = disk_path.stat().st_size
    except OSError:
        return None

    if actual_size < 1024 * 1024:
        return f"disk boot-check skipped: disk image for '{vm_name}' looks uninitialized"
    return None


def local_test_clean_candidates(selected_names: list[str], cfg: dict[str, Any]) -> list[str]:
    candidates: list[str] = []
    for vm_name in selected_names:
        vm = config.get_vm(cfg, vm_name)
        mode, _ = local_test_mode(vm)
        if mode in {"bootstrap-unattended", "bootstrap-omarchy", "bootstrap-archinstall", "bootstrap-preseed", "bootstrap-kickstart", "bootstrap-autoyast", "bootstrap-alpine", "bootstrap-pearos", "bootstrap-nixos", "bootstrap-windows", "bootstrap-pfsense", "bootstrap-freebsd", "bootstrap-haiku", "bootstrap-proxmox", "bootstrap-reactos", "bootstrap-windowsxp", "bootstrap-windows2000", "bootstrap-windowsnt4", "bootstrap-windows98"}:
            candidates.append(vm_name)
    return candidates


def maybe_clean_local_test_candidates(selected_names: list[str], cfg: dict[str, Any], args: argparse.Namespace) -> None:
    candidates = local_test_clean_candidates(selected_names, cfg)
    if not candidates:
        return
    names = ", ".join(candidates)
    if getattr(args, "no_clean_first", False):
        ui.print_note("Running check-vms without cleaning unattended/bootstrap VMs")
        return
    if not getattr(args, "clean_first", False):
        if not host_setup.prompt_yes_no_default_yes(f"Clean unattended/bootstrap VMs before check-vms? {names}"):
            ui.print_note("Running check-vms without cleaning unattended/bootstrap VMs")
            return

    ui.print_header("Clean local test VMs")
    ui.print_kv("profiles", names)
    for vm_name in candidates:
        vm = config.get_vm(cfg, vm_name)
        cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        clean_vm(vm_name, vm, dry_run=args.dry_run)


INSTALL_ONLY_SCREENSHOT_WAIT_SEC = 120
# A guest that has just been installed is still busy at its next boot - Windows runs the servicing
# pass its driver installers asked for - and a blind wait photographs whatever happens to be on the
# screen and then stops a machine in the middle of that work: the stop request lands on a guest too
# busy to answer, the grace runs out and the SIGTERM leaves the disk marked as a failed boot, so the
# boot after that opens "Avvio di Windows non riuscito" and WinRE. That is how a healthy Windows 7
# install was reported as "guest agent silent" on 2026-09-15 and 09-16: reproduced on a single row
# with no --document and no parallelism, and read back from the guest's own event log, where the
# failing disk has one system start that the same install done outside the matrix does not have.
# When the profile has an agent, wait for it to answer instead of counting seconds: that answer is
# the guest itself saying it is ready to be photographed and asked to shut down.
INSTALL_ONLY_SCREENSHOT_AGENT_WAIT_SEC = 420
INSTALL_ONLY_SCREENSHOT_POLL_SEC = 5


def boot_for_report_screenshot(vm_name: str, vm: dict[str, Any], args: argparse.Namespace) -> None:
    """Install-only flows (pfSense, Windows without SSH) end with the guest powered off, so there is
    nothing to photograph: when a report is being written, boot the installed disk headless, wait
    for it to be genuinely up, and let the caller's capture + stop do the rest."""
    if not getattr(args, "_report_dir", None) or args.dry_run:
        return
    ui.print_note(f"Report: booting the installed {vm_name} for the final screenshot")
    start_installed_vm_headless(vm_name, vm, True, dry_run=False)
    if not guest_agent.enabled(vm):
        time.sleep(INSTALL_ONLY_SCREENSHOT_WAIT_SEC)
        return
    deadline = time.monotonic() + INSTALL_ONLY_SCREENSHOT_AGENT_WAIT_SEC
    while time.monotonic() < deadline:
        if guest_agent.responds(vm, timeout=INSTALL_ONLY_SCREENSHOT_POLL_SEC):
            return
        time.sleep(INSTALL_ONLY_SCREENSHOT_POLL_SEC)
    ui.print_status("warn", f"{vm_name}: the guest agent stayed silent for "
                            f"{INSTALL_ONLY_SCREENSHOT_AGENT_WAIT_SEC}s after the install; "
                            "photographing the guest as it is")


def run_local_test_vm(
    vm_name: str,
    vm: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[str, str]:
    args._report_phase = "prerequisites"
    prepared_vm, prep_note = prepare_vm_for_local_test(vm_name, vm)
    if prep_note is not None and prep_note.startswith("disk already in use"):
        return ("skipped", prep_note)
    prereq_skip = local_test_prereq_skip(vm_name, prepared_vm)
    if prereq_skip is not None:
        return ("skipped", prereq_skip)
    mode, note = local_test_mode(prepared_vm)
    if mode == "skip":
        detail = note if prep_note is None else f"{note}; {prep_note}"
        return ("skipped", detail)
    args._report_phase = mode
    if mode == "bootstrap-unattended":
        try:
            cmd_bootstrap_unattended(
                argparse.Namespace(
                    vm=vm_name,
                    video=None,
                    timeout=args.timeout,
                    spice_port=None,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-omarchy":
        try:
            cmd_bootstrap_omarchy(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    spice_port=None,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-archinstall":
        try:
            cmd_bootstrap_archinstall(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-preseed":
        try:
            cmd_bootstrap_preseed(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-kickstart":
        try:
            cmd_bootstrap_kickstart(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-autoyast":
        try:
            cmd_bootstrap_autoyast(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode in {"bootstrap-freebsd", "bootstrap-haiku", "bootstrap-proxmox"}:
        handler = {"bootstrap-freebsd": cmd_bootstrap_freebsd, "bootstrap-haiku": cmd_bootstrap_haiku,
                   "bootstrap-proxmox": cmd_bootstrap_proxmox}[mode]
        try:
            handler(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-alpine":
        try:
            cmd_bootstrap_alpine(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-pearos":
        try:
            cmd_bootstrap_pearos(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-nixos":
        try:
            cmd_bootstrap_nixos(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-windows":
        try:
            cmd_bootstrap_windows(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            if cloud_init.ssh_access_config(prepared_vm) is None:
                boot_for_report_screenshot(vm_name, prepared_vm, args)
            args._report_phase = "post-install"
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-pfsense":
        try:
            cmd_bootstrap_pfsense(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            boot_for_report_screenshot(vm_name, prepared_vm, args)
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-windows98":
        try:
            cmd_bootstrap_windows98(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            boot_for_report_screenshot(vm_name, prepared_vm, args)
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-windowsnt4":
        try:
            cmd_bootstrap_windowsnt4(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            boot_for_report_screenshot(vm_name, prepared_vm, args)
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode in {"bootstrap-windowsxp", "bootstrap-windows2000"}:
        try:
            cmd_bootstrap_windowsxp(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            boot_for_report_screenshot(vm_name, prepared_vm, args)
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "bootstrap-reactos":
        try:
            cmd_bootstrap_reactos(
                argparse.Namespace(
                    vm=vm_name,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
            boot_for_report_screenshot(vm_name, prepared_vm, args)
        finally:
            report.capture(vm_name, prepared_vm, args)
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "boot-check":
        ci = prepared_vm.get("ci", {})
        if not (isinstance(ci, dict) and ci.get("boot_from") == "disk"):
            # A live/ISO boot-check still attaches the profile disk: create the empty image the
            # way `vmctl prep` does, so the matrix never fails on "Disk image not found".
            ensure_vm_disk(prepared_vm, dry_run=args.dry_run)
        with report.watch_boot(vm_name, prepared_vm, args):
            cmd_boot_check(
                argparse.Namespace(
                    vm=vm_name,
                    expect=None,
                    timeout=args.timeout,
                    dry_run=args.dry_run,
                    _vm_override=prepared_vm,
                    _report_parent=args,
                )
            )
        detail = note if prep_note is None else f"{note}; {prep_note}"
        return ("passed", detail)
    detail = note if prep_note is None else f"{note}; {prep_note}"
    return ("skipped", detail)


def run_local_test_once(vm_name: str, vm: dict[str, Any], args: argparse.Namespace) -> tuple[str, str]:
    mode, note = local_test_mode(vm)
    ui.print_header(f"Test VM: {vm_name}")
    ui.print_kv("mode", mode)
    ui.print_kv("check", note)
    started = time.monotonic()
    args._screenshot_error = None
    # The phase names the timeline frames until a flow says otherwise ("post-install").
    report.phase(args, "boot" if mode == "boot-check" else "install")
    try:
        with report.watch_timeline(vm_name, vm, args):
            status, detail = run_local_test_vm(vm_name, vm, args)
    except (VMError, OSError, subprocess.CalledProcessError) as exc:
        status = "failed"
        detail = str(exc)

    report.record(vm_name, vm, args, status, detail, time.monotonic() - started, mode)
    write_profile_sheet(vm_name, args)
    if status == "passed":
        ui.print_status("ok", f"{vm_name}: {detail}")
    elif status == "failed":
        ui.print_status("fail", f"{vm_name}: {detail}", ok=False)
    else:
        ui.print_status("warn", f"{vm_name}: {detail}", ok=False)
    return (status, detail)


def parse_check_vm_result(output: str) -> tuple[str, str]:
    marker = "__VMCTL_CHECK_VM_RESULT__"
    for line in reversed(output.splitlines()):
        if not line.startswith(marker):
            continue
        payload = json.loads(line[len(marker):])
        status = str(payload["status"])
        detail = str(payload["detail"])
        return status, detail
    raise VMError("worker result marker missing from check-vm output")


def strip_check_vm_result(output: str) -> str:
    marker = "__VMCTL_CHECK_VM_RESULT__"
    lines = [line for line in output.splitlines() if not line.startswith(marker)]
    if not lines:
        return ""
    return "\n".join(lines) + "\n"


def write_check_vm_log(path: Path, content: str) -> None:
    runtime.ensure_parent(path)
    path.write_text(content, encoding="utf-8")


def run_local_test_vm_subprocess(vm_name: str, args: argparse.Namespace) -> tuple[str, str, str]:
    stdout_log = check_vm_stdout_log_path(vm_name)
    stderr_log = check_vm_stderr_log_path(vm_name)
    cmd = [
        sys.executable,
        str(state.ROOT / "bin" / "vmctl"),
        "_check-vm",
        vm_name,
        "--timeout",
        str(args.timeout),
    ]
    if getattr(args, "dry_run", False):
        cmd.append("--dry-run")
    if getattr(args, "_report_dir", None):
        cmd += ["--report-dir", str(args._report_dir)]
    if getattr(args, "document", False):
        cmd.append("--document")
    result = subprocess.run(
        cmd,
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    stdout_output = result.stdout or ""
    stderr_output = result.stderr or ""
    write_check_vm_log(stdout_log, stdout_output)
    write_check_vm_log(stderr_log, stderr_output)
    try:
        status, detail = parse_check_vm_result(stdout_output)
    except VMError:
        detail_source = stderr_output.strip() or stdout_output.strip() or f"worker exited with code {result.returncode}"
        detail = detail_source.splitlines()[-1]
        status = "failed"
    return status, detail, strip_check_vm_result(stdout_output)


# --- info commands -------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    cfg = config.load_config()

    if getattr(args, "groups", False):
        return print_groups(cfg, args)

    if getattr(args, "names", False):
        for name in config.sorted_vm_names(cfg):
            print(name)
        return 0

    if getattr(args, "json", False):
        out = []
        for name, vm in config.sorted_vm_items(cfg):
            out.append({
                "profile": name,
                "name": vm.get("name", name),
                "firmware": vm.get("firmware", {}).get("type"),
                "memory_mb": vm.get("memory_mb"),
                "cpus": vm.get("cpus"),
                "status": vm.get("meta", {}).get("status", "manual"),
                "verified": vm.get("meta", {}).get("verified"),
            })
        print(json.dumps(out, indent=2))
        return 0

    rows = []
    for name, vm in config.sorted_vm_items(cfg):
        firmware = vm.get("firmware", {}).get("type", "?").upper()
        memory = vm.get("memory_mb", "?")
        cpus = vm.get("cpus", "?")
        meta = vm.get("meta", {})
        rows.append((name, vm.get("name", name), firmware, str(memory), str(cpus),
                     str(meta.get("status", "manual")), str(meta.get("verified") or "-")))

    name_width = max(len("PROFILE"), max(len(row[0]) for row in rows))
    label_width = max(len("NAME"), max(len(row[1]) for row in rows))
    firmware_width = max(len("FW"), max(len(row[2]) for row in rows))
    memory_width = max(len("RAM"), max(len(f"{row[3]}M") for row in rows))
    cpu_width = max(len("CPU"), max(len(row[4]) for row in rows))
    status_width = max(len("STATUS"), max(len(row[5]) for row in rows))
    print(f"{ui.style('PROFILE', ui.BOLD, ui.CYAN):<{name_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('NAME', ui.BOLD, ui.CYAN):<{label_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('FW', ui.BOLD, ui.CYAN):>{firmware_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('RAM', ui.BOLD, ui.CYAN):>{memory_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('CPU', ui.BOLD, ui.CYAN):>{cpu_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{'STATUS':<{status_width}}  LAST LIVE PASS")
    for name, label, firmware, memory, cpus, status, verified in rows:
        print(f"{ui.style(name, ui.BOLD):<{name_width + len(ui.BOLD) + len(ui.RESET)}}  "
              f"{label:<{label_width}}  "
              f"{firmware:>{firmware_width}}  "
              f"{f'{memory}M':>{memory_width}}  "
              f"{cpus:>{cpu_width}}  {status:<{status_width}}  {verified}")
    return 0


def disk_status(vm: dict[str, Any]) -> tuple[str, str, str]:
    """``(missing|ready, bytes on the host, virtual capacity)`` as formatted strings.

    The host figure is the allocated blocks of the image (what ``du`` reports), not its
    apparent size: a sparse raw image is as large as its capacity on paper and nearly empty
    on disk. Neither number is the guest filesystem's used or free space.
    """
    facts = vmstate.disk_facts(vm)
    if not facts["present"]:
        return "missing", "-", "-"
    virtual_size = runtime.format_bytes(facts["virtual_bytes"]) if facts["virtual_bytes"] else "?"
    return "ready", runtime.format_bytes(facts["host_bytes"]), virtual_size


def iso_status(vm: dict[str, Any]) -> str:
    iso_path = runtime.resolve_path(vm["iso"])
    return "ready" if iso_path.is_file() else "missing"


def nvram_status(vm: dict[str, Any]) -> str:
    firmware = vm.get("firmware", {})
    if firmware.get("type") != "efi":
        return "-"
    vars_path = runtime.resolve_path(firmware["vars_path"])
    return "ready" if vars_path.is_file() else "missing"


def vm_has_local_state(vm: dict[str, Any]) -> bool:
    disk_path = runtime.resolve_path(vm["disk"]["path"])
    if disk_path.exists():
        return True
    iso_path = runtime.resolve_path(vm["iso"])
    if iso_path.exists():
        return True
    firmware = vm.get("firmware", {})
    if firmware.get("type") == "efi":
        vars_path = runtime.resolve_path(firmware["vars_path"])
        if vars_path.exists():
            return True
    return False


def format_runtime_cell(runtime_str: str, runtime_note: str) -> str:
    if runtime_note == "-" or not runtime_note:
        return runtime_str
    return f"{runtime_str} ({runtime_note})"


def status_cell_style(value: str) -> tuple[str, ...]:
    if value in {"ready", vmstate.LABEL_VERIFIED}:
        return (ui.GREEN, ui.BOLD)
    if value in {"missing", vmstate.LABEL_UNVERIFIED, vmstate.LABEL_INCOMPLETE}:
        return (ui.YELLOW, ui.BOLD)
    if value == vmstate.LABEL_INSTALLED:
        return (ui.CYAN, ui.BOLD)
    if value.startswith(("tracked:", "hostfwd:", "open:")):
        return (ui.GREEN, ui.BOLD)
    if value.startswith(("closed:",)):
        return (ui.YELLOW, ui.BOLD)
    if value == "?":
        return (ui.YELLOW, ui.BOLD)
    return ()


def style_status_cell(value: str, width: int, align: str = "<") -> str:
    padded = f"{value:{align}{width}}"
    codes = status_cell_style(value)
    if not codes:
        return padded
    return ui.style(padded, *codes)


def cmd_status(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    rows = []
    for name, vm in config.sorted_vm_items(cfg):
        if not args.all and not vm_has_local_state(vm):
            continue
        disk, actual, virtual = disk_status(vm)
        runtime_str, runtime_note = vm_runtime_status(name, vm)
        runtime_cell = format_runtime_cell(runtime_str, runtime_note)
        known = vmstate.summary(name, vm)
        rows.append((name, disk, iso_status(vm), nvram_status(vm), runtime_cell, actual, virtual, runtime_note, known))

    if getattr(args, "json", False):
        out = [
            {
                "profile": name,
                "disk": disk,
                "iso": iso_str,
                "nvram": nvram,
                "runtime": runtime_str,
                # Bytes the image occupies on the host and its virtual capacity: neither is
                # the guest filesystem's used or free space.
                "host_size": actual,
                "virtual_size": virtual,
                "host_bytes": known["host_bytes"],
                "virtual_bytes": known["virtual_bytes"],
                "install": known["label"],
                "install_detail": known["detail"],
                "install_state": known["install_state"],
                "install_flow": known["install_flow"],
                "install_at": known["install_at"],
                "verified": known["verified"],
                "verify_kind": known["verify_kind"],
                "verify_at": known["verify_at"],
                "origin": known["origin_kind"],
                "runtime_note": runtime_note if runtime_note != "-" else None,
            }
            for name, disk, iso_str, nvram, runtime_str, actual, virtual, runtime_note, known in rows
        ]
        print(json.dumps(out, indent=2))
        return 0

    if not rows:
        ui.print_status("ok", "No local VM state found. Use --all to show the full catalog.")
        return 0

    name_width = max(len("PROFILE"), max(len(row[0]) for row in rows))
    disk_width = max(len("DISK"), max(len(row[1]) for row in rows))
    iso_width = max(len("ISO"), max(len(row[2]) for row in rows))
    nvram_width = max(len("NVRAM"), max(len(row[3]) for row in rows))
    runtime_width = max(len("RUNTIME"), max(len(row[4]) for row in rows))
    actual_width = max(len("ON HOST"), max(len(row[5]) for row in rows))
    virtual_width = max(len("CAPACITY"), max(len(row[6]) for row in rows))
    install_width = max(len("INSTALL"), max(len(row[8]["label"]) for row in rows))

    # ON HOST is what the image occupies on the host, CAPACITY the disk the guest sees;
    # INSTALL is what is known about the disk (vmstate.summary), not the profile's history.
    print(f"{ui.style('PROFILE', ui.BOLD, ui.CYAN):<{name_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('DISK', ui.BOLD, ui.CYAN):<{disk_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('INSTALL', ui.BOLD, ui.CYAN):<{install_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('ISO', ui.BOLD, ui.CYAN):<{iso_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('NVRAM', ui.BOLD, ui.CYAN):<{nvram_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('RUNTIME', ui.BOLD, ui.CYAN):<{runtime_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('ON HOST', ui.BOLD, ui.CYAN):>{actual_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('CAPACITY', ui.BOLD, ui.CYAN):>{virtual_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}")
    for name, disk, iso_str, nvram, runtime_str, actual, virtual, runtime_note, known in rows:
        print(f"{ui.style(name, ui.BOLD):<{name_width + len(ui.BOLD) + len(ui.RESET)}}  "
              f"{style_status_cell(disk, disk_width)}  "
              f"{style_status_cell(known['label'], install_width)}  "
              f"{style_status_cell(iso_str, iso_width)}  "
              f"{style_status_cell(nvram, nvram_width)}  "
              f"{style_status_cell(runtime_str, runtime_width)}  "
              f"{actual:>{actual_width}}  "
              f"{virtual:>{virtual_width}}")
    return 0


def print_groups(cfg: dict[str, Any], args: argparse.Namespace) -> int:
    """``vmctl list --groups``: the categories check-vms --group accepts, and who is in them."""
    index = group_index(cfg)
    sources = group_sources(cfg)
    if getattr(args, "json", False):
        print(json.dumps([{"group": group, "sources": sources[group], "count": len(names), "profiles": names}
                          for group, names in index.items()], indent=2))
        return 0
    ui.print_header("Profile groups")
    ui.print_note("vmctl check-vms --group <name> runs one; repeat --group to add another.")
    ui.print_note("declared = meta.groups in the profile; the others follow meta.family, meta.status, meta.role and the install flow.")
    width = max(len("GROUP"), max(len(group) for group in index))
    source_width = max(len("FROM"), max(len(",".join(found)) for found in sources.values()))
    print(f"\n{ui.style('GROUP', ui.BOLD, ui.CYAN):<{width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('VMS', ui.BOLD, ui.CYAN):>{3 + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('FROM', ui.BOLD, ui.CYAN):<{source_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('PROFILES', ui.BOLD, ui.CYAN)}")
    for group, names in index.items():
        members = ", ".join(names)
        if len(members) > 60:
            members = f"{names[0]}, {names[1]}, ... (+{len(names) - 2} more)"
        print(f"{ui.style(group, ui.BOLD):<{width + len(ui.BOLD) + len(ui.RESET)}}  "
              f"{len(names):>3}  {','.join(sources[group]):<{source_width}}  {members}")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    if not getattr(args, "json", False):
        ui.print_header(f"VM profile: {args.vm}")
    print(json.dumps(vm, indent=2))
    return 0


def cmd_fetch_iso(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    iso.ensure_iso(vm, dry_run=args.dry_run)
    return 0


def cmd_delete_iso(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    iso_path = runtime.resolve_path(vm["iso"])
    partial_path = iso_path.with_name(iso_path.name + ".part")

    removed = False
    for path in [iso_path, partial_path]:
        if path.exists():
            if not path.is_file():
                raise VMError(f"ISO path exists but is not a regular file: {path}")
            ui.print_note(f"Removing {ui.pretty_path(path)}")
            if not args.dry_run:
                path.unlink()
            removed = True

    if removed:
        ui.print_status("ok", f"ISO cache removed for '{args.vm}'")
    else:
        ui.print_status("ok", f"No cached ISO found for '{args.vm}'")
    return 0


# --- prep / install / start ----------------------------------------------------

def cmd_prep(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.ensure_vm_dirs(args.vm)
    iso.ensure_iso(vm, dry_run=args.dry_run)
    ensure_vm_disk(vm, dry_run=args.dry_run)
    qemu.firmware_args(vm, dry_run=args.dry_run)
    ui.print_status("ok", f"Prepared VM '{args.vm}'")
    return 0


def cmd_provision(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.ensure_vm_dirs(args.vm)

    ui.print_header(f"Provision VM: {args.vm}")
    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_path = runtime.resolve_path(vm["disk"]["path"])
    disk_exists = disk_path.exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)

    if args.no_start:
        qemu.firmware_args(vm, dry_run=args.dry_run)
        ui.print_status("ok", f"Provisioned VM '{args.vm}' without starting the installer")
        return 0

    running, bg_pid, _ = is_bootstrap_vm_running(args.vm)
    if not running:
        ssh_cfg = cloud_init.ssh_access_config(vm)
        if ssh_cfg and ssh_cfg.get("ssh_host_port"):
            bg_pid, _ = find_qemu_process_by_hostfwd_port(int(ssh_cfg["ssh_host_port"]))
            running = bg_pid is not None
    if running and bg_pid is not None:
        ui.print_status("warn", f"VM '{args.vm}' is already running headless (pid {bg_pid})", ok=False)
        ui.print_note(f"  vmctl shell {args.vm}              — open an SSH session")
        ui.print_note(f"  vmctl stop {args.vm} && vmctl start {args.vm}  — restart with display")
        return 1

    qemu_args = qemu.common_args(
        vm,
        qemu.installer_video_variant(vm, args.video),
        dry_run=args.dry_run,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        spice_port=getattr(args, "spice_port", None),
    )
    qemu_args += ["-cdrom", str(iso_path)]
    vmstate.begin_install(args.vm, "provision", interactive=True, dry_run=args.dry_run)
    runtime.run(qemu_args, dry_run=args.dry_run)
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.ensure_vm_dirs(args.vm)
    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    qemu_args = qemu.common_args(
        vm,
        qemu.installer_video_variant(vm, args.video),
        dry_run=args.dry_run,
        enable_clipboard=False,
        spice_port=getattr(args, "spice_port", None),
    )
    qemu_args += ["-cdrom", str(iso_path)]
    if args.cloud_init:
        qemu_args += cloud_init.cloud_init_drive_args(cloud_init.create_cloud_init_seed(args.vm, vm, dry_run=args.dry_run))
    stdout_log, stderr_log = announce_phase_logs(args.vm, "install")
    vmstate.begin_install(args.vm, "install", interactive=True, dry_run=args.dry_run)
    runtime.run(qemu_args, dry_run=args.dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    return 0


def explain_failed_bootstrap(exc: VMError, failed_token: str, flow: str, serial_log: Path) -> VMError:
    """The install script's own failure report, when it is in the captured serial output.

    A live-system install script that fails now prints ``failed_token`` and powers off, so
    QEMU exits without the success token and ``run_and_expect`` raises with the output. Turn
    that into the guest's own line rather than a bare "exited before emitting": the mirror
    stall that broke pacstrap is the useful part.
    """
    if failed_token not in str(exc):
        return exc
    failed_line = next((line for line in str(exc).splitlines() if failed_token in line), "")
    return VMError(f"{flow} install script reported a failure: {failed_line.strip()} "
                   f"(full serial output in {ui.pretty_path(serial_log)})")


def cmd_bootstrap_archinstall(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if archinstall.archinstall_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define archinstall_config")

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap Arch (automated): {args.vm}")

    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-archinstall", dry_run=args.dry_run)
    reset_vm_nvram(vm, dry_run=args.dry_run)

    bootstrap_iso = archinstall.create_bootstrap_iso(args.vm, vm, dry_run=args.dry_run)
    kernel_path, initrd_path = iso.extract_arch_installer_boot_artifacts(vm, iso_path, dry_run=args.dry_run)
    iso_label = archinstall.arch_iso_label(iso_path)

    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        network_phase="install",
    )
    install_qemu_args += ["-cdrom", str(iso_path)]
    install_qemu_args += archinstall.config_iso_drive_args(bootstrap_iso)
    install_qemu_args += [
        "-kernel", str(kernel_path),
        "-initrd", str(initrd_path),
        "-append", archinstall.live_kernel_append(vm, iso_label),
    ]

    login_prompt, shell_prompt = archinstall.live_prompts(vm)
    trigger = "mkdir -p /tmp/archconf && mount /dev/vdb /tmp/archconf && bash /tmp/archconf/run.sh"
    ui.print_note("Booting the live ISO — waiting for shell, then triggering automated install...")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    try:
        qemu.run_and_expect(
            install_qemu_args,
            expected_text=archinstall.BOOTSTRAP_COMPLETE_TOKEN,
            timeout_sec=getattr(args, "timeout", 1800),
            auto_inputs=[
                (login_prompt, "root\n"),
                (shell_prompt, f"\n{trigger}\n"),
            ],
            dry_run=args.dry_run,
            log_path=serial_log,
        )
    except VMError as exc:
        raise explain_failed_bootstrap(exc, archinstall.BOOTSTRAP_FAILED_TOKEN, "Arch", serial_log) from exc
    vmstate.complete_install(args.vm, "bootstrap-archinstall", vm, dry_run=args.dry_run)
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    post_serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/post-install-serial.log")
    run_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        network_phase="install",
        serial_socket=qemu.serial_socket_path(vm),
        serial_log=post_serial_log,
    )
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(run_qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

    report.phase(args, "post-install")
    run_post_install(args.vm, vm, getattr(args, "timeout", 300), dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
    return 0


def cmd_bootstrap_alpine(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if alpine.alpine_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define alpine_config")

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap Alpine (setup-alpine): {args.vm}")

    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-alpine", dry_run=args.dry_run)
    reset_vm_nvram(vm, dry_run=args.dry_run)

    seed_iso = alpine.create_alpine_seed_iso(args.vm, vm, dry_run=args.dry_run)
    kernel_path, initrd_path = alpine.extract_alpine_boot_artifacts(vm, iso_path, dry_run=args.dry_run)

    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        network_phase="install",
    )
    install_qemu_args += ["-cdrom", str(iso_path)]
    install_qemu_args += alpine.seed_iso_drive_args(seed_iso)
    install_qemu_args += [
        "-kernel", str(kernel_path),
        "-initrd", str(initrd_path),
        "-append", alpine.LIVE_KERNEL_APPEND,
    ]

    ui.print_note("Booting Alpine live ISO — waiting for the root prompt, then running setup-alpine...")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    try:
        qemu.run_and_expect(
            install_qemu_args,
            expected_text=alpine.BOOTSTRAP_COMPLETE_TOKEN,
            timeout_sec=getattr(args, "timeout", 1800),
            auto_inputs=[
                (alpine.ALPINE_SERIAL_LOGIN_PROMPT, "root\n"),
                (alpine.ALPINE_LIVE_PROMPT, f"\n{alpine.live_trigger_command()}\n"),
            ],
            dry_run=args.dry_run,
            log_path=serial_log,
        )
    except VMError as exc:
        raise explain_failed_bootstrap(exc, alpine.BOOTSTRAP_FAILED_TOKEN, "Alpine", serial_log) from exc
    vmstate.complete_install(args.vm, "bootstrap-alpine", vm, dry_run=args.dry_run)
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    post_serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/post-install-serial.log")
    run_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        network_phase="install",
        serial_socket=qemu.serial_socket_path(vm),
        serial_log=post_serial_log,
    )
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(run_qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

    report.phase(args, "post-install")
    run_post_install(args.vm, vm, getattr(args, "timeout", 300), dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
    return 0


def cmd_bootstrap_pearos(args: argparse.Namespace) -> int:
    """pearOS NiceC0re: unpackfs install driven over the live ISO's serial console."""
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    problems = pearos.check_profile(args.vm, vm)
    if problems:
        raise VMError("; ".join(problems))

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap pearOS NiceC0re: {args.vm}")

    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-pearos", dry_run=args.dry_run)
    reset_vm_nvram(vm, dry_run=args.dry_run)

    seed_iso = pearos.create_pearos_seed_iso(args.vm, vm, dry_run=args.dry_run)
    kernel_path, initrd_path = pearos.extract_pearos_boot_artifacts(vm, iso_path, dry_run=args.dry_run)
    iso_label = pearos.pearos_iso_label(iso_path)

    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        network_phase="install",
    )
    install_qemu_args += ["-cdrom", str(iso_path)]
    install_qemu_args += pearos.seed_iso_drive_args(seed_iso)
    install_qemu_args += [
        "-kernel", str(kernel_path),
        "-initrd", str(initrd_path),
        "-append", pearos.live_kernel_append(iso_label),
    ]

    ui.print_note("Booting the pearOS live ISO — waiting for the root prompt, then unpacking the squashfs...")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    try:
        qemu.run_and_expect(
            install_qemu_args,
            expected_text=pearos.BOOTSTRAP_COMPLETE_TOKEN,
            timeout_sec=getattr(args, "timeout", 3600),
            auto_inputs=[
                (pearos.PEAROS_SERIAL_LOGIN_PROMPT, "root\n"),
                (pearos.PEAROS_LIVE_PROMPT, f"\n{pearos.live_trigger_command()}\n"),
            ],
            dry_run=args.dry_run,
            log_path=serial_log,
        )
    except VMError as exc:
        raise explain_failed_bootstrap(exc, pearos.BOOTSTRAP_FAILED_TOKEN, "pearOS", serial_log) from exc
    vmstate.complete_install(args.vm, "bootstrap-pearos", vm, dry_run=args.dry_run)
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    report.phase(args, "post-install")
    start_installed_vm_headless(args.vm, vm, disk_exists, dry_run=args.dry_run)
    run_post_install(args.vm, vm, getattr(args, "timeout", 300), dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
    return 0


def cmd_bootstrap_nixos(args: argparse.Namespace) -> int:
    """NixOS: partition, generate the hardware config, install the profile's configuration.nix."""
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    problems = nixos.check_profile(args.vm, vm)
    if problems:
        raise VMError("; ".join(problems))

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap NixOS: {args.vm}")

    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-nixos", dry_run=args.dry_run)
    reset_vm_nvram(vm, dry_run=args.dry_run)

    seed_iso = nixos.create_nixos_seed_iso(args.vm, vm, dry_run=args.dry_run)
    live_boot = nixos.resolve_live_boot(iso_path, dry_run=args.dry_run)
    kernel_path, initrd_path = nixos.extract_nixos_boot_artifacts(vm, iso_path, live_boot, dry_run=args.dry_run)

    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        network_phase="install",
    )
    install_qemu_args += ["-cdrom", str(iso_path)]
    install_qemu_args += nixos.seed_iso_drive_args(seed_iso)
    install_qemu_args += ["-kernel", str(kernel_path), "-initrd", str(initrd_path)]
    if live_boot is not None:
        install_qemu_args += ["-append", nixos.live_kernel_append(live_boot)]
    elif not args.dry_run:
        raise VMError(
            f"Could not read the boot configuration of {ui.pretty_path(iso_path)}: the NixOS live "
            "system boots through an init= store path that only the medium knows"
        )

    ui.print_note("Booting the NixOS installer — waiting for the live shell, then running nixos-install...")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    try:
        qemu.run_and_expect(
            install_qemu_args,
            expected_text=nixos.BOOTSTRAP_COMPLETE_TOKEN,
            timeout_sec=getattr(args, "timeout", 3600),
            auto_inputs=[(nixos.NIXOS_LIVE_PROMPT, f"\n{nixos.live_trigger_command()}\n")],
            dry_run=args.dry_run,
            log_path=serial_log,
        )
    except VMError as exc:
        raise explain_failed_bootstrap(exc, nixos.BOOTSTRAP_FAILED_TOKEN, "NixOS", serial_log) from exc
    vmstate.complete_install(args.vm, "bootstrap-nixos", vm, dry_run=args.dry_run)
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    report.phase(args, "post-install")
    start_installed_vm_headless(args.vm, vm, disk_exists, dry_run=args.dry_run)
    run_post_install(args.vm, vm, getattr(args, "timeout", 300), dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
    return 0


def start_installed_vm_headless(vm_name: str, vm: dict[str, Any], disk_exists: bool, dry_run: bool = False) -> None:
    """Boot the freshly installed disk in the background, serial to post-install-serial.log."""
    pid_path, log_path = prepare_background_vm_slot(vm_name, dry_run=dry_run)
    post_serial_log = runtime.resolve_path(f"artifacts/{vm_name}/logs/post-install-serial.log")
    run_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=dry_run,
        accel=automation_accel(vm),
        headless=True,
        allow_missing_disk=dry_run and not disk_exists,
        network_phase="install",
        serial_socket=qemu.serial_socket_path(vm),
        serial_log=post_serial_log,
    )
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(run_qemu_args, log_path, dry_run=dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))


def run_windows_post_install(vm_name: str, vm: dict[str, Any], timeout_sec: int, dry_run: bool = False) -> None:
    """Post-install over Windows OpenSSH: commands run in cmd.exe, copies are plain scp."""
    ssh_cfg = cloud_init.ssh_access_config(vm)
    if ssh_cfg is None:
        raise VMError(f"VM '{vm_name}' does not define SSH provisioning")
    runtime.require_command("ssh")
    runtime.require_command("scp")
    stdout_log, stderr_log = announce_phase_logs(vm_name, "post-install")
    ssh.wait_for_ssh(vm, timeout_sec, dry_run=dry_run, probe_command="exit 0")
    ui.print_status("ok", f"SSH is ready for VM '{vm_name}'")
    ui.print_note("Running post-install provisioning (cmd.exe semantics)")
    for entry in ssh_cfg.get("copy_from_host", []):
        if not isinstance(entry, dict):
            raise VMError("Invalid copy_from_host entry: expected object")
        ssh.post_install_copy_raw(vm, entry, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    for command in ssh_cfg.get("post_install_run", []):
        ssh.post_install_run_raw(vm, str(command), dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    vmstate.record_verified(vm_name, "post-install", dry_run=dry_run)


def cmd_bootstrap_windows(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if windows.windows_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define windows_config")

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap Windows (autounattend): {args.vm}")

    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    install_iso = windows.ensure_noprompt_iso(iso_path, dry_run=args.dry_run, legacy=windows.is_legacy_windows(windows.windows_config(vm) or {}))
    virtio_iso = windows.ensure_virtio_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-windows", dry_run=args.dry_run)
    reset_vm_nvram(vm, dry_run=args.dry_run)

    seed_iso = windows.create_windows_seed_iso(args.vm, vm, dry_run=args.dry_run)

    # Setup reboots several times (WinPE -> specialize -> OOBE), so no -no-reboot here. The
    # disk gets bootindex 1 and the prompt-free install CD bootindex 2: OVMF falls through to
    # the CD only while the disk has no bootloader, then every reboot lands on Windows.
    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        disk_bootindex=1,
        network_phase="install",
    )
    install_qemu_args += windows.install_media_args(install_iso, virtio_iso, seed_iso)

    ui.print_note("Booting Windows Setup — waiting for the first-logon script to report completion on COM1...")
    ui.print_note(f"Watch the screen with: vmctl attach {args.vm}")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    try:
        qemu.run_and_expect(
            install_qemu_args,
            expected_text=windows.BOOTSTRAP_COMPLETE_TOKEN,
            timeout_sec=getattr(args, "timeout", 3600),
            dry_run=args.dry_run,
            log_path=serial_log,
            exit_grace_sec=windows.SHUTDOWN_GRACE_SEC,
        )
    except VMError as exc:
        # The first-logon script shuts the guest down after a failure too, with its own token:
        # QEMU exits without the success token and we land here with the failure in the output.
        if windows.BOOTSTRAP_FAILED_TOKEN in str(exc):
            failed_line = next((line for line in str(exc).splitlines() if windows.BOOTSTRAP_FAILED_TOKEN in line), "")
            raise VMError(
                f"Windows first-logon setup reported failures: {failed_line.strip()} "
                f"(details in {ui.pretty_path(serial_log)} and C:\\vmctl\\setup.log in the guest)"
            ) from exc
        raise
    vmstate.complete_install(args.vm, "bootstrap-windows", vm, dry_run=args.dry_run)
    if cloud_init.ssh_access_config(vm) is None:
        ui.print_status("ok", f"Installation complete for VM '{args.vm}' (no ssh_provision: skipping post-install)")
        return 0
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    start_installed_vm_headless(args.vm, vm, disk_exists, dry_run=args.dry_run)
    report.phase(args, "post-install")
    run_windows_post_install(args.vm, vm, getattr(args, "timeout", 600), dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
    return 0


def pfsense_source_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    """The pfSense CE ISO: an existing file (local.json may point at it), else the .iso.gz from Netgate's mirror."""
    iso_path = runtime.resolve_path(vm["iso"])
    if iso_path.is_file():
        return iso_path
    if iso.iso_url_candidates(vm, allow_discovery=False):
        return iso.ensure_iso(vm, dry_run=dry_run)
    if dry_run:
        ui.print_status("warn", f"ISO {ui.pretty_path(iso_path)} is missing: dry-run continues with the path", ok=False)
        return iso_path
    raise VMError(iso.missing_iso_message(vm, vm_name))


def cmd_bootstrap_freebsd(args: argparse.Namespace) -> int:
    vm = resolved_vm(args, config.load_config())
    freebsd.check_profile(args.vm, vm)
    runtime.ensure_vm_dirs(args.vm)
    source = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-freebsd", dry_run=args.dry_run)
    media = freebsd.ensure_install_iso(args.vm, vm, source,
        cloud_init._authorized_keys_for_vm(vm, dry_run=args.dry_run), dry_run=args.dry_run)
    command = qemu.common_args(vm, None, dry_run=args.dry_run,
        accel=automation_accel(vm), headless=True, serial_stdio=True,
        no_reboot=True, allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False, disk_bootindex=1, network_phase="install")
    command += freebsd.install_media_args(media)
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    report.phase(args, "install")
    try:
        qemu.run_and_expect(command, expected_text=freebsd.BOOTSTRAP_COMPLETE_TOKEN,
            timeout_sec=args.timeout, dry_run=args.dry_run, log_path=serial_log,
            exit_grace_sec=freebsd.SHUTDOWN_GRACE_SEC)
    except VMError as exc:
        raise explain_failed_bootstrap(exc, freebsd.BOOTSTRAP_FAILED_TOKEN, "FreeBSD", serial_log) from exc
    vmstate.complete_install(args.vm, "bootstrap-freebsd", vm, dry_run=args.dry_run)
    start_installed_vm_headless(args.vm, vm, disk_exists, dry_run=args.dry_run)
    report.phase(args, "post-install")
    run_post_install(args.vm, vm, args.timeout, dry_run=args.dry_run)
    return 0


def cmd_bootstrap_haiku(args: argparse.Namespace) -> int:
    vm = resolved_vm(args, config.load_config())
    haiku.check_profile(args.vm, vm)
    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap Haiku (live command line, driven over QMP): {args.vm}")
    source = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-haiku", dry_run=args.dry_run)
    seed = haiku.ensure_seed_iso(args.vm, vm, cloud_init._authorized_keys_for_vm(vm, dry_run=args.dry_run),
                                 dry_run=args.dry_run)
    command = qemu.common_args(vm, None, dry_run=args.dry_run,
        accel=automation_accel(vm), headless=True, serial_stdio=True,
        no_reboot=True, allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False, disk_bootindex=1, network_phase="install")
    command += haiku.install_media_args(source, seed)
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    report.phase(args, "install")
    pilot = haiku.Pilot(qemu.qmp_socket_path(vm), seed.parent)
    if args.dry_run:
        ui.print_note(f"Would drive the live session over QMP, then type: {haiku.typed_command().strip()}")
    else:
        stale = qemu.qmp_socket_path(vm)
        stale.unlink(missing_ok=True)  # the pilot must not talk to a previous QEMU's socket
        pilot.start()
    try:
        qemu.run_and_expect(command, expected_text=haiku.BOOTSTRAP_COMPLETE_TOKEN,
            timeout_sec=args.timeout, dry_run=args.dry_run, log_path=serial_log,
            exit_grace_sec=haiku.SHUTDOWN_GRACE_SEC)
    except VMError as exc:
        if pilot.error:
            raise VMError(f"Haiku install: {pilot.error}") from exc
        raise explain_failed_bootstrap(exc, haiku.BOOTSTRAP_FAILED_TOKEN, "Haiku", serial_log) from exc
    finally:
        pilot.stop.set()
    vmstate.complete_install(args.vm, "bootstrap-haiku", vm, dry_run=args.dry_run)
    start_installed_vm_headless(args.vm, vm, disk_exists, dry_run=args.dry_run)
    report.phase(args, "post-install")
    run_post_install(args.vm, vm, args.timeout, dry_run=args.dry_run)
    return 0


def cmd_bootstrap_proxmox(args: argparse.Namespace) -> int:
    vm = resolved_vm(args, config.load_config())
    proxmox.check_profile(args.vm, vm)
    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap Proxmox VE (automated installer): {args.vm}")
    source = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-proxmox", dry_run=args.dry_run)
    media = proxmox.ensure_install_iso(args.vm, vm, source,
        cloud_init._authorized_keys_for_vm(vm, dry_run=args.dry_run), dry_run=args.dry_run)
    boot_vm = {**vm, "installer_boot": {"kernel": proxmox.KERNEL_MEMBER, "initrd": proxmox.INITRD_MEMBER}}
    kernel_path, initrd_path = iso.extract_installer_boot_artifacts(boot_vm, media, dry_run=args.dry_run)
    command = qemu.common_args(vm, None, dry_run=args.dry_run,
        accel=automation_accel(vm), headless=True, serial_stdio=True,
        no_reboot=True, allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False, network_phase="install")
    command += proxmox.install_media_args(media)
    command += ["-kernel", str(kernel_path), "-initrd", str(initrd_path), "-append", proxmox.KERNEL_APPEND]
    stdout_log, stderr_log = announce_phase_logs(args.vm, "install-proxmox")
    report.phase(args, "install")
    # reboot-mode = "power-off" + -no-reboot: the installer's own shutdown (pool exported) ends QEMU.
    runtime.run(command, dry_run=args.dry_run, stdout_log=stdout_log, stderr_log=stderr_log,
                timeout_sec=args.timeout)
    vmstate.complete_install(args.vm, "bootstrap-proxmox", vm, dry_run=args.dry_run)
    start_installed_vm_headless(args.vm, vm, disk_exists, dry_run=args.dry_run)
    report.phase(args, "post-install")
    run_post_install(args.vm, vm, args.timeout, dry_run=args.dry_run)
    return 0


def cmd_bootstrap_pfsense(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if pfsense.pfsense_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define pfsense_config")
    pfsense.check_profile(args.vm, vm)
    top = netlab.topology(cfg, args.vm)

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap pfSense (scripted bsdinstall): {args.vm}")
    for line in netlab.describe(top):
        ui.print_note(line)

    source_iso = pfsense_source_iso(args.vm, vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-pfsense", dry_run=args.dry_run)
    config_xml = pfsense.render_config_xml(
        args.vm, vm, top,
        password_hash="dry-run" if args.dry_run else None,
        authorized_keys=cloud_init._authorized_keys_for_vm(vm, dry_run=args.dry_run),
    )
    install_iso = pfsense.ensure_install_iso(args.vm, vm, source_iso, config_xml, dry_run=args.dry_run)

    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        disk_bootindex=1,
        network_phase="install",
    )
    install_qemu_args += pfsense.install_media_args(install_iso)

    ui.print_note("Booting the pfSense installer — waiting for the completion token on the serial console...")
    ui.print_note(f"Watch the screen with: vmctl attach {args.vm}")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    try:
        qemu.run_and_expect(
            install_qemu_args,
            expected_text=pfsense.BOOTSTRAP_COMPLETE_TOKEN,
            timeout_sec=getattr(args, "timeout", 1800),
            dry_run=args.dry_run,
            log_path=serial_log,
            exit_grace_sec=pfsense.SHUTDOWN_GRACE_SEC,
        )
    except VMError as exc:
        if pfsense.BOOTSTRAP_FAILED_TOKEN in str(exc):
            raise VMError(
                f"pfSense bsdinstall reported a failure (the installer log follows the token in {ui.pretty_path(serial_log)})"
            ) from exc
        raise
    vmstate.complete_install(args.vm, "bootstrap-pfsense", vm, dry_run=args.dry_run)
    ui.print_status("ok", f"Installation complete for VM '{args.vm}'")
    router_user = str((pfsense.pfsense_config(vm) or {}).get("username"))
    for host_port, guest_port in sorted(top["router_gui"].items()):
        scheme = "https" if guest_port == 443 else "http"
        ui.print_note(f"Web GUI once started: {scheme}://127.0.0.1:{host_port}/  (user {router_user} or admin)")
    ui.print_note(f"Start it with: vmctl start {args.vm} --headless --background   (or: vmctl lab up)")
    return 0


def reactos_source_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    """The ReactOS BootCD: SourceForge ships it inside a zip, so it is a local file unzipped into isos/."""
    iso_path = runtime.resolve_path(vm["iso"])
    if iso_path.is_file():
        return iso.ensure_iso(vm, dry_run=dry_run)
    if iso.iso_url_candidates(vm, allow_discovery=False):
        return iso.ensure_iso(vm, dry_run=dry_run)
    if dry_run:
        ui.print_status("warn", f"ISO {ui.pretty_path(iso_path)} is missing: dry-run continues with the path", ok=False)
        return iso_path
    raise VMError(iso.missing_iso_message(vm, vm_name))


def cmd_bootstrap_reactos(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if reactos.reactos_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define reactos_config")
    reactos.check_profile(args.vm, vm)

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap ReactOS (unattend.inf): {args.vm}")

    source_iso = reactos_source_iso(args.vm, vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-reactos", dry_run=args.dry_run)
    unattend = reactos.render_unattend(args.vm, vm)
    install_iso = reactos.ensure_install_iso(args.vm, vm, source_iso, unattend, dry_run=args.dry_run)

    # No -no-reboot: Setup reboots after the text stage and after the GUI stage; the disk carries
    # bootindex=1, so once it is bootable it wins over the CD, like the Windows flow.
    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        disk_bootindex=1,
        network_phase="install",
    )
    install_qemu_args += reactos.install_media_args(install_iso)

    ui.print_note("Booting ReactOS Setup — waiting for the completion token on COM1 (text stage, GUI stage, first logon)...")
    ui.print_note(f"Watch the screen with: vmctl attach {args.vm}")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    qemu.run_and_expect(
        install_qemu_args,
        expected_text=reactos.BOOTSTRAP_COMPLETE_TOKEN,
        timeout_sec=getattr(args, "timeout", 1800),
        dry_run=args.dry_run,
        log_path=serial_log,
        exit_grace_sec=reactos.SHUTDOWN_GRACE_SEC,
    )
    vmstate.complete_install(args.vm, "bootstrap-reactos", vm, dry_run=args.dry_run)
    ui.print_status("ok", f"Installation complete for VM '{args.vm}'")
    ui.print_note(f"Start it with: vmctl start {args.vm}   (no SSH: ReactOS ships no server, the desktop autologs in as Administrator)")
    return 0


def windowsxp_source_iso(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    """The retail/OEM medium: no public URL, so it is the profile's path or a local.json override."""
    source = runtime.resolve_path(str(vm["iso"]))
    if not source.is_file() and not dry_run:
        raise VMError(iso.missing_iso_message(vm, vm_name))
    return source


def cmd_bootstrap_windowsxp(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if windowsxp.windowsxp_config(vm) is None:
        raise VMError(f"VM '{args.vm}' defines neither windowsxp_config nor windows2000_config")
    windowsxp.check_profile(args.vm, vm)

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap {windowsxp.product_name(vm)} (WINNT.SIF): {args.vm}")

    source_iso = windowsxp_source_iso(args.vm, vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-windowsxp", dry_run=args.dry_run)
    answer = windowsxp.render_winnt_sif(args.vm, vm)
    install_iso = windowsxp.ensure_install_iso(args.vm, vm, source_iso, answer, dry_run=args.dry_run)

    # No -no-reboot: Setup reboots after the text stage and again before the first logon. The disk
    # carries bootindex=1, so once it holds a boot sector it wins over the CD; with the CD ahead of
    # it the installer starts over from the beginning, forever (verified live).
    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        disk_bootindex=1,
        network_phase="install",
    )
    install_qemu_args += windowsxp.install_media_args(install_iso)

    ui.print_note("Booting Windows XP Setup - waiting for the completion token on COM1 (text stage, GUI stage, first logon)...")
    ui.print_note(f"Watch the screen with: vmctl attach {args.vm}")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    qemu.run_and_expect(
        install_qemu_args,
        expected_text=windowsxp.complete_token(vm),
        timeout_sec=getattr(args, "timeout", 3600),
        dry_run=args.dry_run,
        log_path=serial_log,
        exit_grace_sec=windowsxp.SHUTDOWN_GRACE_SEC,
    )
    vmstate.complete_install(args.vm, "bootstrap-windowsxp", vm, dry_run=args.dry_run)
    ui.print_status("ok", f"Installation complete for VM '{args.vm}'")
    ui.print_note(f"Start it with: vmctl start {args.vm}   (no SSH on this generation; the desktop autologs in)")
    return 0


cmd_bootstrap_windows2000 = cmd_bootstrap_windowsxp


def cmd_bootstrap_windows98(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if windows98.windows98_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define windows98_config")
    windows98.check_profile(args.vm, vm)

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap Windows 98 (MSBATCH.INF): {args.vm}")

    source = runtime.resolve_path(str(vm["iso"]))
    if not source.is_file() and not args.dry_run:
        raise VMError(iso.missing_iso_message(vm, args.vm))
    # Not ensure_vm_disk: Setup neither partitions nor formats, so the host hands it a disk that is
    # already a FAT32 volume with boot code that steps aside until Windows owns it.
    windows98.prepare_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-windows98", dry_run=args.dry_run)
    answer = windows98.render_msbatch(args.vm, vm)
    install_iso = windows98.ensure_install_iso(args.vm, vm, source, answer, dry_run=args.dry_run)

    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        allow_missing_disk=args.dry_run,
        enable_clipboard=False,
        disk_bootindex=1,
        network_phase="install",
    )
    install_qemu_args += windows98.install_media_args(install_iso)

    ui.print_note("Booting Windows 98 Setup - waiting for the completion token on COM1 (DOS stage, GUI stage, first logon)...")
    ui.print_note(f"Watch the screen with: vmctl attach {args.vm}")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    qemu.run_and_expect(
        install_qemu_args,
        expected_text=windows98.BOOTSTRAP_COMPLETE_TOKEN,
        timeout_sec=getattr(args, "timeout", 5400),
        dry_run=args.dry_run,
        log_path=serial_log,
        exit_grace_sec=windows98.SHUTDOWN_GRACE_SEC,
    )
    vmstate.complete_install(args.vm, "bootstrap-windows98", vm, dry_run=args.dry_run)
    ui.print_status("ok", f"Installation complete for VM '{args.vm}'")
    ui.print_note(f"Start it with: vmctl start {args.vm}   (no SSH: Windows 98 ships no server)")
    return 0


def cmd_bootstrap_windowsnt4(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if windowsnt4.windowsnt4_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define windowsnt4_config")
    windowsnt4.check_profile(args.vm, vm)

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap Windows NT 4.0 (UNATTEND.TXT): {args.vm}")

    source = runtime.resolve_path(str(vm["iso"]))
    if not source.is_file() and not args.dry_run:
        raise VMError(iso.missing_iso_message(vm, args.vm))
    windowsnt4.ensure_dos_pieces(vm, dry_run=args.dry_run)
    # Not ensure_vm_disk: WINNT.EXE runs from DOS and copies onto a C: that must already be a
    # FAT16 volume, with boot code that steps aside until the NT loader owns the disk.
    windowsnt4.prepare_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-windowsnt4", dry_run=args.dry_run)
    answer = windowsnt4.render_unattend(args.vm, vm, source)
    install_iso = windowsnt4.ensure_install_iso(args.vm, vm, source, answer, dry_run=args.dry_run)

    # No -no-reboot: WINNT.EXE reboots into text-mode Setup, which reboots into the GUI stage,
    # which reboots into the first logon. The disk carries bootindex=1 and wins as soon as it
    # holds a boot sector; before that our MBR returns to the BIOS and the CD's DOS floppy boots.
    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        allow_missing_disk=args.dry_run,
        enable_clipboard=False,
        disk_bootindex=1,
        network_phase="install",
    )
    install_qemu_args += windowsnt4.install_media_args(install_iso)

    ui.print_note("Booting the DOS floppy - waiting for the completion token on COM1 (WINNT.EXE copy, text stage, GUI stage, first logon)...")
    ui.print_note(f"Watch the screen with: vmctl attach {args.vm}")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    try:
        qemu.run_and_expect(
            install_qemu_args,
            expected_text=windowsnt4.BOOTSTRAP_COMPLETE_TOKEN,
            timeout_sec=getattr(args, "timeout", 5400),
            dry_run=args.dry_run,
            log_path=serial_log,
            # NT 4 cannot power the machine off: after the token the guest shuts down to "safe to
            # turn off" and QEMU is closed by the host once this grace has passed.
            exit_grace_sec=windowsnt4.SHUTDOWN_GRACE_SEC,
        )
    except VMError as exc:
        raise explain_failed_bootstrap(exc, windowsnt4.BOOTSTRAP_FAILED_TOKEN, "Windows NT 4.0", serial_log) from exc
    vmstate.complete_install(args.vm, "bootstrap-windowsnt4", vm, dry_run=args.dry_run)
    ui.print_status("ok", f"Installation complete for VM '{args.vm}'")
    ui.print_note(f"Start it with: vmctl start {args.vm}   (no SSH: Windows NT 4.0 ships no server; the desktop autologs in as Administrator)")
    return 0


def cmd_lab(args: argparse.Namespace) -> int:
    """Network lab orchestration: plan, install (router, Pi-hole, clients in order), up/down, status, attach."""
    cfg = config.load_config()
    action = str(args.action)
    if action == "attach":
        if not args.vm:
            raise VMError("lab attach needs the profile to connect: vmctl lab attach <vm> [--apply]")
        router = netlab.resolve_router(cfg, getattr(args, "router", None))
        snippet = netlab.attach_snippet(cfg, router, args.vm)
        top = netlab.topology(cfg, router)
        ui.print_header(f"Attach {args.vm} to {top['lan']['name']}")
        ui.print_note(f"Gateway {top['router_ip']}, DNS {top['dns_ip']}: the guest takes DHCP from Pi-hole or a free static address outside "
                      f"{top['dhcp']['start']}-{top['dhcp']['end']}. Nothing is changed inside the guest.")
        if not getattr(args, "apply", False):
            ui.print_note("Add this to vms/profiles/local.json (or rerun with --apply):")
            print(json.dumps(snippet, indent=2))
            return 0
        path = netlab.write_local_override(snippet, dry_run=args.dry_run)
        ui.print_status("ok", f"{args.vm} now lists a {top['lan']['name']} NIC in {ui.pretty_path(path)}; its slirp NIC (and SSH forward) is gone")
        return 0

    router = netlab.resolve_router(cfg, args.vm)
    top = netlab.topology(cfg, router)
    names = netlab.lab_vm_names(cfg, router)
    if action == "plan":
        ui.print_header(f"Network lab behind {router}")
        for line in netlab.describe(top):
            ui.print_note(line)
        ui.print_note("libvirt network for export-libvirt:")
        print(netlab.segment_network_xml(top), end="")
        ui.print_note(f"Install order: {' -> '.join(names)}")
        return 0
    if action == "status":
        ui.print_header(f"Network lab behind {router}")
        for name in names:
            vm = config.get_vm(cfg, name)
            disk_state, _, _ = disk_status(vm)
            runtime_str, _ = vm_runtime_status(name, vm)
            role = str((netlab.lab_config(vm) or {}).get("role"))
            address = top["router_ip"] if role == "pfsense" else netlab.member_of(top, name)["ip"]
            ui.print_kv(name, f"{role:<8} {address:<16} disk: {disk_state:<10} {runtime_str}")
        return 0
    if action == "clean":
        uri = str(getattr(args, "connect", None) or "qemu:///system")
        if lab_in_libvirt(uri, names, args.dry_run):
            raise VMError(f"The lab is defined in libvirt ({uri}): run vmctl lab unexport first, then vmctl lab clean")
        ui.print_header(f"Clean the network lab: {', '.join(names)} (disks and artifacts; ISOs are kept)")
        for name in reversed(names):
            vm = config.get_vm(cfg, name)
            if running_qemu_pid(name, vm) is not None:
                cmd_stop(argparse.Namespace(vm=name, dry_run=args.dry_run))
            clean_vm(name, vm, dry_run=args.dry_run)
        ui.print_status("ok", "Network lab removed. Reinstall with: vmctl lab install")
        return 0
    if action == "install":
        existing = [name for name in names if runtime.resolve_path(config.get_vm(cfg, name)["disk"]["path"]).exists()]
        if existing:
            raise VMError(
                f"Disk already present for: {', '.join(existing)}. The lab never reinstalls implicitly: "
                f"vmctl clean <vm> (or move the disk away) first, then rerun vmctl lab install"
            )
        ui.print_header(f"Install the network lab: {' -> '.join(names)}")
        for name in names:
            vm = config.get_vm(cfg, name)
            role = str((netlab.lab_config(vm) or {}).get("role"))
            if role == "pfsense":
                cmd_bootstrap_pfsense(argparse.Namespace(vm=name, timeout=args.timeout, dry_run=args.dry_run))
                continue
            if cloud_init.autoinstall_config(vm) is None:
                raise VMError(f"{name}: lab members install with the Ubuntu autoinstall flow (autoinstall section missing)")
            try:
                cmd_bootstrap_unattended(argparse.Namespace(vm=name, video=None, timeout=args.timeout, dry_run=args.dry_run))
            finally:
                cmd_stop(argparse.Namespace(vm=name, dry_run=args.dry_run))
        if getattr(args, "export", False):
            ui.print_status("ok", "Network lab installed; handing it to libvirt (--export)")
            return lab_export(cfg, top, names, str(getattr(args, "connect", None) or "qemu:///system"), args)
        ui.print_status("ok", "Network lab installed. Start it with: vmctl lab up")
        return 0
    if action == "up":
        for name in names:
            cmd_start(argparse.Namespace(vm=name, headless=True, background=True, video=None, cloud_init=False, spice_port=None, dry_run=args.dry_run))
        for line in netlab.describe(top):
            ui.print_note(line)
        return 0
    if action == "down":
        for name in reversed(names):
            cmd_stop(argparse.Namespace(vm=name, dry_run=args.dry_run))
        return 0
    uri = str(getattr(args, "connect", None) or "qemu:///system")
    if action == "check":
        mode = "libvirt" if getattr(args, "libvirt", False) or lab_in_libvirt(uri, names, args.dry_run) else "qemu"
        return lab_check(top, mode, int(getattr(args, "wait", 180)), args.dry_run)
    if action == "export":
        return lab_export(cfg, top, names, uri, args)
    if action == "unexport":
        return lab_unexport(names, uri, args)
    if action == "libvirt-test":
        code = lab_export(cfg, top, names, uri, args)
        if getattr(args, "keep", False):
            ui.print_note("--keep: the lab stays defined and running in libvirt (vmctl lab unexport brings it back)")
            return code
        return max(code, lab_unexport(names, uri, args))
    raise VMError(f"Unknown lab action: {action}")


def group_states(cfg: dict[str, Any], names: list[str]) -> dict[str, dict[str, Any]]:
    """What the lab map and ``group status`` show live: running or not, and the disk ladder."""
    states: dict[str, dict[str, Any]] = {}
    for name in names:
        vm = config.get_vm(cfg, name)
        states[name] = {"running": running_qemu_pid(name, vm) is not None,
                        "install": str(vmstate.summary(name, vm)["label"]),
                        "flow": local_test_mode(vm)[0]}
    return states


def cmd_group(args: argparse.Namespace) -> int:
    """``vmctl group list|status|up|down|map``: a declared group handled as one stack."""
    cfg = config.load_config()
    action = args.action
    if action == "list":
        groups = labs.lab_groups(cfg) if args.labs else sorted(
            {group for _, vm in config.sorted_vm_items(cfg) for group in config.declared_groups(vm)})
        lab_names = set(labs.lab_groups(cfg))
        entries: list[dict[str, Any]] = []
        for group in groups:
            members = labs.group_members(cfg, group)
            entry: dict[str, Any] = {"group": group, "members": members, "lab": group in lab_names,
                                     "start_order": labs.start_order(cfg, members)}
            if entry["lab"]:
                # Addresses for the TUI's lab panel; live state stays with the dashboard's own rows.
                entry["addresses"] = {member["name"]: [nic["address"] for nic in member["nics"] if nic["type"] == "segment"]
                                      for member in labs.model(cfg, group)["members"]}
            entries.append(entry)
        if args.json:
            print(json.dumps(entries, indent=2))
        else:
            for entry in entries:
                kind = "lab  " if entry["lab"] else "group"
                print(f"{entry['group']:<18} {kind}  {', '.join(entry['members'])}")
        return 0
    if not args.group:
        raise VMError(f"vmctl group {action} needs a group name (vmctl group list shows them)")
    names = labs.group_members(cfg, args.group)
    if not names:
        raise VMError(f"No profile declares the group '{args.group}' (vmctl group list shows them)")
    lab = labs.model(cfg, args.group, group_states(cfg, names))
    order = lab["start_order"]
    if action == "status":
        if args.json:
            print(json.dumps(lab, indent=2))
            return 0
        ui.print_header(f"Group {args.group}: {len(order)} VMs, start order {' -> '.join(order)}")
        for member in lab["members"]:
            addresses = ", ".join(nic.get("address") or nic["type"] for nic in member["nics"])
            state = "running" if member["running"] else "stopped"
            ui.print_note(f"{member['name']:<22} {state:<8} {member['install']:<11} {addresses}")
        return 0
    if action == "up":
        for member in lab["members"]:
            if member["running"]:
                ui.print_status("ok", f"{member['name']} is already running")
            elif member["install"] in ("no disk", "empty"):
                ui.print_status("warn", f"{member['name']} has no installed disk: skipped (install it first)", ok=False)
            else:
                cmd_start(argparse.Namespace(vm=member["name"], headless=True, background=True, video=None,
                                             cloud_init=False, spice_port=None, dry_run=args.dry_run))
        ui.print_note(f"Watch a desktop with: vmctl attach <vm>  ·  map: vmctl group map {args.group} --open")
        return 0
    if action == "down":
        for member in reversed(lab["members"]):
            if member["running"]:
                cmd_stop(argparse.Namespace(vm=member["name"], dry_run=args.dry_run))
        return 0
    if action == "install":
        return group_install(cfg, args, lab)
    if action == "cluster":
        # The cross-VM step of install alone, on a running stack (idempotent).
        pvecluster.form(cfg, order, args.timeout, dry_run=args.dry_run)
        return 0
    if action == "clean":
        present = [member["name"] for member in lab["members"] if member["install"] != "no disk"]
        if not present:
            ui.print_status("ok", f"Nothing to clean in {args.group}: no member has a disk")
            return 0
        confirm_or_yes(args, f"Stop and delete the disks and artifacts of {', '.join(present)} (checkpoints and ISOs are kept)?")
        for member in reversed(lab["members"]):
            vm = config.get_vm(cfg, member["name"])
            if member["running"]:
                cmd_stop(argparse.Namespace(vm=member["name"], dry_run=args.dry_run))
            clean_vm(member["name"], vm, dry_run=args.dry_run)
        ui.print_status("ok", f"{args.group} cleaned; reinstall it with: vmctl group install {args.group}")
        return 0
    if action == "map":
        dest = Path(args.output).expanduser() if args.output else None
        if args.dry_run:
            ui.print_note(f"Would write {ui.pretty_path(dest or labs.map_path(args.group))}")
            return 0
        path = labs.write_map(lab, dest)
        ui.print_status("ok", f"Network map of {args.group}: {ui.pretty_path(path)}")
        if args.open:
            import webbrowser
            webbrowser.open(path.resolve().as_uri())
        return 0
    raise VMError(f"Unknown group action: {action}")


INSTALLED_LABELS = ("installed", "verified")


def group_install(cfg: dict[str, Any], args: argparse.Namespace, lab: dict[str, Any]) -> int:
    """Install what the group lacks, in start order, with each member's own unattended flow; then
    bring the stack up in the runtime phase. Cumulative: installed members are kept, so a rerun after
    a failure continues where it stopped. A disk left empty or by an unfinished install is deleted
    first (after asking): an install cannot resume on it, and an old disk would boot ahead of the medium."""
    group = lab["group"]
    missing = [m["name"] for m in lab["members"] if m["install"] not in INSTALLED_LABELS]
    if not missing:
        ui.print_status("ok", f"Every member of {group} is installed")
    else:
        redo = [m["name"] for m in lab["members"] if m["name"] in missing and m["install"] != "no disk"]
        if redo:
            confirm_or_yes(args, f"Reinstall from scratch (their disks are deleted): {', '.join(redo)}?")
        ui.print_header(f"Install {group}: {' -> '.join(missing)}"
                        + (f" (kept: {', '.join(n for n in lab['start_order'] if n not in missing)})" if len(missing) < len(lab["members"]) else ""))
        for member in lab["members"]:
            name = member["name"]
            if name not in missing:
                continue
            vm = config.get_vm(cfg, name)
            if member["running"]:
                cmd_stop(argparse.Namespace(vm=name, dry_run=args.dry_run))
            if name in redo:
                clean_vm(name, vm, dry_run=args.dry_run)
            outcome, detail = run_local_test_vm(name, vm, argparse.Namespace(timeout=args.timeout, dry_run=args.dry_run))
            if outcome != "passed":
                raise VMError(f"{name} was not installed ({detail}); fix it and rerun vmctl group install {group}")
            ui.print_status("ok", f"{name} installed ({detail})")
    # The installs ran with the install-phase NICs: restart everything in the runtime phase.
    for member in reversed(labs.model(cfg, group, group_states(cfg, lab["start_order"]))["members"]):
        if member["running"]:
            cmd_stop(argparse.Namespace(vm=member["name"], dry_run=args.dry_run))
    code = cmd_group(argparse.Namespace(**{**vars(args), "action": "up"}))
    # Cross-VM steps need the runtime NICs, so they come last: a Proxmox cluster over the segment.
    pvecluster.form(cfg, lab["start_order"], args.timeout, dry_run=args.dry_run)
    return code


def lab_in_libvirt(uri: str, names: list[str], dry_run: bool) -> bool:
    if dry_run or shutil.which("virsh") is None:
        return False
    try:
        defined = libvirt.virsh_output(uri, "list", "--all", "--name").splitlines()
    except subprocess.CalledProcessError:
        return False
    return names[0] in defined


def lab_check(top: dict[str, Any], mode: str, wait_sec: int, dry_run: bool) -> int:
    """Probe the GUIs and SSH ports of the lab from the host; 0 when everything answers."""
    ui.print_header(f"Network lab check ({mode}: {'through the router forwards on 127.0.0.1' if mode == 'qemu' else 'host on the LAN at ' + top['lan']['host_ip']})")
    targets = netlab.check_targets(top, mode)
    if dry_run:
        for target in targets:
            ui.print_note(f"Would probe {target['label']}: " + (str(target["url"]) if target["kind"] == "http" else f"{target['host']}:{target['port']}"))
        return 0
    failed = 0
    for target, ok, detail in netlab.wait_targets(targets, wait_sec):
        where = str(target["url"]) if target["kind"] == "http" else f"{target['host']}:{target['port']}"
        ui.print_status("ok" if ok else "fail", f"{target['label']}: {where} ({detail})", ok=ok)
        failed += 0 if ok else 1
    return 1 if failed else 0


def lab_export(cfg: dict[str, Any], top: dict[str, Any], names: list[str], uri: str, args: argparse.Namespace) -> int:
    """QEMU lab down, every VM defined in libvirt (lab-lan network included), started with virsh, then checked."""
    ui.print_header(f"Network lab -> libvirt ({uri})")
    for name in reversed(names):
        cmd_stop(argparse.Namespace(vm=name, dry_run=args.dry_run))
    for name in names:
        cmd_export_libvirt(argparse.Namespace(vm=name, name=None, connect=uri, no_define=False,
                                              replace=bool(getattr(args, "replace", False)), autostart=False, dry_run=args.dry_run))
    running = [] if args.dry_run else libvirt.virsh_output(uri, "list", "--name").splitlines()
    for name in names:
        if name in running:
            ui.print_note(f"{name} is already running in libvirt")
            continue
        runtime.run(["virsh", "--connect", uri, "start", name], dry_run=args.dry_run)
    for line in netlab.describe(top):
        ui.print_note(line)
    ui.print_note(f"On libvirt the host is on the LAN: GUI http://{top['router_ip']}/ ; the 127.0.0.1 forwards above apply to plain QEMU only")
    return lab_check(top, "libvirt", int(getattr(args, "wait", 180)), args.dry_run)


def lab_unexport(names: list[str], uri: str, args: argparse.Namespace) -> int:
    """virsh shutdown (waited) and unexport-libvirt for every lab VM, members first, router last."""
    ui.print_header(f"Network lab <- libvirt ({uri})")
    if not args.dry_run:
        running = libvirt.virsh_output(uri, "list", "--name").splitlines()
        for name in reversed(names):
            if name in running:
                runtime.run(["virsh", "--connect", uri, "shutdown", name])
        deadline = time.monotonic() + int(getattr(args, "wait", 180))
        pending = [name for name in reversed(names) if name in running]
        while pending and time.monotonic() < deadline:
            time.sleep(3)
            # `virsh list --name` is locale-independent; `domstate` prints translated states ("terminato").
            still_running = libvirt.virsh_output(uri, "list", "--name").splitlines()
            pending = [name for name in pending if name in still_running]
        if pending:
            raise VMError(f"Still running in libvirt after the grace period: {', '.join(pending)} (virsh destroy them by hand, then rerun vmctl lab unexport)")
    for name in reversed(names):
        cmd_unexport_libvirt(argparse.Namespace(vm=name, name=None, connect=uri, dry_run=args.dry_run))
    ui.print_status("ok", "Network lab is back on plain QEMU: vmctl lab up")
    return 0


def cmd_bootstrap_preseed(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    cfg_obj = preseed.preseed_config(vm)
    if cfg_obj is None:
        raise VMError(f"VM '{args.vm}' does not define preseed_config")

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap Debian (preseed): {args.vm}")

    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-preseed", dry_run=args.dry_run)
    reset_vm_nvram(vm, dry_run=args.dry_run)

    kernel_path, initrd_path = preseed.extract_preseed_boot_artifacts(
        args.vm, vm, iso_path, dry_run=args.dry_run,
    )

    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        network_phase="install",
    )
    install_qemu_args += ["-cdrom", str(iso_path)]

    locale = cfg_obj.get("locale", "en_US.UTF-8")
    keymap = cfg_obj.get("keyboard_layout", "us")
    language = cfg_obj.get("language", "en")
    country = cfg_obj.get("country", "US")
    append_str = preseed.PRESEED_KERNEL_APPEND.format(
        locale=locale, language=language, country=country, keymap=keymap,
    )
    
    install_qemu_args += [
        "-kernel", str(kernel_path),
        "-initrd", str(initrd_path),
        "-append", append_str,
    ]

    ui.print_note("Booting Debian installer — waiting for completion token...")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    qemu.run_and_expect(
        install_qemu_args,
        expected_text=preseed.BOOTSTRAP_COMPLETE_TOKEN,
        timeout_sec=getattr(args, "timeout", 1800),
        dry_run=args.dry_run,
        log_path=serial_log,
    )
    vmstate.complete_install(args.vm, "bootstrap-preseed", vm, dry_run=args.dry_run)
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    post_serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/post-install-serial.log")
    run_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        network_phase="install",
        serial_socket=qemu.serial_socket_path(vm),
        serial_log=post_serial_log,
    )
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(run_qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

    report.phase(args, "post-install")
    run_post_install(args.vm, vm, getattr(args, "timeout", 300), dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
    return 0


def cmd_bootstrap_kickstart(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if kickstart.kickstart_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define kickstart_config")

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap AlmaLinux/RHEL (kickstart): {args.vm}")

    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-kickstart", dry_run=args.dry_run)
    reset_vm_nvram(vm, dry_run=args.dry_run)

    ostree_ref = kickstart.resolve_ostree_ref(vm, iso_path, dry_run=args.dry_run)
    if ostree_ref:
        ui.print_status("ok", f"ostree ref: {ostree_ref}")
    seed_iso = kickstart.create_kickstart_iso(args.vm, vm, dry_run=args.dry_run, ostree_ref=ostree_ref)
    kernel_path, initrd_path = kickstart.extract_kickstart_boot_artifacts(vm, iso_path, dry_run=args.dry_run)
    stage2 = kickstart.resolve_stage2(iso_path, dry_run=args.dry_run)
    if stage2:
        ui.print_status("ok", f"installer runtime from the medium: {stage2}")

    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        network_phase="install",
    )
    install_qemu_args += ["-cdrom", str(iso_path)]
    install_qemu_args += kickstart.kickstart_iso_drive_args(seed_iso)
    install_qemu_args += [
        "-kernel", str(kernel_path),
        "-initrd", str(initrd_path),
        "-append", kickstart.kernel_append(vm, stage2=stage2),
    ]

    ui.print_note("Booting Kickstart installer — waiting for completion token...")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    qemu.run_and_expect(
        install_qemu_args,
        expected_text=kickstart.BOOTSTRAP_COMPLETE_TOKEN,
        timeout_sec=getattr(args, "timeout", 1800),
        dry_run=args.dry_run,
        log_path=serial_log,
    )
    vmstate.complete_install(args.vm, "bootstrap-kickstart", vm, dry_run=args.dry_run)
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    post_serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/post-install-serial.log")
    run_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        network_phase="install",
        serial_socket=qemu.serial_socket_path(vm),
        serial_log=post_serial_log,
    )
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(run_qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

    report.phase(args, "post-install")
    run_post_install(args.vm, vm, getattr(args, "timeout", 300), dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
    return 0


def cmd_bootstrap_autoyast(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if autoyast.autoyast_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define autoyast_config")

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap openSUSE (AutoYaST): {args.vm}")

    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    vmstate.begin_install(args.vm, "bootstrap-autoyast", dry_run=args.dry_run)
    reset_vm_nvram(vm, dry_run=args.dry_run)

    seed_iso = autoyast.create_autoyast_iso(args.vm, vm, dry_run=args.dry_run)
    kernel_path, initrd_path = autoyast.extract_autoyast_boot_artifacts(vm, iso_path, dry_run=args.dry_run)

    install_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        network_phase="install",
    )
    install_qemu_args += autoyast.install_media_args(iso_path, seed_iso)
    install_qemu_args += [
        "-kernel", str(kernel_path),
        "-initrd", str(initrd_path),
        "-append", autoyast.kernel_append(vm),
    ]

    ui.print_note("Booting AutoYaST installer — waiting for completion token...")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    qemu.run_and_expect(
        install_qemu_args,
        expected_text=autoyast.BOOTSTRAP_COMPLETE_TOKEN,
        timeout_sec=getattr(args, "timeout", 1800),
        dry_run=args.dry_run,
        log_path=serial_log,
    )
    vmstate.complete_install(args.vm, "bootstrap-autoyast", vm, dry_run=args.dry_run)
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    start_installed_vm_headless(args.vm, vm, disk_exists, dry_run=args.dry_run)
    report.phase(args, "post-install")
    run_post_install(args.vm, vm, getattr(args, "timeout", 300), dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
    return 0


def cmd_install_archinstall(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    if archinstall.archinstall_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define archinstall_config")
    runtime.ensure_vm_dirs(args.vm)
    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    ensure_vm_disk(vm, dry_run=args.dry_run)
    reset_vm_nvram(vm, dry_run=args.dry_run)
    config_iso = archinstall.create_config_iso(args.vm, vm, dry_run=args.dry_run)
    qemu_args = qemu.common_args(
        vm,
        qemu.installer_video_variant(vm, args.video),
        dry_run=args.dry_run,
        enable_clipboard=False,
        spice_port=getattr(args, "spice_port", None),
    )
    qemu_args += ["-cdrom", str(iso_path)]
    qemu_args += archinstall.config_iso_drive_args(config_iso)
    ui.print_note("In the live environment run:")
    ui.print_note("  mkdir -p /tmp/archconf && mount /dev/vdb /tmp/archconf && bash /tmp/archconf/run.sh")
    stdout_log, stderr_log = announce_phase_logs(args.vm, "install-archinstall")
    vmstate.begin_install(args.vm, "install-archinstall", interactive=True, dry_run=args.dry_run)
    runtime.run(qemu_args, dry_run=args.dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    return 0


def cmd_install_unattended(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if cloud_init.autoinstall_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define autoinstall")
    runtime.ensure_vm_dirs(args.vm)
    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    flow = getattr(args, "_flow", "install-unattended")
    vmstate.begin_install(args.vm, flow, dry_run=args.dry_run)
    headless = getattr(args, "headless", False)
    seed_path = cloud_init.create_autoinstall_seed(args.vm, vm, dry_run=args.dry_run)
    append_args = "autoinstall ds=nocloud"
    if headless:
        append_args += " console=ttyS0,115200n8"
    kernel_path, initrd_path = iso.extract_installer_boot_artifacts(vm, iso_path, dry_run=args.dry_run)
    qemu_args = qemu.common_args(
        vm,
        None if headless else qemu.installer_video_variant(vm, args.video),
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=headless,
        serial_stdio=headless,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        spice_port=getattr(args, "spice_port", None),
        network_phase="install",
    )
    qemu_args += ["-cdrom", str(iso_path)]
    qemu_args += cloud_init.cloud_init_drive_args(seed_path)
    qemu_args += ["-kernel", str(kernel_path), "-initrd", str(initrd_path), "-append", append_args]
    stdout_log, stderr_log = announce_phase_logs(args.vm, "install-unattended")
    # Bounded only when a bootstrap drives it: `vmctl install-unattended <vm>` by hand stays unbounded,
    # because someone is watching the installer and may take as long as they like.
    runtime.run(qemu_args, dry_run=args.dry_run, stdout_log=stdout_log, stderr_log=stderr_log,
                timeout_sec=getattr(args, "timeout", None))
    vmstate.complete_install(args.vm, flow, vm, dry_run=args.dry_run)
    return 0


def cmd_install_omarchy(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if omarchy.omarchy_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define omarchy_config")
    runtime.ensure_vm_dirs(args.vm)
    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    flow = getattr(args, "_flow", "install-omarchy")
    vmstate.begin_install(args.vm, flow, dry_run=args.dry_run)
    seed_path = omarchy.create_cidata_iso(args.vm, vm, dry_run=args.dry_run)
    headless = getattr(args, "headless", False)
    qemu_args = qemu.common_args(
        vm,
        None if headless else qemu.installer_video_variant(vm, args.video),
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=headless,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        spice_port=getattr(args, "spice_port", None),
        network_phase="install",
    )
    qemu_args += ["-cdrom", str(iso_path)]
    qemu_args += omarchy.cidata_drive_args(seed_path)
    stdout_log, stderr_log = announce_phase_logs(args.vm, "install-omarchy")
    # Bounded only when a bootstrap drives it: `vmctl install-omarchy <vm>` by hand stays unbounded,
    # because someone is watching the installer and may take as long as they like.
    runtime.run(qemu_args, dry_run=args.dry_run, stdout_log=stdout_log, stderr_log=stderr_log,
                timeout_sec=getattr(args, "timeout", None))
    vmstate.complete_install(args.vm, flow, vm, dry_run=args.dry_run)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.ensure_vm_dirs(args.vm)

    if not getattr(args, "background", False):
        running, bg_pid, _ = is_bootstrap_vm_running(args.vm)
        if not running:
            port = vm_ssh_host_port(vm)
            if port is not None:
                bg_pid, _ = find_qemu_process_by_hostfwd_port(port, vm_owner_paths(args.vm, vm))
                running = bg_pid is not None
                if not running:
                    warn_foreign_hostfwd(port, args.vm)
        if running and bg_pid is not None:
            ui.print_status("warn", f"VM '{args.vm}' is already running headless (pid {bg_pid})", ok=False)
            ui.print_note(f"  vmctl shell {args.vm}              — open an SSH session")
            ui.print_note(f"  vmctl stop {args.vm} && vmctl start {args.vm}  — restart with display")
            return 1

    spice_port = getattr(args, "spice_port", None)
    cloud_init_args: list[str] = []
    if args.cloud_init:
        cloud_init_args = cloud_init.cloud_init_drive_args(
            cloud_init.create_cloud_init_seed(args.vm, vm, dry_run=args.dry_run)
        )
    qemu_args = qemu.common_args(vm, args.video, dry_run=args.dry_run, headless=args.headless, spice_port=spice_port)
    qemu_args += cloud_init_args
    if args.background:
        if not args.headless and spice_port is None:
            raise VMError("--background currently requires --headless or --spice-port")
        if args.headless:
            qemu_args = qemu.common_args(
                vm,
                args.video,
                dry_run=args.dry_run,
                headless=True,
                spice_port=spice_port,
                serial_socket=qemu.serial_socket_path(vm),
                serial_log=serial_log_path(args.vm),
            )
            qemu_args += cloud_init_args
            ui.print_kv("serial", f"{ui.pretty_path(serial_log_path(args.vm))}  (interactive: vmctl console {args.vm})")
        pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
        stderr_log = companion_stderr_log_path(log_path)
        pid = runtime.run_background(qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
        if pid is not None:
            pid_path.write_text(f"{pid}\n", encoding="utf-8")
            ui.print_kv("pid", str(pid))
        ui.print_status("ok", f"Started background VM for '{args.vm}'")
        return 0
    runtime.run(qemu_args, dry_run=args.dry_run)
    return 0


def run_post_install(vm_name: str, vm: dict[str, Any], timeout_sec: int, dry_run: bool = False) -> None:
    ssh_cfg = cloud_init.ssh_access_config(vm)
    if ssh_cfg is None:
        raise VMError(f"VM '{vm_name}' does not define SSH provisioning")
    runtime.require_command("ssh")
    runtime.require_command("scp")
    stdout_log, stderr_log = announce_phase_logs(vm_name, "post-install")
    ssh.wait_for_ssh(vm, timeout_sec, dry_run=dry_run)
    ui.print_status("ok", f"SSH is ready for VM '{vm_name}'")
    ssh.ensure_passwordless_sudo(
        vm,
        dry_run=dry_run,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
    )
    ssh.wait_for_guest_post_install_ready(vm, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    ssh.provision_shared_dir(vm, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    netlab.provision_guest(vm_name, vm, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    ui.print_note("Running post-install provisioning")

    for entry in ssh_cfg.get("copy_from_host", []):
        if not isinstance(entry, dict):
            raise VMError("Invalid copy_from_host entry: expected object")
        ssh.post_install_copy(vm, entry, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)

    for command in ssh_cfg.get("post_install_run", []):
        ssh.post_install_run(vm, str(command), dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)

    run_verify_after_reboot(vm_name, vm, ssh_cfg, timeout_sec, dry_run=dry_run,
                            stdout_log=stdout_log, stderr_log=stderr_log)
    vmstate.record_verified(vm_name, "post-install", dry_run=dry_run)


def run_verify_after_reboot(
    vm_name: str,
    vm: dict[str, Any],
    ssh_cfg: dict[str, Any],
    timeout_sec: int,
    dry_run: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> None:
    """Reboot the guest, wait for SSH again, then run ``verify_after_reboot``.

    A profile that installs its desktop *during* the post-install cannot be checked in the
    same session: on the Arch and CachyOS recipes greetd is enabled and restarted while the
    install is still running, the initial niri session does not survive it and tty1 falls
    back to a text login (seen live, screenshot in the report), while the very same disk
    brings up niri and an active session on the next boot. So the assertions that describe
    the finished system belong after a reboot, which is also how the user will meet the VM.
    """
    commands = [str(command) for command in ssh_cfg.get("verify_after_reboot", [])]
    if not commands:
        return
    ui.print_note(f"Rebooting VM '{vm_name}' before the final checks")
    ssh.reboot_guest(vm, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    if not dry_run:
        # Let the guest go down before probing, or the first SSH attempt reconnects to the
        # session that is on its way out.
        time.sleep(REBOOT_SETTLE_SEC)
    ssh.wait_for_ssh(vm, timeout_sec, dry_run=dry_run)
    ui.print_status("ok", f"VM '{vm_name}' is back after the reboot")
    for command in commands:
        ssh.post_install_run(vm, command, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)


def cmd_agent(args: argparse.Namespace) -> int:
    """Ask the guest itself: alive, what it is, which addresses it holds, or shut it down."""
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    if not guest_agent.enabled(vm):
        raise VMError(f"VM '{args.vm}' does not declare guest_agent: true")
    if args.dry_run:
        ui.print_note(f"Would talk to the guest agent on {guest_agent.socket_path(vm)}")
        return 0
    action = args.action or "info"
    if action == "ping":
        guest_agent.command(vm, "guest-ping")
        ui.print_status("ok", f"Guest agent answers for '{args.vm}'")
    elif action == "info":
        ui.print_header(f"Guest agent: {args.vm}")
        guest_agent.print_report(args.vm, vm)
    elif action == "ip":
        for interface, address in guest_agent.addresses(vm):
            ui.print_kv(interface, address)
    elif action == "shutdown":
        guest_agent.shutdown(vm)
        ui.print_status("ok", f"Asked '{args.vm}' to power off through the guest agent")
    else:
        raise VMError(f"Unknown agent action: {action}")
    return 0


def cmd_post_install(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    if windows.windows_config(vm) is not None:
        run_windows_post_install(args.vm, vm, args.timeout, dry_run=args.dry_run)
    else:
        run_post_install(args.vm, vm, args.timeout, dry_run=args.dry_run)
    ui.print_status("ok", f"Post-install completed for VM '{args.vm}'")
    return 0


def cmd_bootstrap_unattended(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if cloud_init.autoinstall_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define autoinstall")
    if cloud_init.ssh_access_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define SSH access for post-install provisioning")

    runtime.ensure_vm_dirs(args.vm)

    ui.print_header(f"Bootstrap VM autoinstall: {args.vm}")
    cmd_install_unattended(
        argparse.Namespace(
            vm=args.vm,
            video=args.video,
            headless=True,
            spice_port=getattr(args, "spice_port", None),
            dry_run=args.dry_run,
            _vm_override=vm,
            _flow="bootstrap-unattended",
            timeout=args.timeout,
        )
    )

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        serial_stdio=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        network_phase="install",
    )
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

    report.phase(args, "post-install")
    run_post_install(args.vm, vm, args.timeout, dry_run=args.dry_run)

    ui.print_status("ok", f"Post-install completed for VM '{args.vm}'")
    return 0


def cmd_bootstrap_omarchy(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    if omarchy.omarchy_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define omarchy_config")
    if cloud_init.ssh_access_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define SSH access for post-install provisioning")

    runtime.ensure_vm_dirs(args.vm)
    ui.print_header(f"Bootstrap Omarchy (cidata): {args.vm}")
    reset_vm_nvram(vm, dry_run=args.dry_run)
    cmd_install_omarchy(
        argparse.Namespace(
            vm=args.vm,
            video=None,
            headless=True,
            spice_port=getattr(args, "spice_port", None),
            dry_run=args.dry_run,
            _vm_override=vm,
            _flow="bootstrap-omarchy",
            timeout=args.timeout,
        )
    )

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    post_serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/post-install-serial.log")
    qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        network_phase="install",
        serial_socket=qemu.serial_socket_path(vm),
        serial_log=post_serial_log,
    )
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

    report.phase(args, "post-install")
    run_post_install(args.vm, vm, args.timeout, dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
    return 0


def poweroff_grace_sec(vm: dict[str, Any]) -> int:
    """How long `vmctl stop` lets the guest power itself off after the ACPI request.

    Profiles may raise it with ``acpi_poweroff_grace_sec``: Windows commits pending feature
    operations (e.g. the OpenSSH capability) during its first shutdown and needs minutes, and a
    SIGTERM in the middle of that would corrupt the guest.
    """
    value = vm.get("acpi_poweroff_grace_sec")
    return ACPI_POWEROFF_GRACE_SEC if value is None else max(1, int(value))


def ssh_poweroff_command(vm: dict[str, Any]) -> list[str] | None:
    """Power-off over the VM's SSH access (`systemctl poweroff`, `shutdown /s` on Windows), or None without SSH."""
    ssh_cfg = cloud_init.ssh_access_config(vm)
    if ssh_cfg is None or not ssh_cfg.get("ssh_host_port"):
        return None
    try:
        base = ssh.ssh_base_cmd(vm) + ["-o", "ConnectTimeout=5"]
    except VMError:
        return None
    if windows.windows_config(vm) is not None:
        return base + ["shutdown /s /t 0 /f"]
    if freebsd.freebsd_config(vm) is not None:
        return base + ["sudo", "shutdown", "-p", "now"]
    if haiku.haiku_config(vm) is not None:
        return base + ["shutdown", "-q"]
    if proxmox.proxmox_config(vm) is not None:
        return base + ["systemctl", "poweroff"]
    return base + ["sudo", "systemctl", "poweroff"]


def cmd_cancel_install(args: argparse.Namespace) -> int:
    vm = config.get_vm(config.load_config(), args.vm)
    ui.print_header(f"Cancel installation: {args.vm}")
    if args.dry_run:
        ui.print_status("ok", "Would cancel the TUI installation and stop its VM, preserving disk and logs")
        return 0

    def stop_vm() -> None:
        pid = running_qemu_pid(args.vm, vm)
        if pid is not None:
            stop_qemu_process(
                pid, f"Stop installer VM: {args.vm}", f"VM '{args.vm}'",
                pid_path=bootstrap_pid_path(args.vm),
            )

    try:
        cancelled = tui_jobs.cancel(state.ROOT, args.vm, stop_vm)
    except (OSError, RuntimeError, ValueError) as exc:
        raise VMError(str(exc)) from exc
    ui.print_status("ok", "Installation cancelled; disk and logs preserved" if cancelled
                    else "No active TUI installation to cancel")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    pid_path = bootstrap_pid_path(args.vm)
    running, pid, cmdline = is_bootstrap_vm_running(args.vm)
    if pid is None:
        port = vm_ssh_host_port(vm)
        if port is not None:
            fallback_pid, _ = find_qemu_process_by_hostfwd_port(port, vm_owner_paths(args.vm, vm))
            if fallback_pid is not None:
                return stop_qemu_process(
                    fallback_pid,
                    f"Stop discovered background VM: {args.vm}",
                    f"discovered background VM for '{args.vm}'",
                    dry_run=args.dry_run,
                    qmp_socket=qemu.qmp_socket_path(vm),
                    ssh_poweroff_cmd=ssh_poweroff_command(vm),
                    grace_sec=poweroff_grace_sec(vm),
                    agent_vm=vm,
                    force=getattr(args, "force", False),
                )
        warn_foreign_hostfwd(port, args.vm)
        ui.print_status("ok", f"No tracked background VM for '{args.vm}'")
        return 0
    if not running:
        cleanup_stale_bootstrap_pid(args.vm, dry_run=args.dry_run, emit=True)
        port = vm_ssh_host_port(vm)
        if port is not None:
            fallback_pid, _ = find_qemu_process_by_hostfwd_port(port, vm_owner_paths(args.vm, vm))
            if fallback_pid is not None:
                return stop_qemu_process(
                    fallback_pid,
                    f"Stop discovered background VM: {args.vm}",
                    f"discovered background VM for '{args.vm}'",
                    dry_run=args.dry_run,
                    qmp_socket=qemu.qmp_socket_path(vm),
                    ssh_poweroff_cmd=ssh_poweroff_command(vm),
                    grace_sec=poweroff_grace_sec(vm),
                    agent_vm=vm,
                    force=getattr(args, "force", False),
                )
        warn_foreign_hostfwd(port, args.vm)
        return 0

    assert pid is not None
    return stop_qemu_process(
        pid,
        f"Stop background VM: {args.vm}",
        f"background VM for '{args.vm}'",
        pid_path=pid_path,
        dry_run=args.dry_run,
        qmp_socket=qemu.qmp_socket_path(vm),
        ssh_poweroff_cmd=ssh_poweroff_command(vm),
        grace_sec=poweroff_grace_sec(vm),
        agent_vm=vm,
        force=getattr(args, "force", False),
    )


def cmd_clean_stale(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    selected_names = [args.vm] if getattr(args, "vm", None) else config.sorted_vm_names(cfg)
    removed = 0

    ui.print_header("Clean stale runtime state")
    for name in selected_names:
        if cleanup_stale_bootstrap_pid(name, dry_run=args.dry_run, emit=True):
            removed += 1

    if removed:
        ui.print_status("ok", f"Removed {removed} stale bootstrap PID file(s)")
    else:
        ui.print_status("ok", "No stale bootstrap PID files found")
    return 0


def running_qemu_pid(name: str, vm: dict[str, Any]) -> int | None:
    """PID of the QEMU serving this VM: the tracked background slot, else discovered by disk path."""
    running, pid, _ = is_bootstrap_vm_running(name)
    if running and pid is not None:
        return pid
    pid, _ = find_qemu_process_by_disk_path(runtime.resolve_path(vm["disk"]["path"]))
    return pid


def viewer_command(url: str, host: str, port: int, requested: str | None) -> list[str] | None:
    """The VNC viewer to launch: the user's template, else the first known viewer on PATH."""
    if requested:
        return [part.format(url=url, host=host, port=port) for part in shlex.split(requested)]
    if shutil.which("remote-viewer"):
        return ["remote-viewer", url]
    if shutil.which("vncviewer"):
        return ["vncviewer", f"{host}::{port}"]
    if shutil.which("remmina"):
        return ["remmina", "-c", url]
    return None


def wait_for_interrupt() -> None:
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass


def cmd_attach(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    sock_path = qemu.vnc_socket_path(vm)
    ui.print_header(f"Attach display: {args.vm}")
    if args.dry_run:
        ui.print_kv("vnc socket", ui.pretty_path(sock_path))
        ui.print_status("ok", "Would bridge the VNC socket to 127.0.0.1 and open a viewer")
        return 0

    deadline = time.monotonic() + max(0, getattr(args, "wait", 0))
    pid = running_qemu_pid(args.vm, vm)
    while (pid is None or not sock_path.exists()) and time.monotonic() < deadline:
        time.sleep(0.1)
        pid = running_qemu_pid(args.vm, vm)
    if pid is None:
        raise VMError(f"VM '{args.vm}' is not running (start it with: vmctl start {args.vm} --headless --background)")
    if not sock_path.exists():
        raise VMError(
            f"VM '{args.vm}' (pid {pid}) has no VNC socket: it was started with its own display window, "
            "or by an older vmctl. Only headless VMs can be attached."
        )

    bridge = qemu.UnixSocketBridge(sock_path, port=int(getattr(args, "port", None) or 0))
    bridge.start()
    ui.print_kv("pid", str(pid))
    ui.print_kv("vnc", bridge.url)
    try:
        cmd = None if getattr(args, "no_viewer", False) else viewer_command(bridge.url, bridge.host, bridge.port, getattr(args, "viewer", None))
        if cmd is None:
            if not getattr(args, "no_viewer", False):
                ui.print_status("warn", "No VNC viewer found (remote-viewer, vncviewer, remmina): install virt-viewer or pass --viewer", ok=False)
            ui.print_note("Display exposed on the address above; connect any VNC viewer. Ctrl-C to detach.")
            wait_for_interrupt()
            return 0
        ui.print_command(cmd)
        result = subprocess.run(cmd, check=False)
        if result.returncode != 0:
            ui.print_status("warn", f"Viewer exited with status {result.returncode}", ok=False)
        return 0
    finally:
        bridge.close()


def cmd_console(args: argparse.Namespace) -> int:
    """Interactive serial console of a background VM (login on ttyS0, the pfSense menu); Ctrl-] detaches."""
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    sock_path = qemu.serial_socket_path(vm)
    ui.print_header(f"Serial console: {args.vm}")
    if args.dry_run:
        ui.print_kv("serial socket", ui.pretty_path(sock_path))
        ui.print_status("ok", "Would attach the terminal to the guest serial port (Ctrl-] to detach)")
        return 0
    pid = running_qemu_pid(args.vm, vm)
    if pid is None:
        raise VMError(f"VM '{args.vm}' is not running (start it with: vmctl start {args.vm} --headless --background)")
    if not sock_path.exists():
        raise VMError(
            f"VM '{args.vm}' (pid {pid}) has no serial socket: it was started with a window, during a bootstrap "
            "(the serial is the automation's stdio there), or by an older vmctl. Its log may still be in artifacts/{args.vm}/logs/."
        )
    ui.print_kv("pid", str(pid))
    ui.print_kv("detach", ui.style("Ctrl-]", ui.BOLD, ui.YELLOW))
    ui.print_note("Attached to COM1/ttyS0. Linux guests need a getty on ttyS0 (the lab profiles enable it); pfSense shows its console menu.")
    qemu.serial_console(sock_path)
    return 0


def cmd_export_libvirt(args: argparse.Namespace) -> int:
    vm = config.get_vm(config.load_config(), args.vm)
    if running_qemu_pid(args.vm, vm) is not None or qemu.qmp_command(qemu.qmp_socket_path(vm), "query-status"):
        raise VMError(f"VM '{args.vm}' is running in QEMU; stop it before exporting to libvirt")
    return libvirt.export(args, vm)


def cmd_unexport_libvirt(args: argparse.Namespace) -> int:
    config.get_vm(config.load_config(), args.vm)
    return libvirt.unexport(args)


def cmd_shell(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.run(ssh.ssh_shell_cmd(vm, dry_run=args.dry_run), dry_run=args.dry_run)
    return 0


def cmd_boot_check(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = resolved_vm(args, cfg)
    runtime.ensure_vm_dirs(args.vm)

    ci = vm.get("ci", {})
    expected_text = args.expect or ci.get("expect")
    if not expected_text:
        raise VMError(f"Missing boot expectation for VM '{args.vm}'")

    timeout_sec = args.timeout or ci.get("timeout_sec", 90)
    accel = ci_boot_accel(vm)
    headless = ci.get("headless", True)
    boot_from = ci.get("boot_from", "cdrom")
    auto_inputs = [(item["match"], item["send"]) for item in ci.get("auto_input", [])]

    # A live boot-check still attaches the profile disk, which check-vms creates just before
    # calling us: on a dry run that creation is only printed, so the image is legitimately
    # missing here and must not fail the preview the way every other handler already allows.
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()

    qemu_args = qemu.common_args(
        vm,
        variant=None,
        dry_run=args.dry_run,
        accel=accel,
        headless=headless,
        serial_stdio=True,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
    )

    if boot_from == "cdrom":
        iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
        qemu_args += ["-boot", "once=d", "-cdrom", str(iso_path)]
    elif boot_from != "disk":
        raise VMError(f"Unsupported boot_from mode: {boot_from}")

    qemu.run_and_expect(
        qemu_args,
        expected_text,
        int(timeout_sec),
        auto_inputs=auto_inputs,
        dry_run=args.dry_run,
    )
    if boot_from == "disk":
        vmstate.record_verified(args.vm, "boot-check", str(expected_text), dry_run=args.dry_run)
    ui.print_status("ok", f"Boot check passed for '{args.vm}'")
    return 0


def cmd_check_vm(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    status, detail = run_local_test_once(args.vm, vm, args)
    print(f"__VMCTL_CHECK_VM_RESULT__{json.dumps({'vm': args.vm, 'status': status, 'detail': detail})}")
    return 1 if status == "failed" else 0


def restore_backup_base() -> Path:
    return state.ROOT / "artifacts" / ".check-vms-restore"


def stash_local_test_artifacts(candidates: list[str], dry_run: bool = False) -> dict[str, str]:
    """Move each existing ``artifacts/<vm>`` aside so the matrix runs on a virgin
    state, and return ``{vm: backup_path}`` for the ones actually moved.

    This is the non-destructive alternative to ``--clean-first``: instead of
    deleting an installed VM, the run borrows the artifact directory and
    ``restore_local_test_artifacts`` puts it back afterwards.
    """
    stashed: dict[str, str] = {}
    backup_base = restore_backup_base()
    for vm_name in candidates:
        base = runtime.vm_artifact_base(vm_name)
        if not base.exists():
            continue
        dest = backup_base / vm_name
        ui.print_note(f"Stashing {ui.pretty_path(base)} -> {ui.pretty_path(dest)}")
        if not dry_run:
            if dest.exists():
                shutil.rmtree(dest)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(base), str(dest))
        stashed[vm_name] = str(dest)
    return stashed


def restore_local_test_artifacts(stashed: dict[str, str], dry_run: bool = False) -> None:
    """Remove what the matrix created for each stashed VM and move its original
    artifact directory back into place."""
    for vm_name, backup_path in stashed.items():
        # A failed flow may have left a VM running on the throwaway disk.
        cmd_stop(argparse.Namespace(vm=vm_name, dry_run=dry_run))
        base = runtime.vm_artifact_base(vm_name)
        if base.exists():
            ui.print_note(f"Removing test artifacts {ui.pretty_path(base)}")
            if not dry_run:
                shutil.rmtree(base)
        ui.print_note(f"Restoring {ui.pretty_path(Path(backup_path))} -> {ui.pretty_path(base)}")
        if not dry_run:
            base.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(backup_path, str(base))
    backup_base = restore_backup_base()
    if not dry_run and backup_base.exists() and not any(backup_base.iterdir()):
        backup_base.rmdir()


def cmd_report_pdf(args: argparse.Namespace) -> int:
    """One PDF per profile out of a check-vms report: facts, outcome, screenshot timeline."""
    requested = getattr(args, "report_dir", None)
    directory = runtime.resolve_path(requested) if requested else report.latest_report_dir()
    langs = tuple(part.strip() for part in str(getattr(args, "lang", "en,it")).split(",") if part.strip())
    ui.print_header("Profile documentation")
    ui.print_kv("report", ui.pretty_path(directory))
    ui.print_kv("languages", ", ".join(langs))
    written = profiledoc.build(directory, langs, dry_run=args.dry_run)
    ui.print_status("ok", f"{len(written)} files under {ui.pretty_path(directory / 'pdf')}")
    return 0


def write_profile_sheet(vm_name: str, args: argparse.Namespace) -> None:
    """With --document, a row's PDF is written as soon as the row ends, not hours later.

    Only this profile's sheet: parallel rows would otherwise write the shared index at the same time,
    and the index is rebuilt anyway by the build that closes the run.
    """
    directory = getattr(args, "_report_dir", None)
    if not directory or args.dry_run or not getattr(args, "document", False):
        return
    try:
        profiledoc.build(Path(directory), profiledoc.LANGS, only=vm_name)
    except (VMError, OSError) as exc:
        # A missing weasyprint must not turn a passing row into a failure.
        ui.print_note(f"Profile sheet for {vm_name} not written: {exc}")


def cmd_clean_reports(args: argparse.Namespace) -> int:
    """Keep the newest check-vms reports and remove the rest; never touch one being written."""
    keep = max(0, int(getattr(args, "keep", 5)))
    older_than = getattr(args, "older_than", None)
    ui.print_header("Clean check-vms reports")
    total = report.discover_reports()
    ui.print_kv("reports", str(len(total)))
    ui.print_kv("policy", f"keep the {keep} newest" + (f", remove what is older than {older_than} days" if older_than else ""))
    removed, active = report.prune_reports(keep, older_than, dry_run=args.dry_run)
    for directory in active:
        ui.print_note(f"Written less than {int(report.REPORT_ACTIVE_SEC / 60)} minutes ago, kept: {ui.pretty_path(directory)}")
    for directory, size in removed:
        verb = "Would remove" if args.dry_run else "Removed"
        rows = len(list((directory / "results").glob("*.json"))) if directory.exists() else 0
        detail = f"{rows} row{'s' if rows != 1 else ''}, {runtime.format_bytes(size)}" if rows else runtime.format_bytes(size)
        ui.print_status("ok", f"{verb} {ui.pretty_path(directory)} ({detail})")
    freed = sum(size for _, size in removed)
    if not removed:
        ui.print_status("ok", "Nothing to remove")
    else:
        ui.print_status("ok", f"{'Would free' if args.dry_run else 'Freed'} {runtime.format_bytes(freed)}")
    return 0


EXPERIMENTAL_SKIP_NOTE = "experimental profile: the full matrix skips it, name it on the command line to run it"


def experimental_profiles(cfg: dict[str, Any], names: list[str]) -> list[str]:
    """The profiles whose ``meta.status`` is experimental: a known incomplete flow, not a regression."""
    return [name for name in names if str(config.get_vm(cfg, name).get("meta", {}).get("status") or "") == "experimental"]


def profile_groups(vm: dict[str, Any]) -> dict[str, list[str]]:
    """Every category this profile belongs to, by where the membership comes from.

    Four of the five are *derived* from metadata the profile already carries, so a new VM
    joins them the moment it is written: its ``meta.family`` (``debian``, ``windows``...),
    its ``meta.status``, its ``meta.role`` (``desktop``, ``server``...) and the install flow
    ``local_test_mode`` picks for it (``bootstrap-preseed``...). Only what none of those can
    express is declared by hand in ``meta.groups``: ``ubuntu`` spans a dozen slugs and two
    families of naming, ``netlab`` is a topology, ``smoke`` is a choice.
    """
    meta = vm.get("meta") or {}
    flow, _ = local_test_mode(vm)
    return {
        "declared": config.declared_groups(vm),
        "family": [str(meta["family"])] if meta.get("family") else [],
        "status": [str(meta.get("status") or "manual")],
        "role": [str(meta["role"])] if meta.get("role") else [],
        "flow": [flow] if flow != "skip" else [],
    }


def group_index(cfg: dict[str, Any]) -> dict[str, list[str]]:
    """``{group: [profile names]}`` over the whole catalog, in catalog order."""
    index: dict[str, list[str]] = {}
    for name, vm in config.sorted_vm_items(cfg):
        for group in sorted({group for groups in profile_groups(vm).values() for group in groups}):
            index.setdefault(group, []).append(name)
    return dict(sorted(index.items()))


def group_sources(cfg: dict[str, Any]) -> dict[str, list[str]]:
    """``{group: [source, ...]}``: which part of the metadata puts profiles in that group."""
    sources: dict[str, set[str]] = {}
    for _, vm in config.sorted_vm_items(cfg):
        for source, groups in profile_groups(vm).items():
            for group in groups:
                sources.setdefault(group, set()).add(source)
    order = ["declared", "family", "status", "role", "flow"]
    return {group: [source for source in order if source in found] for group, found in sorted(sources.items())}


def resolve_group_selection(cfg: dict[str, Any], groups: list[str]) -> list[str]:
    """The union of the named groups, in catalog order; an unknown name lists what exists."""
    index = group_index(cfg)
    unknown = [group for group in groups if group not in index]
    if unknown:
        raise VMError(f"Unknown profile group(s): {', '.join(unknown)}. "
                      f"Available: {', '.join(index)} (vmctl list --groups)")
    selected: list[str] = []
    for group in groups:
        selected.extend(index[group])
    return list(dict.fromkeys(selected))


def cluster_row_id(cluster: str) -> str:
    return f"cluster-{cluster}"


def run_cluster_checks(cfg: dict[str, Any], selected_names: list[str],
                       results: list[tuple[str, str, str]], args: argparse.Namespace) -> None:
    """The cross-VM step the rows cannot see: a Proxmox cluster whose nodes were all in this run
    is formed on their fresh disks (runtime NICs, like ``vmctl group install``) and must reach
    quorum, as one more row. A run naming only some nodes has no cluster to check; a node that
    did not pass makes the row a skip, not a second failure."""
    outcomes = {name: status for name, status, _ in results}
    selected = set(selected_names)
    for cluster, entry in pvecluster.clusters(cfg, config.sorted_vm_names(cfg)).items():
        nodes = entry["nodes"]
        if not selected.issuperset(nodes):
            continue
        row = cluster_row_id(cluster)
        primary = config.get_vm(cfg, entry["primary"])
        label = f"Proxmox cluster {cluster} ({' + '.join(nodes)})"
        ui.print_header(f"Test cluster: {cluster}")
        not_passed = [name for name in nodes if outcomes.get(name) != "passed"]
        if not_passed:
            detail = f"skipped: {', '.join(not_passed)} did not pass"
            ui.print_status("skip", f"{row}: {detail}")
            results.append((row, "skipped", detail))
            report.record_group_row(row, label, primary, args, "skipped", detail, 0.0, "group cluster")
            continue
        started = time.monotonic()
        try:
            states = group_states(cfg, nodes)
            for name in nodes:
                if not states[name]["running"]:
                    cmd_start(argparse.Namespace(vm=name, headless=True, background=True, video=None,
                                                 cloud_init=False, spice_port=None, dry_run=args.dry_run))
            pvecluster.form(cfg, nodes, args.timeout, dry_run=args.dry_run)
            status, detail = "passed", f"{len(nodes)} nodes, quorate"
        except (VMError, subprocess.SubprocessError, OSError) as exc:
            status, detail = "failed", str(exc)
        finally:
            for name in reversed(nodes):
                try:
                    cmd_stop(argparse.Namespace(vm=name, dry_run=args.dry_run))
                except VMError as exc:
                    ui.print_status("warn", f"{name}: {exc}", ok=False)
        ui.print_status("ok" if status == "passed" else "fail", f"{row}: {detail}", ok=status == "passed")
        results.append((row, status, detail))
        report.record_group_row(row, label, primary, args, status, detail, time.monotonic() - started, "group cluster")


def cmd_test_local(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    named = list(dict.fromkeys(getattr(args, "vms", None) or []))
    wanted_groups = list(dict.fromkeys(getattr(args, "group", None) or []))
    from_groups = resolve_group_selection(cfg, wanted_groups) if wanted_groups else []
    selected_names = list(dict.fromkeys([*named, *from_groups])) if (named or from_groups) else config.sorted_vm_names(cfg)
    for vm_name in selected_names:
        config.get_vm(cfg, vm_name)
    # A full matrix is a regression run: an experimental profile is expected to stop somewhere
    # (Windows 98 waits at a dialog until the timeout), so it is reported as skipped rather than
    # occupying a worker for an hour. Naming it runs it, which is how it gets promoted; a group
    # is a selector like the full matrix, not that choice, so it skips them too.
    experimental = [name for name in experimental_profiles(cfg, selected_names) if name not in named]
    selected_names = [name for name in selected_names if name not in experimental]
    if wanted_groups:
        ui.print_kv("groups", f"{', '.join(wanted_groups)} ({len(from_groups)} profiles)")
    args.vms = selected_names
    report_directory = report.init(args)
    if getattr(args, "document", False) and report_directory is None:
        raise VMError("--document needs --report: the screenshot timeline and the PDFs live in the report directory")
    results: list[tuple[str, str, str]] = []
    if experimental:
        ui.print_kv("experimental", ", ".join(experimental) + " (skipped; run by name to validate)")
        for vm_name in experimental:
            results.append((vm_name, "skipped", EXPERIMENTAL_SKIP_NOTE))
            report.record(vm_name, config.get_vm(cfg, vm_name), args, "skipped", EXPERIMENTAL_SKIP_NOTE, 0.0, "matrix")
    try:
        parallel = scheduler.parse_parallel(getattr(args, "parallel", 1))
    except ValueError as exc:
        raise VMError(str(exc)) from exc
    matrix_scheduler: scheduler.DynamicScheduler | None = None
    if parallel != 1:
        # `parallel is None` inline, not through `automatic`: mypy narrows the Optional only here.
        automatic = parallel is None
        resources = scheduler.host_resources() if parallel is None else scheduler.HostResources(0, 0, 1)
        max_workers = scheduler.DEFAULT_MAX_WORKERS if parallel is None else parallel
        matrix_scheduler = scheduler.DynamicScheduler(
            resources, max_workers=min(max_workers, max(1, len(selected_names))), resource_budgets=automatic,
        )

    restore = getattr(args, "restore", False)
    ui.print_header("Local VM test matrix")
    ui.print_kv("timeout", f"{args.timeout}s")
    ui.print_kv("parallel", "auto (resource-aware)" if parallel is None else str(parallel))
    if matrix_scheduler is not None:
        scheduler.print_plan(matrix_scheduler, [(name, scheduler.vm_cost(config.get_vm(cfg, name))) for name in selected_names])
    ui.print_kv("mode", "restore (stash + revert)" if restore else "in place")

    stashed: dict[str, str] = {}
    if restore:
        candidates = local_test_clean_candidates(selected_names, cfg)
        if candidates:
            ui.print_header("Stash existing artifacts before the matrix")
            ui.print_kv("profiles", ", ".join(candidates))
            for vm_name in candidates:
                cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
            stashed = stash_local_test_artifacts(candidates, dry_run=args.dry_run)
    else:
        maybe_clean_local_test_candidates(selected_names, cfg, args)

    try:
        if parallel == 1:
            for vm_name in selected_names:
                vm = config.get_vm(cfg, vm_name)
                status, detail = run_local_test_once(vm_name, vm, args)
                results.append((vm_name, status, detail))
        elif matrix_scheduler is not None:
            for vm_name in selected_names:
                ui.print_note(f"{vm_name} logs: {ui.pretty_path(check_vm_stdout_log_path(vm_name))} | {ui.pretty_path(check_vm_stderr_log_path(vm_name))}")
                ui.print_note(f"tail -f {ui.pretty_path(check_vm_stdout_log_path(vm_name))}")
            jobs = [(vm_name, scheduler.vm_cost(config.get_vm(cfg, vm_name))) for vm_name in selected_names]

            def on_start(vm_name: str, cost: scheduler.VmCost, running_now: int) -> None:
                ui.print_note(f"starting {vm_name} ({cost.mem_mb} MB, {cost.cpus} vCPU): {running_now} VM(s) running")

            for vm_name, outcome in matrix_scheduler.run(jobs, lambda name: run_local_test_vm_subprocess(name, args), on_start=on_start):
                if isinstance(outcome, BaseException):
                    results.append((vm_name, "failed", str(outcome)))
                    ui.print_header(f"Test VM: {vm_name}")
                    ui.print_status("fail", f"{vm_name}: {outcome}", ok=False)
                    continue
                status, detail, output = outcome
                if output:
                    print(output, end="" if output.endswith("\n") else "\n")
                results.append((vm_name, status, detail))
            ui.print_kv("peak concurrency", str(matrix_scheduler.peak_running))
        # On the rows' own disks, before --restore puts the stashed ones back.
        run_cluster_checks(cfg, selected_names, results, args)
    finally:
        if stashed:
            ui.print_header("Restore stashed artifacts")
            restore_local_test_artifacts(stashed, dry_run=args.dry_run)
            ui.print_kv("restored", ", ".join(sorted(stashed)))

    if report_directory is not None:
        report.finish(report_directory, args, results, cfg)
        if getattr(args, "document", False):
            # The screenshot timeline is only worth its QMP traffic if someone reads it: --document
            # keeps the frames and turns the report into one PDF per profile, in both languages.
            written = profiledoc.build(report_directory, profiledoc.LANGS, dry_run=args.dry_run)
            ui.print_kv("profile sheets", f"{len(written)} files under {ui.pretty_path(report_directory / 'pdf')}")

    passed = sum(1 for _, status, _ in results if status == "passed")
    failed = sum(1 for _, status, _ in results if status == "failed")
    skipped = sum(1 for _, status, _ in results if status == "skipped")

    ui.print_header("Local VM test summary")
    ui.print_kv("passed", str(passed))
    ui.print_kv("failed", str(failed))
    ui.print_kv("skipped", str(skipped))

    return 1 if failed else 0


# --- setup / clean -------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> int:
    install = getattr(args, "install", None)
    if install is not None:
        # One helper for every dependency: the named ones, or all the missing ones, then the check.
        host_setup.install_tools(install, assume_yes=getattr(args, "yes", False), dry_run=getattr(args, "dry_run", False))
        args.install = None
        args._skip_prompt = True
    cfg = config.load_config()
    verbose = getattr(args, "verbose", False)
    required = set(state.REQUIRED_COMMANDS)
    purposes = {**{name: "required" for name in state.REQUIRED_COMMANDS}, **state.OPTIONAL_COMMANDS}
    status_ok = True
    missing_optional: list[str] = []

    ui.print_header("Host check")
    for group, names in host_setup.SETUP_GROUPS:
        present = [name for name in names if host_setup.tool_present(name)]
        absent = [name for name in names if name not in present]
        shown = [f"textual ({host_setup.textual_location()})" if name == host_setup.TEXTUAL else name for name in present]
        if verbose:
            for name, label in zip(present, shown):
                ui.print_status("ok", f"{label} ({purposes[name]})")
        elif present:
            ui.print_status("ok", f"{group:<19} {host_setup.compact_names(shown)}")
        for name in absent:
            ui.print_status("missing", f"{name} ({purposes[name]}; {host_setup.tool_package(name)})", ok=False)
            if name in required:
                status_ok = False
            else:
                missing_optional.append(name)

    kvm_ok, kvm_detail = host_setup.kvm_status()
    ui.print_status("ok" if kvm_ok else "warn", f"{'KVM':<19} {kvm_detail}", ok=kvm_ok)

    efi_vms = [(name, vm) for name, vm in config.sorted_vm_items(cfg) if vm["firmware"]["type"] == "efi"]
    if not efi_vms:
        ui.print_status("ok", f"{'Firmware':<19} no EFI profiles configured")
    else:
        try:
            _, details = qemu.firmware_status(efi_vms[0][1])
            code = re.search(r"code=(\S+)", details)
            ui.print_status("ok", f"{'Firmware':<19} {code.group(1) if code else details}" if not verbose else details)
        except VMError as exc:
            ui.print_status("missing", str(exc), ok=False)
            ui.print_status("warn", f"Affected EFI profiles: {', '.join(name for name, _ in efi_vms)}", ok=False)
            status_ok = False

    if missing_optional:
        ui.print_note(f"{len(missing_optional)} optional tool(s) missing: make setup installs every missing one "
                      f"(one only: make install {missing_optional[0]})")

    if status_ok:
        ui.print_status("ok", "Setup check passed.")
        return 0

    ui.print_header("Suggested install commands")
    install_commands = host_setup.host_install_commands()
    for cmd in host_setup.host_install_hints():
        ui.print_kv("cmd", cmd)

    if install_commands and not getattr(args, "_skip_prompt", False) and host_setup.prompt_yes_no("Install missing packages now?"):
        ui.print_header("Installing packages")
        try:
            for install_cmd in install_commands:
                runtime.run(install_cmd, dry_run=False)
        except subprocess.CalledProcessError as exc:
            raise VMError(f"Package installation failed: {' '.join(exc.cmd)}") from exc
        ui.print_note("Re-running setup checks")
        args._skip_prompt = True
        return cmd_setup(args)
    return 1


def clean_vm(name: str, vm: dict[str, Any], dry_run: bool = False, checkpoints: bool = False) -> None:
    disk_path = runtime.resolve_path(vm["disk"]["path"])
    fw = vm["firmware"]
    vars_path = runtime.resolve_path(fw["vars_path"]) if fw["type"] == "efi" else None
    extras = [runtime.resolve_path(extra["path"]) for extra in qemu.extra_disks(vm)]
    for path in [disk_path, *extras, vars_path, vmstate.state_path(name)]:
        if path and path.exists():
            ui.print_note(f"Removing {path}")
            if not dry_run:
                path.unlink()
    base = runtime.vm_artifact_base(name)
    for subdir in [
        base / "runtime",
        base / "logs",
        base / "ssh",
        archinstall.archinstall_artifact_dir(vm),
        preseed.preseed_artifact_dir(vm),
        kickstart.kickstart_artifact_dir(vm),
        alpine.alpine_artifact_dir(vm),
        windows.windows_artifact_dir(vm),
        pfsense.pfsense_artifact_dir(vm),
        proxmox.proxmox_artifact_dir(vm),
        netlab.netlab_artifact_dir(vm),
        omarchy.omarchy_artifact_dir(vm),
        cloud_init.cloud_init_artifact_dir(vm),
        cloud_init.autoinstall_artifact_dir(vm),
        cloud_init.unattended_artifact_dir(vm),
        iso.installer_artifact_dir(vm),
    ]:
        if subdir.exists():
            ui.print_note(f"Removing {subdir}")
            if not dry_run:
                shutil.rmtree(subdir)
    # Checkpoints are the way back from a clean: they stay unless --checkpoints says otherwise.
    if checkpoints:
        checkpoint.delete_all(name, dry_run=dry_run)
    else:
        kept = checkpoint.list_checkpoints(name)
        if kept:
            ui.print_note(f"Keeping {len(kept)} checkpoint(s) under {ui.pretty_path(checkpoint.checkpoints_dir(name))}: "
                          f"vmctl checkpoint restore {name} <name> brings one back; vmctl clean {name} --checkpoints removes them")
    if base.exists() and not any(base.iterdir()):
        ui.print_note(f"Removing empty dir {base}")
        if not dry_run:
            base.rmdir()


def cmd_clean(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    checkpoints = bool(getattr(args, "checkpoints", False))
    remove_profile = bool(getattr(args, "remove_profile", False))
    # The guest disk is about to be deleted, so skip guest shutdown and its grace periods.
    if args.all:
        if remove_profile:
            raise VMError("--remove-profile applies to one VM, not to --all")
        for name, vm in config.sorted_vm_items(cfg):
            cmd_stop(argparse.Namespace(vm=name, dry_run=args.dry_run, force=True))
            clean_vm(name, vm, dry_run=args.dry_run, checkpoints=checkpoints)
        return 0
    name = config.canonical_vm_name(args.vm)
    vm = config.get_vm(cfg, name)
    if remove_profile:
        # Refuse before deleting anything: a tracked profile keeps its artifacts too.
        if name in clone.tracked_profile_names():
            raise VMError(f"'{name}' is a tracked profile; --remove-profile removes only profiles that live in local.json alone (clones)")
    cmd_stop(argparse.Namespace(vm=name, dry_run=args.dry_run, force=True))
    clean_vm(name, vm, dry_run=args.dry_run, checkpoints=remove_profile or checkpoints)
    if remove_profile:
        base = runtime.vm_artifact_base(name)
        if base.exists() and not args.dry_run:
            shutil.rmtree(base, ignore_errors=True)
        clone.delete_local_profile(name, dry_run=args.dry_run)
    return 0


# --- checkpoints ---------------------------------------------------------------------------

def libvirt_domain_defined(vm_name: str, uri: str = "qemu:///system") -> bool:
    """Whether the VM was handed to libvirt (export-libvirt): its disk is then libvirt's to run."""
    if shutil.which("virsh") is None:
        return False
    try:
        names = libvirt.virsh_output(uri, "list", "--all", "--name").splitlines()
    except (VMError, subprocess.CalledProcessError, OSError):
        return False
    return libvirt.domain_name(vm_name) in names


def ensure_vm_quiescent(vm_name: str, vm: dict[str, Any], action: str) -> None:
    """A checkpoint or clone touches the disk file itself, so nothing may be using it: no
    QEMU on it (tracked or not), no installation job, no libvirt domain defined over it."""
    runtime_str, note = vm_runtime_status(vm_name, vm)
    if runtime_str.startswith(("tracked:", "hostfwd:", "running:")):
        raise VMError(f"Cannot {action} '{vm_name}' while it is running ({runtime_str}); stop it first (vmctl stop {vm_name})")
    job = tui_jobs.status(tui_jobs.job_dir(state.ROOT, vm_name))
    if job == "running":
        raise VMError(f"Cannot {action} '{vm_name}': an installation is in progress (vmtui Installation Log / Cancel Installation)")
    if libvirt_domain_defined(vm_name):
        raise VMError(f"Cannot {action} '{vm_name}': it is defined in libvirt; vmctl unexport-libvirt {vm_name} first")


def confirm_or_yes(args: argparse.Namespace, prompt: str) -> None:
    """Destructive checkpoint actions ask, unless --yes was given; a pipe never counts as yes."""
    if getattr(args, "yes", False) or getattr(args, "dry_run", False):
        return
    if not runtime.confirm_default_no(prompt):
        raise VMError("Not confirmed (pass --yes to skip the question in scripts)")


def regenerate_clone_identity(dst: str, profile: dict[str, Any], origin: dict[str, Any], timeout_sec: int, dry_run: bool = False) -> None:
    """Boot the clone headless, give it its own hostname, machine-id and SSH host keys, stop it."""
    if dry_run:
        ui.print_note(f"Would boot '{dst}' headless, run the identity script over SSH and stop it")
        return
    ui.print_note(f"Booting '{dst}' headless to regenerate its guest identity")
    start_installed_vm_headless(dst, profile, True, dry_run=False)
    try:
        ssh.wait_for_ssh(profile, timeout_sec)
        ssh.ensure_passwordless_sudo(profile)
        script = clone.identity_script(dst, clone.guest_hostname(origin))
        runtime.run(ssh.remote_sudo_shell_cmd(profile, script), show_command=False)
        ui.print_status("ok", f"Guest identity of '{dst}' regenerated (hostname, machine-id, SSH host keys)")
    finally:
        cmd_stop(argparse.Namespace(vm=dst, dry_run=False, force=False))


def cmd_clone(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    src = config.canonical_vm_name(args.vm)
    vm = config.get_vm(cfg, src)
    dst = clone.validate_name(args.destination)
    mode = str(getattr(args, "identity", None) or "keep")
    if mode not in clone.IDENTITY_CHOICES:
        raise VMError(f"--identity must be one of: {', '.join(clone.IDENTITY_CHOICES)}")
    clone.check_destination(cfg, dst)
    clone.check_source(src, vm)
    ensure_vm_quiescent(src, vm, "clone")
    supported, advice = clone.identity_support(vm)
    if mode == "regenerate" and not supported:
        raise VMError(f"--identity regenerate is not available for '{src}': {advice}. Clone with --identity keep and follow that advice inside the guest.")

    profile, report = clone.derive_profile(cfg, src, vm, dst, ssh_port=getattr(args, "ssh_port", None))
    ui.print_header(f"Clone {src} -> {dst}")
    ui.print_kv("profile", f"'{dst}' added to vms/profiles/local.json (a complete copy: later edits of '{src}' do not follow)")
    for old, new in report["ports"].items():
        ui.print_kv("host port", f"{old} -> {new}")
    if report["macs_dropped"]:
        ui.print_note(f"{report['macs_dropped']} explicit MAC address(es) dropped: the clone's NICs get their own")
    for line in clone.describe_identity(vm, src, dst, mode):
        ui.print_note(line)

    clone.copy_artifacts(src, vm, dst, profile, dry_run=args.dry_run)
    _, created_local = clone.write_profile(dst, profile, dry_run=args.dry_run)
    if mode == "regenerate" and supported:
        try:
            regenerate_clone_identity(dst, profile, vm, int(getattr(args, "timeout", 300) or 300), dry_run=args.dry_run)
        except (VMError, OSError, subprocess.CalledProcessError) as exc:
            # Half a clone must not stay published: unpublish it and say how to redo it.
            if not args.dry_run:
                clone.remove_profile(dst, created=created_local)
                shutil.rmtree(runtime.vm_artifact_base(dst), ignore_errors=True)
            raise VMError(f"Identity regeneration failed ({exc}); the clone '{dst}' was removed again. "
                          f"Retry with --identity keep to get the copy without touching the guest.") from exc
    ui.print_status("ok", f"Clone '{dst}' ready" + (" (dry run)" if args.dry_run else ""))
    ssh_cfg = cloud_init.ssh_access_config(profile)
    if ssh_cfg and ssh_cfg.get("ssh_host_port"):
        ui.print_note(f"vmctl start {dst} --headless --background && vmctl shell {dst}   (SSH on 127.0.0.1:{ssh_cfg['ssh_host_port']})")
    else:
        ui.print_note(f"vmctl start {dst}")
    ui.print_note(f"vmctl clean {dst} removes its artifacts; the profile stays in local.json until you delete the '{dst}' entry")
    return 0


def cmd_checkpoint(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    action = args.action
    if action == "list":
        rows = checkpoint.list_checkpoints(args.vm)
        if getattr(args, "json", False):
            print(json.dumps(rows, indent=2))
            return 0
        ui.print_header(f"Checkpoints of {args.vm}")
        if not rows:
            ui.print_status("ok", f"No checkpoints for '{args.vm}' (vmctl checkpoint create {args.vm} <name>)")
            return 0
        name_width = max(len("NAME"), max(len(row["name"]) for row in rows))
        print(f"{'NAME':<{name_width}}  {'CREATED':<20}  {'ON HOST':>9}  {'CAPACITY':>9}  {'NVRAM':<5}  {'DISK':<10}  NOTE")
        for row in rows:
            created = str(row.get("created_at") or "?")[:19].replace("T", " ")
            capacity = runtime.format_bytes(int(row["virtual_bytes"])) if row.get("virtual_bytes") else "?"
            print(f"{row['name']:<{name_width}}  {created:<20}  {runtime.format_bytes(int(row['host_bytes'])):>9}  {capacity:>9}  "
                  f"{'yes' if row['nvram'] else 'no':<5}  {row['label']:<10}  {row.get('note') or ''}")
        return 0

    name = checkpoint.validate_name(getattr(args, "name", None))
    if action == "create":
        ensure_vm_quiescent(args.vm, vm, "checkpoint")
        checkpoint.create(args.vm, vm, name, note=getattr(args, "note", None), compress=bool(getattr(args, "compress", False)),
                          replace=bool(getattr(args, "replace", False)), dry_run=args.dry_run)
        return 0
    if action == "restore":
        ensure_vm_quiescent(args.vm, vm, "restore a checkpoint into")
        if checkpoint.load_manifest(checkpoint.checkpoint_dir(args.vm, name)) is None:
            raise VMError(f"Checkpoint '{name}' of '{args.vm}' does not exist (vmctl checkpoint list {args.vm})")
        confirm_or_yes(args, f"Replace the current disk (and EFI vars) of '{args.vm}' with checkpoint '{name}'? The current state is lost.")
        checkpoint.restore(args.vm, vm, name, dry_run=args.dry_run)
        return 0
    if action == "delete":
        if not checkpoint.checkpoint_dir(args.vm, name).is_dir():
            raise VMError(f"Checkpoint '{name}' of '{args.vm}' does not exist")
        confirm_or_yes(args, f"Delete checkpoint '{name}' of '{args.vm}'?")
        checkpoint.delete(args.vm, name, dry_run=args.dry_run)
        return 0
    raise VMError(f"Unknown checkpoint action: {action}")
