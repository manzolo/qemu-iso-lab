"""VM lifecycle commands: list, status, install, start, stop, post-install, ..."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from vmctl import cloud_init, config, host_setup, iso, qemu, runtime, ssh, state, ui
from vmctl.errors import VMError


# --- background-VM tracking ----------------------------------------------------

def bootstrap_pid_path(name: str) -> Path:
    return runtime.vm_artifact_base(name) / "runtime" / "bootstrap-start.pid"


def bootstrap_log_path(name: str) -> Path:
    return runtime.vm_artifact_base(name) / "logs" / "bootstrap-start.log"


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
    return raw.replace(b"\x00", b" ").decode(errors="replace").strip()


def local_tcp_port_open(port: int, host: str = "127.0.0.1", timeout_sec: float = 0.3) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except OSError:
        return False


def find_qemu_process_by_hostfwd_port(port: int) -> tuple[int | None, str | None]:
    needles = (f":127.0.0.1:{port}-:22", f":{port}-:22")
    proc_root = Path("/proc")
    for proc_dir in proc_root.iterdir():
        if not proc_dir.name.isdigit():
            continue
        try:
            raw = (proc_dir / "cmdline").read_bytes()
        except OSError:
            continue
        if not raw:
            continue
        cmdline = raw.replace(b"\x00", b" ").decode(errors="replace").strip()
        if "qemu-system-x86_64" not in cmdline:
            continue
        if any(needle in cmdline for needle in needles):
            return int(proc_dir.name), cmdline
    return None, None


def is_bootstrap_vm_running(name: str) -> tuple[bool, int | None, str | None]:
    pid = read_pid_file(bootstrap_pid_path(name))
    if pid is None:
        return (False, None, None)
    cmdline = process_cmdline(pid)
    if not cmdline or "qemu-system-x86_64" not in cmdline:
        return (False, pid, cmdline)
    return (True, pid, cmdline)


def vm_runtime_status(name: str, vm: dict[str, Any]) -> tuple[str, str]:
    running, pid, cmdline = is_bootstrap_vm_running(name)
    if running and pid is not None:
        return (f"tracked:{pid}", "-")

    ssh_cfg = cloud_init.ssh_access_config(vm)
    if ssh_cfg is None or not ssh_cfg.get("ssh_host_port"):
        return ("-", "-")

    port = int(ssh_cfg["ssh_host_port"])
    pid, cmdline = find_qemu_process_by_hostfwd_port(port)
    if pid is not None:
        return (f"hostfwd:{port}", f"pid={pid}")
    if local_tcp_port_open(port):
        return (f"open:{port}", "-")
    return (f"closed:{port}", "-")


def stop_qemu_process(
    pid: int,
    header: str,
    description: str,
    pid_path: Path | None = None,
    dry_run: bool = False,
) -> int:
    ui.print_header(header)
    ui.print_kv("pid", str(pid))
    cmdline = process_cmdline(pid)
    if cmdline:
        ui.print_kv("cmd", cmdline)
    if dry_run:
        ui.print_status("ok", f"Would stop {description} (pid {pid})")
        return 0

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        raise VMError(f"Failed to stop process {pid}: {exc}") from exc

    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process_cmdline(pid) is None:
            if pid_path is not None and pid_path.exists():
                pid_path.unlink()
            ui.print_status("ok", f"Stopped {description}")
            return 0
        time.sleep(1)

    raise VMError(f"Timed out stopping {description} (pid {pid})")


def prepare_background_vm_slot(name: str, dry_run: bool = False) -> tuple[Path, Path]:
    pid_path = bootstrap_pid_path(name)
    log_path = bootstrap_log_path(name)
    running, pid, _ = is_bootstrap_vm_running(name)
    if running:
        raise VMError(f"Background VM for '{name}' is already running (pid {pid})")
    if pid is not None and pid_path.exists():
        ui.print_status("warn", f"Removing stale background VM PID file for '{name}'", ok=False)
        if not dry_run:
            pid_path.unlink()
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
    return disk_path


# --- info commands -------------------------------------------------------------

def cmd_list(args: argparse.Namespace) -> int:
    cfg = config.load_config()

    if getattr(args, "json", False):
        out = []
        for name, vm in sorted(cfg["vms"].items()):
            out.append({
                "profile": name,
                "name": vm.get("name", name),
                "firmware": vm.get("firmware", {}).get("type"),
                "memory_mb": vm.get("memory_mb"),
                "cpus": vm.get("cpus"),
            })
        print(json.dumps(out, indent=2))
        return 0

    rows = []
    for name, vm in sorted(cfg["vms"].items()):
        firmware = vm.get("firmware", {}).get("type", "?").upper()
        memory = vm.get("memory_mb", "?")
        cpus = vm.get("cpus", "?")
        rows.append((name, vm.get("name", name), firmware, str(memory), str(cpus)))

    name_width = max(len("PROFILE"), max(len(row[0]) for row in rows))
    label_width = max(len("NAME"), max(len(row[1]) for row in rows))
    firmware_width = max(len("FW"), max(len(row[2]) for row in rows))
    memory_width = max(len("RAM"), max(len(f"{row[3]}M") for row in rows))
    cpu_width = max(len("CPU"), max(len(row[4]) for row in rows))
    print(f"{ui.style('PROFILE', ui.BOLD, ui.CYAN):<{name_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('NAME', ui.BOLD, ui.CYAN):<{label_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('FW', ui.BOLD, ui.CYAN):>{firmware_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('RAM', ui.BOLD, ui.CYAN):>{memory_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('CPU', ui.BOLD, ui.CYAN):>{cpu_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}")
    for name, label, firmware, memory, cpus in rows:
        print(f"{ui.style(name, ui.BOLD):<{name_width + len(ui.BOLD) + len(ui.RESET)}}  "
              f"{label:<{label_width}}  "
              f"{firmware:>{firmware_width}}  "
              f"{f'{memory}M':>{memory_width}}  "
              f"{cpus:>{cpu_width}}")
    return 0


def disk_status(vm: dict[str, Any]) -> tuple[str, str, str]:
    disk_path = runtime.resolve_path(vm["disk"]["path"])
    if not disk_path.is_file():
        return "missing", "-", "-"

    actual_size = runtime.format_bytes(disk_path.stat().st_size)
    virtual_size = "-"
    if shutil.which("qemu-img") is not None:
        try:
            virtual_size = runtime.format_bytes(int(runtime.image_info(disk_path, quiet=True).get("virtual-size", 0) or 0))
        except Exception:
            virtual_size = "?"
    return "ready", actual_size, virtual_size


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


def cmd_status(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    rows = []
    for name, vm in sorted(cfg["vms"].items()):
        if not args.all and not vm_has_local_state(vm):
            continue
        disk, actual, virtual = disk_status(vm)
        runtime_str, runtime_note = vm_runtime_status(name, vm)
        rows.append((name, disk, iso_status(vm), nvram_status(vm), runtime_str, actual, virtual, runtime_note))

    if getattr(args, "json", False):
        out = [
            {
                "profile": name,
                "disk": disk,
                "iso": iso_str,
                "nvram": nvram,
                "runtime": runtime_str,
                "actual_size": actual,
                "virtual_size": virtual,
                "runtime_note": runtime_note if runtime_note != "-" else None,
            }
            for name, disk, iso_str, nvram, runtime_str, actual, virtual, runtime_note in rows
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
    actual_width = max(len("ACTUAL"), max(len(row[5]) for row in rows))
    virtual_width = max(len("VIRTUAL"), max(len(row[6]) for row in rows))

    print(f"{ui.style('PROFILE', ui.BOLD, ui.CYAN):<{name_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('DISK', ui.BOLD, ui.CYAN):<{disk_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('ISO', ui.BOLD, ui.CYAN):<{iso_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('NVRAM', ui.BOLD, ui.CYAN):<{nvram_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('RUNTIME', ui.BOLD, ui.CYAN):<{runtime_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('ACTUAL', ui.BOLD, ui.CYAN):>{actual_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}  "
          f"{ui.style('VIRTUAL', ui.BOLD, ui.CYAN):>{virtual_width + len(ui.BOLD) + len(ui.CYAN) + len(ui.RESET)}}")
    for name, disk, iso_str, nvram, runtime_str, actual, virtual, runtime_note in rows:
        print(f"{ui.style(name, ui.BOLD):<{name_width + len(ui.BOLD) + len(ui.RESET)}}  "
              f"{disk:<{disk_width}}  "
              f"{iso_str:<{iso_width}}  "
              f"{nvram:<{nvram_width}}  "
              f"{runtime_str:<{runtime_width}}  "
              f"{actual:>{actual_width}}  "
              f"{virtual:>{virtual_width}}")
        if runtime_note != "-":
            print(f"{'':<{name_width}}  {'':<{disk_width}}  {'':<{iso_width}}  {'':<{nvram_width}}  "
                  f"{ui.style('note', ui.CYAN):<{runtime_width}}  {runtime_note}")
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

    qemu_args = qemu.common_args(
        vm,
        qemu.installer_video_variant(vm, args.video),
        dry_run=args.dry_run,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        spice_port=getattr(args, "spice_port", None),
    )
    qemu_args += ["-cdrom", str(iso_path)]
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
    runtime.run(qemu_args, dry_run=args.dry_run)
    return 0


def cmd_install_unattended(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.ensure_vm_dirs(args.vm)
    iso_path = iso.ensure_iso(vm, dry_run=args.dry_run)
    disk_exists = runtime.resolve_path(vm["disk"]["path"]).exists()
    ensure_vm_disk(vm, dry_run=args.dry_run)
    seed_path = cloud_init.create_autoinstall_seed(args.vm, vm, dry_run=args.dry_run)
    kernel_path, initrd_path = iso.extract_installer_boot_artifacts(vm, iso_path, dry_run=args.dry_run)
    headless = getattr(args, "headless", False)
    qemu_args = qemu.common_args(
        vm,
        None if headless else qemu.installer_video_variant(vm, args.video),
        dry_run=args.dry_run,
        headless=headless,
        no_reboot=True,
        allow_missing_disk=args.dry_run and not disk_exists,
        enable_clipboard=False,
        spice_port=getattr(args, "spice_port", None),
    )
    qemu_args += ["-cdrom", str(iso_path)]
    qemu_args += cloud_init.cloud_init_drive_args(seed_path)
    qemu_args += ["-kernel", str(kernel_path), "-initrd", str(initrd_path), "-append", "autoinstall"]
    runtime.run(qemu_args, dry_run=args.dry_run)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.ensure_vm_dirs(args.vm)
    spice_port = getattr(args, "spice_port", None)
    qemu_args = qemu.common_args(vm, args.video, dry_run=args.dry_run, headless=args.headless, spice_port=spice_port)
    if args.cloud_init:
        qemu_args += cloud_init.cloud_init_drive_args(cloud_init.create_cloud_init_seed(args.vm, vm, dry_run=args.dry_run))
    if args.background:
        if not args.headless and spice_port is None:
            raise VMError("--background currently requires --headless or --spice-port")
        pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
        pid = runtime.run_background(qemu_args, log_path, dry_run=args.dry_run)
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
    ssh.wait_for_ssh(vm, timeout_sec, dry_run=dry_run)
    ui.print_status("ok", f"SSH is ready for VM '{vm_name}'")
    ssh.wait_for_guest_post_install_ready(vm, dry_run=dry_run)
    ui.print_note("Running post-install provisioning")

    for entry in ssh_cfg.get("copy_from_host", []):
        if not isinstance(entry, dict):
            raise VMError("Invalid copy_from_host entry: expected object")
        ssh.post_install_copy(vm, entry, dry_run=dry_run)

    for command in ssh_cfg.get("post_install_run", []):
        ssh.post_install_run(vm, str(command), dry_run=dry_run)


def cmd_post_install(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    run_post_install(args.vm, vm, args.timeout, dry_run=args.dry_run)
    ui.print_status("ok", f"Post-install completed for VM '{args.vm}'")
    return 0


def cmd_bootstrap_unattended(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    if cloud_init.autoinstall_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define autoinstall")
    if cloud_init.cloud_init_config(vm) is None:
        raise VMError(f"VM '{args.vm}' does not define cloud_init")

    runtime.ensure_vm_dirs(args.vm)

    ui.print_header(f"Bootstrap VM unattended: {args.vm}")
    cmd_install_unattended(
        argparse.Namespace(
            vm=args.vm,
            video=args.video,
            headless=getattr(args, "headless", False),
            spice_port=getattr(args, "spice_port", None),
            dry_run=args.dry_run,
        )
    )

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    qemu_args = qemu.common_args(vm, None, dry_run=args.dry_run, headless=True)
    pid = runtime.run_background(qemu_args, log_path, dry_run=args.dry_run)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

    run_post_install(args.vm, vm, args.timeout, dry_run=args.dry_run)

    ui.print_status("ok", f"Post-install completed for VM '{args.vm}'")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    pid_path = bootstrap_pid_path(args.vm)
    running, pid, cmdline = is_bootstrap_vm_running(args.vm)
    if pid is None:
        ssh_cfg = cloud_init.ssh_access_config(vm)
        if ssh_cfg is not None and ssh_cfg.get("ssh_host_port"):
            port = int(ssh_cfg["ssh_host_port"])
            fallback_pid, _ = find_qemu_process_by_hostfwd_port(port)
            if fallback_pid is not None:
                return stop_qemu_process(
                    fallback_pid,
                    f"Stop discovered background VM: {args.vm}",
                    f"discovered background VM for '{args.vm}'",
                    dry_run=args.dry_run,
                )
        ui.print_status("ok", f"No tracked background VM for '{args.vm}'")
        return 0
    if not running:
        ui.print_status("warn", f"Removing stale bootstrap PID file for '{args.vm}'", ok=False)
        if not args.dry_run and pid_path.exists():
            pid_path.unlink()
        ssh_cfg = cloud_init.ssh_access_config(vm)
        if ssh_cfg is not None and ssh_cfg.get("ssh_host_port"):
            port = int(ssh_cfg["ssh_host_port"])
            fallback_pid, _ = find_qemu_process_by_hostfwd_port(port)
            if fallback_pid is not None:
                return stop_qemu_process(
                    fallback_pid,
                    f"Stop discovered background VM: {args.vm}",
                    f"discovered background VM for '{args.vm}'",
                    dry_run=args.dry_run,
                )
        return 0

    assert pid is not None
    return stop_qemu_process(
        pid,
        f"Stop background VM: {args.vm}",
        f"background VM for '{args.vm}'",
        pid_path=pid_path,
        dry_run=args.dry_run,
    )


def cmd_shell(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.run(ssh.ssh_shell_cmd(vm, dry_run=args.dry_run), dry_run=args.dry_run)
    return 0


def cmd_boot_check(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.ensure_vm_dirs(args.vm)

    ci = vm.get("ci", {})
    expected_text = args.expect or ci.get("expect")
    if not expected_text:
        raise VMError(f"Missing boot expectation for VM '{args.vm}'")

    timeout_sec = args.timeout or ci.get("timeout_sec", 90)
    accel = ci.get("accel", "tcg")
    headless = ci.get("headless", True)
    boot_from = ci.get("boot_from", "cdrom")
    auto_inputs = [(item["match"], item["send"]) for item in ci.get("auto_input", [])]

    qemu_args = qemu.common_args(
        vm,
        variant=None,
        dry_run=args.dry_run,
        accel=accel,
        headless=headless,
        serial_stdio=True,
        no_reboot=True,
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
    ui.print_status("ok", f"Boot check passed for '{args.vm}'")
    return 0


# --- setup / clean -------------------------------------------------------------

def cmd_setup(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    status_ok = True

    ui.print_header("Host dependency check")
    for name in state.REQUIRED_COMMANDS:
        present = shutil.which(name) is not None
        marker = "ok" if present else "missing"
        ui.print_status(marker, name, ok=present)
        status_ok = status_ok and present

    ui.print_header("Optional tools")
    for name, note in state.OPTIONAL_COMMANDS.items():
        present = shutil.which(name) is not None
        marker = "ok" if present else "missing"
        ui.print_status(marker, f"{name} ({note})", ok=present)

    ui.print_header("Firmware check")
    efi_vms = [(name, vm) for name, vm in sorted(cfg["vms"].items()) if vm["firmware"]["type"] == "efi"]
    if not efi_vms:
        ui.print_status("ok", "No EFI profiles configured")
    else:
        try:
            _, details = qemu.firmware_status(efi_vms[0][1])
            ui.print_status("ok", details)
        except VMError as exc:
            ui.print_status("missing", str(exc), ok=False)
            ui.print_status("warn", f"Affected EFI profiles: {', '.join(name for name, _ in efi_vms)}", ok=False)
            status_ok = False

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


def clean_vm(name: str, vm: dict[str, Any], dry_run: bool = False) -> None:
    disk_path = runtime.resolve_path(vm["disk"]["path"])
    fw = vm["firmware"]
    vars_path = runtime.resolve_path(fw["vars_path"]) if fw["type"] == "efi" else None
    for path in [disk_path, vars_path]:
        if path and path.exists():
            ui.print_note(f"Removing {path}")
            if not dry_run:
                path.unlink()
    base = runtime.vm_artifact_base(name)
    for subdir in [
        base / "runtime",
        base / "logs",
        cloud_init.cloud_init_artifact_dir(vm),
        cloud_init.autoinstall_artifact_dir(vm),
        iso.installer_artifact_dir(vm),
    ]:
        if subdir.exists():
            ui.print_note(f"Removing {subdir}")
            if not dry_run:
                shutil.rmtree(subdir)
    if base.exists() and not any(base.iterdir()):
        ui.print_note(f"Removing empty dir {base}")
        if not dry_run:
            base.rmdir()


def cmd_clean(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    if args.all:
        for name, vm in sorted(cfg["vms"].items()):
            cmd_stop(argparse.Namespace(vm=name, dry_run=args.dry_run))
            clean_vm(name, vm, dry_run=args.dry_run)
        return 0
    vm = config.get_vm(cfg, args.vm)
    cmd_stop(argparse.Namespace(vm=args.vm, dry_run=args.dry_run))
    clean_vm(args.vm, vm, dry_run=args.dry_run)
    return 0
