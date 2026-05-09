"""VM lifecycle commands: list, status, install, start, stop, post-install, ..."""
from __future__ import annotations

import argparse
import concurrent.futures
import copy

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from vmctl import archinstall, cloud_init, config, host_setup, iso, preseed, kickstart, qemu, runtime, ssh, state, ui
from vmctl.errors import VMError


# --- background-VM tracking ----------------------------------------------------

def bootstrap_pid_path(name: str) -> Path:
    return runtime.vm_artifact_base(name) / "runtime" / "bootstrap-start.pid"


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


def find_qemu_process_by_disk_path(disk_path: Path) -> tuple[int | None, str | None]:
    needle = str(disk_path)
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
        if needle in cmdline:
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
) -> int:
    def finalize_stop(message: str) -> int:
        if pid_path is not None and pid_path.exists():
            pid_path.unlink()
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

    ssh_cfg = cloud_init.ssh_access_config(prepared)
    if ssh_cfg is None or not ssh_cfg.get("ssh_host_port"):
        return prepared, None

    port = int(ssh_cfg["ssh_host_port"])
    if not local_tcp_port_open(port):
        return prepared, None

    new_port = pick_free_local_port()
    ssh_cfg["ssh_host_port"] = new_port
    return prepared, f"SSH host port {port} busy, using {new_port} for check-vms"


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
        if mode in {"bootstrap-unattended", "bootstrap-archinstall", "bootstrap-preseed", "bootstrap-kickstart"}:
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


def run_local_test_vm(
    vm_name: str,
    vm: dict[str, Any],
    args: argparse.Namespace,
) -> tuple[str, str]:
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
                )
            )
        finally:
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
                )
            )
        finally:
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
                )
            )
        finally:
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
                )
            )
        finally:
            cmd_stop(argparse.Namespace(vm=vm_name, dry_run=args.dry_run))
        detail = f"{note}; stopped after check-vms"
        if prep_note is not None:
            detail = f"{detail}; {prep_note}"
        return ("passed", detail)
    if mode == "boot-check":
        cmd_boot_check(
            argparse.Namespace(
                vm=vm_name,
                expect=None,
                timeout=args.timeout,
                dry_run=args.dry_run,
                _vm_override=prepared_vm,
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
    try:
        status, detail = run_local_test_vm(vm_name, vm, args)
    except VMError as exc:
        status = "failed"
        detail = str(exc)
        ui.print_status("fail", f"{vm_name}: {exc}", ok=False)
        return (status, detail)

    if status == "passed":
        ui.print_status("ok", f"{vm_name}: {detail}")
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


def format_runtime_cell(runtime_str: str, runtime_note: str) -> str:
    if runtime_note == "-" or not runtime_note:
        return runtime_str
    return f"{runtime_str} ({runtime_note})"


def status_cell_style(value: str) -> tuple[str, ...]:
    if value in {"ready"}:
        return (ui.GREEN, ui.BOLD)
    if value in {"missing"}:
        return (ui.YELLOW, ui.BOLD)
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
    for name, vm in sorted(cfg["vms"].items()):
        if not args.all and not vm_has_local_state(vm):
            continue
        disk, actual, virtual = disk_status(vm)
        runtime_str, runtime_note = vm_runtime_status(name, vm)
        runtime_cell = format_runtime_cell(runtime_str, runtime_note)
        rows.append((name, disk, iso_status(vm), nvram_status(vm), runtime_cell, actual, virtual, runtime_note))

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
              f"{style_status_cell(disk, disk_width)}  "
              f"{style_status_cell(iso_str, iso_width)}  "
              f"{style_status_cell(nvram, nvram_width)}  "
              f"{style_status_cell(runtime_str, runtime_width)}  "
              f"{actual:>{actual_width}}  "
              f"{virtual:>{virtual_width}}")
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
    runtime.run(qemu_args, dry_run=args.dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    return 0


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
    )
    install_qemu_args += ["-cdrom", str(iso_path)]
    install_qemu_args += archinstall.config_iso_drive_args(bootstrap_iso)
    install_qemu_args += [
        "-kernel", str(kernel_path),
        "-initrd", str(initrd_path),
        "-append", f"archisobasedir=arch archisolabel={iso_label} console=ttyS0,115200 quiet",
    ]

    trigger = "mkdir -p /tmp/archconf && mount /dev/vdb /tmp/archconf && bash /tmp/archconf/run.sh"
    ui.print_note("Booting Arch live ISO — waiting for shell, then triggering automated install...")
    serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/bootstrap-serial.log")
    qemu.run_and_expect(
        install_qemu_args,
        expected_text=archinstall.BOOTSTRAP_COMPLETE_TOKEN,
        timeout_sec=getattr(args, "timeout", 1800),
        auto_inputs=[
            (archinstall.ARCH_SERIAL_LOGIN_PROMPT, "root\n"),
            (archinstall.ARCH_LIVE_PROMPT, f"\n{trigger}\n"),
        ],
        dry_run=args.dry_run,
        log_path=serial_log,
    )
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    run_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        allow_missing_disk=args.dry_run and not disk_exists,
    )
    post_serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/post-install-serial.log")
    runtime.ensure_parent(post_serial_log)
    run_qemu_args += ["-serial", f"file:{post_serial_log}"]
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(run_qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

    run_post_install(args.vm, vm, getattr(args, "timeout", 300), dry_run=args.dry_run)
    ui.print_status("ok", f"Bootstrap complete for VM '{args.vm}'")
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
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    run_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        allow_missing_disk=args.dry_run and not disk_exists,
    )
    post_serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/post-install-serial.log")
    runtime.ensure_parent(post_serial_log)
    run_qemu_args += ["-serial", f"file:{post_serial_log}"]
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(run_qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

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
    reset_vm_nvram(vm, dry_run=args.dry_run)

    seed_iso = kickstart.create_kickstart_iso(args.vm, vm, dry_run=args.dry_run)
    kernel_path, initrd_path = kickstart.extract_kickstart_boot_artifacts(vm, iso_path, dry_run=args.dry_run)

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
    )
    install_qemu_args += ["-cdrom", str(iso_path)]
    install_qemu_args += kickstart.kickstart_iso_drive_args(seed_iso)
    install_qemu_args += [
        "-kernel", str(kernel_path),
        "-initrd", str(initrd_path),
        "-append", "inst.ks=hd:LABEL=KS_CFG:/ks.cfg inst.text inst.cmdline inst.repo=cdrom console=ttyS0,115200",
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
    ui.print_status("ok", "Installation complete — starting installed VM for post-install")

    pid_path, log_path = prepare_background_vm_slot(args.vm, dry_run=args.dry_run)
    run_qemu_args = qemu.common_args(
        vm,
        None,
        dry_run=args.dry_run,
        accel=automation_accel(vm),
        headless=True,
        allow_missing_disk=args.dry_run and not disk_exists,
    )
    post_serial_log = runtime.resolve_path(f"artifacts/{args.vm}/logs/post-install-serial.log")
    runtime.ensure_parent(post_serial_log)
    run_qemu_args += ["-serial", f"file:{post_serial_log}"]
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(run_qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
    if pid is not None:
        pid_path.write_text(f"{pid}\n", encoding="utf-8")
        ui.print_kv("pid", str(pid))

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
    )
    qemu_args += ["-cdrom", str(iso_path)]
    qemu_args += cloud_init.cloud_init_drive_args(seed_path)
    qemu_args += ["-kernel", str(kernel_path), "-initrd", str(initrd_path), "-append", append_args]
    stdout_log, stderr_log = announce_phase_logs(args.vm, "install-unattended")
    runtime.run(qemu_args, dry_run=args.dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    return 0


def cmd_start(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    runtime.ensure_vm_dirs(args.vm)

    if not getattr(args, "background", False):
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
                serial_stdio=True,
                spice_port=spice_port,
            )
            qemu_args += cloud_init_args
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
    ssh.wait_for_guest_post_install_ready(vm, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)
    ui.print_note("Running post-install provisioning")

    for entry in ssh_cfg.get("copy_from_host", []):
        if not isinstance(entry, dict):
            raise VMError("Invalid copy_from_host entry: expected object")
        ssh.post_install_copy(vm, entry, dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)

    for command in ssh_cfg.get("post_install_run", []):
        ssh.post_install_run(vm, str(command), dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log)


def cmd_post_install(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
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
    )
    stderr_log = companion_stderr_log_path(log_path)
    pid = runtime.run_background(qemu_args, log_path, dry_run=args.dry_run, stderr_path=stderr_log)
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
        cleanup_stale_bootstrap_pid(args.vm, dry_run=args.dry_run, emit=True)
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


def cmd_clean_stale(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    selected_names = [args.vm] if getattr(args, "vm", None) else sorted(cfg["vms"])
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


def cmd_check_vm(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    vm = config.get_vm(cfg, args.vm)
    status, detail = run_local_test_once(args.vm, vm, args)
    print(f"__VMCTL_CHECK_VM_RESULT__{json.dumps({'vm': args.vm, 'status': status, 'detail': detail})}")
    return 1 if status == "failed" else 0


def cmd_test_local(args: argparse.Namespace) -> int:
    cfg = config.load_config()
    selected_names = list(args.vms) if getattr(args, "vms", None) else sorted(cfg["vms"])
    results: list[tuple[str, str, str]] = []
    parallel = max(1, int(getattr(args, "parallel", 1)))

    ui.print_header("Local VM test matrix")
    ui.print_kv("timeout", f"{args.timeout}s")
    ui.print_kv("parallel", str(parallel))
    maybe_clean_local_test_candidates(selected_names, cfg, args)

    if parallel == 1:
        for vm_name in selected_names:
            vm = config.get_vm(cfg, vm_name)
            status, detail = run_local_test_once(vm_name, vm, args)
            results.append((vm_name, status, detail))
    else:
        with concurrent.futures.ThreadPoolExecutor(max_workers=parallel) as executor:
            future_map = {
                executor.submit(run_local_test_vm_subprocess, vm_name, args): vm_name
                for vm_name in selected_names
            }
            for vm_name in selected_names:
                stdout_log = check_vm_stdout_log_path(vm_name)
                stderr_log = check_vm_stderr_log_path(vm_name)
                ui.print_note(
                    f"{vm_name} logs: {ui.pretty_path(stdout_log)} | {ui.pretty_path(stderr_log)}"
                )
                ui.print_note(
                    f"tail -f {ui.pretty_path(stdout_log)}"
                )
            for future in concurrent.futures.as_completed(future_map):
                vm_name = future_map[future]
                try:
                    status, detail, output = future.result()
                except Exception as exc:
                    results.append((vm_name, "failed", str(exc)))
                    ui.print_header(f"Test VM: {vm_name}")
                    ui.print_status("fail", f"{vm_name}: {exc}", ok=False)
                    continue
                if output:
                    print(output, end="" if output.endswith("\n") else "\n")
                results.append((vm_name, status, detail))

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
        base / "ssh",
        archinstall.archinstall_artifact_dir(vm),
        preseed.preseed_artifact_dir(vm),
        kickstart.kickstart_artifact_dir(vm),
        cloud_init.cloud_init_artifact_dir(vm),
        cloud_init.autoinstall_artifact_dir(vm),
        cloud_init.unattended_artifact_dir(vm),
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
