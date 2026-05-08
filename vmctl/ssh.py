"""SSH helpers: target resolution, base commands, wait, post-install copy/run."""
from __future__ import annotations

import shlex
import shutil
import stat
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

from vmctl import cloud_init, flash, runtime, ui
from vmctl.errors import VMError


def generated_ssh_key_path(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "ssh" / "id_ed25519"


def _configured_ssh_key(cfg: dict[str, Any]) -> Path | None:
    key_path = str(cfg.get("ssh_key") or "").strip()
    if not key_path:
        return None
    return runtime.expand_host_path(key_path)


def ensure_generated_ssh_keypair(vm: dict[str, Any], dry_run: bool = False) -> Path:
    private = generated_ssh_key_path(vm)
    public = private.parent / f"{private.name}.pub"
    if dry_run:
        return private
    private.parent.mkdir(parents=True, exist_ok=True)
    if private.exists() and public.exists():
        return private
    if private.exists() and not public.exists():
        public.write_text(
            subprocess.check_output(["ssh-keygen", "-y", "-f", str(private)], text=True).strip() + "\n",
            encoding="utf-8",
        )
        return private
    runtime.require_command("ssh-keygen")
    comment = str(vm.get("name") or vm.get("archinstall_config", {}).get("hostname") or "vmctl")
    runtime.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", f"vmctl {comment}", "-f", str(private)])
    return private


def resolve_ssh_private_key(vm: dict[str, Any], cfg: dict[str, Any], dry_run: bool = False) -> Path | None:
    configured = _configured_ssh_key(cfg)
    if configured is not None:
        if not configured.is_file():
            if dry_run:
                return None
            raise VMError(f"SSH private key not found: {configured}")
        return configured
    if cloud_init.ssh_access_config(vm) is cfg:
        return ensure_generated_ssh_keypair(vm, dry_run=dry_run)
    return None


def resolve_ssh_public_key(vm: dict[str, Any], cfg: dict[str, Any], dry_run: bool = False) -> Path | None:
    configured = _configured_ssh_key(cfg)
    if configured is not None:
        public = configured.parent / f"{configured.name}.pub"
        if not public.is_file():
            if dry_run:
                return None
            raise VMError(f"SSH public key not found at {public} (expected for ssh_provision)")
        return public
    if cloud_init.ssh_access_config(vm) is cfg:
        private = ensure_generated_ssh_keypair(vm, dry_run=dry_run)
        return private.parent / f"{private.name}.pub"
    return None


def ssh_target(vm: dict[str, Any]) -> tuple[str, int, str]:
    cfg = cloud_init.ssh_access_config(vm)
    if cfg is None:
        raise VMError("VM profile does not define SSH provisioning")
    user = str(cfg.get("user") or "").strip()
    port = int(cfg.get("ssh_host_port") or 0)
    if not user:
        raise VMError("SSH provisioning user is required")
    if port <= 0:
        raise VMError("SSH provisioning ssh_host_port is required")
    return ("127.0.0.1", port, user)


def _ssh_common_opts(cfg: dict[str, Any], dry_run: bool = False) -> list[str]:
    opts = ["-F", "/dev/null", "-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null"]
    return opts


def ssh_base_cmd(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    host, port, user = ssh_target(vm)
    cfg = cloud_init.ssh_access_config(vm)
    assert cfg is not None
    opts = _ssh_common_opts(cfg, dry_run=dry_run)
    private = resolve_ssh_private_key(vm, cfg, dry_run=dry_run)
    if private is not None:
        opts += ["-i", str(private)]
    return ["ssh"] + opts + ["-o", "BatchMode=yes", "-p", str(port), f"{user}@{host}"]


def ssh_shell_cmd(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    host, port, user = ssh_target(vm)
    cfg = cloud_init.ssh_access_config(vm)
    assert cfg is not None
    opts = _ssh_common_opts(cfg, dry_run=dry_run)
    private = resolve_ssh_private_key(vm, cfg, dry_run=dry_run)
    if private is not None:
        opts += ["-i", str(private)]
    return ["ssh"] + opts + ["-p", str(port), f"{user}@{host}"]


def scp_base_cmd(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    _, port, _ = ssh_target(vm)
    cfg = cloud_init.ssh_access_config(vm)
    assert cfg is not None
    opts = _ssh_common_opts(cfg, dry_run=dry_run)
    private = resolve_ssh_private_key(vm, cfg, dry_run=dry_run)
    if private is not None:
        opts += ["-i", str(private)]
    return ["scp"] + opts + ["-P", str(port)]


def wait_for_ssh(vm: dict[str, Any], timeout_sec: int, dry_run: bool = False) -> None:
    host, port, _ = ssh_target(vm)
    if dry_run:
        ui.print_note(f"Would wait for SSH on {host}:{port}")
        return
    deadline = time.monotonic() + timeout_sec
    probe_cmd = ssh_base_cmd(vm, dry_run=dry_run) + ["true"]
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                probe_cmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            if result.returncode == 0:
                return
        except (OSError, subprocess.TimeoutExpired):
            time.sleep(2)
            continue
        time.sleep(2)
    raise VMError(f"Timed out waiting for SSH on {host}:{port}")


def remote_shell_cmd(vm: dict[str, Any], command: str, dry_run: bool = False) -> list[str]:
    return ssh_base_cmd(vm, dry_run=dry_run) + [f"sh -lc {shlex.quote(command)}"]


def remote_sudo_shell_cmd(vm: dict[str, Any], command: str, dry_run: bool = False) -> list[str]:
    return ssh_base_cmd(vm, dry_run=dry_run) + [f"sudo sh -lc {shlex.quote(command)}"]


def wait_for_guest_post_install_ready(
    vm: dict[str, Any],
    dry_run: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> None:
    if dry_run:
        ui.print_note("Would wait for cloud-init to finish")
        ui.print_note("Would wait for package manager activity to settle")
        return

    ui.print_note("Waiting for cloud-init to finish")
    cloud_init_wait = (
        "if command -v cloud-init >/dev/null 2>&1; then "
        "sudo cloud-init status --wait || true; "
        "fi"
    )
    runtime.run(
        remote_shell_cmd(vm, cloud_init_wait, dry_run=dry_run),
        dry_run=dry_run,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        append=True,
    )

    ui.print_note("Waiting for package manager activity to settle")
    package_wait = (
        "while pgrep -x apt >/dev/null || "
        "pgrep -x apt-get >/dev/null || "
        "pgrep -x dpkg >/dev/null; do "
        "sleep 2; "
        "done"
    )
    runtime.run(
        remote_shell_cmd(vm, package_wait, dry_run=dry_run),
        dry_run=dry_run,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        append=True,
    )


def post_install_copy(
    vm: dict[str, Any],
    entry: dict[str, Any],
    dry_run: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> None:
    host, _, user = ssh_target(vm)
    source_raw = str(entry.get("source") or "").strip()
    dest_raw = str(entry.get("dest") or "").strip()
    if not source_raw or not dest_raw:
        raise VMError("copy_from_host entries require source and dest")

    source_sudo = bool(entry.get("source_sudo", False))
    dest_sudo = bool(entry.get("dest_sudo", False))
    dest_mode = str(entry.get("dest_mode") or "").strip()
    source = runtime.expand_host_path(source_raw)
    if not dry_run and not source.exists():
        ui.print_status("warn", f"Skipping missing host path: {source}", ok=False)
        return

    recursive = source_raw.endswith("/") or source.is_dir()
    remote_mkdir = remote_sudo_shell_cmd if dest_sudo else remote_shell_cmd
    remote_chmod = remote_sudo_shell_cmd if dest_sudo else remote_shell_cmd

    if source_sudo and recursive:
        raise VMError("copy_from_host does not support source_sudo for recursive directories")

    if source_sudo:
        temp_source = Path(tempfile.mkdtemp(prefix="vmctl-copy-src-", dir="/tmp")) / source.name
        try:
            runtime.run(
                ["sudo", "cp", "--archive", str(source), str(temp_source)],
                dry_run=dry_run,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                append=True,
            )
            flash.maybe_restore_sudo_owner(temp_source)
            source = temp_source
        except Exception:
            if temp_source.exists():
                temp_source.unlink(missing_ok=True)
            raise

    if recursive:
        runtime.run(
            remote_mkdir(vm, f"mkdir -p {shlex.quote(dest_raw)}", dry_run=dry_run),
            dry_run=dry_run,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            append=True,
        )
        staging_dir = Path(tempfile.mkdtemp(prefix="vmctl-copy-dir-", dir="/tmp"))
        staged_source = staging_dir / source.name
        try:
            shutil.copytree(source, staged_source, symlinks=True, ignore_dangling_symlinks=True)
            for path in staged_source.rglob("*"):
                try:
                    mode = path.lstat().st_mode
                except OSError:
                    continue
                if path.is_symlink() and not path.exists():
                    path.unlink(missing_ok=True)
                    continue
                if stat.S_ISSOCK(mode) or stat.S_ISFIFO(mode) or stat.S_ISCHR(mode) or stat.S_ISBLK(mode):
                    if path.is_dir():
                        shutil.rmtree(path, ignore_errors=True)
                    else:
                        path.unlink(missing_ok=True)
            remote_target = f"{user}@{host}:{dest_raw}"
            runtime.run(
                scp_base_cmd(vm, dry_run=dry_run) + ["-r", f"{staged_source}/.", remote_target],
                dry_run=dry_run,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                append=True,
            )
            if dest_mode:
                runtime.run(
                    remote_chmod(vm, f"chmod -R {shlex.quote(dest_mode)} {shlex.quote(dest_raw)}", dry_run=dry_run),
                    dry_run=dry_run,
                    stdout_log=stdout_log,
                    stderr_log=stderr_log,
                    append=True,
                )
        finally:
            shutil.rmtree(staging_dir, ignore_errors=True)
            if source_sudo and source.exists():
                source.unlink(missing_ok=True)
        return

    dest_parent = str(Path(dest_raw).parent)
    temp_dest = dest_raw
    if dest_sudo:
        temp_dest = f"/tmp/{Path(dest_raw).name}"

    try:
        runtime.run(
            remote_mkdir(vm, f"mkdir -p {shlex.quote(dest_parent if dest_sudo else dest_parent)}", dry_run=dry_run),
            dry_run=dry_run,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            append=True,
        )
        remote_target = f"{user}@{host}:{temp_dest}"
        runtime.run(
            scp_base_cmd(vm, dry_run=dry_run) + [str(source), remote_target],
            dry_run=dry_run,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            append=True,
        )
        if dest_sudo:
            runtime.run(
                remote_sudo_shell_cmd(
                    vm,
                    f"install -D -m {shlex.quote(dest_mode or '600')} {shlex.quote(temp_dest)} {shlex.quote(dest_raw)} && rm -f {shlex.quote(temp_dest)}",
                    dry_run=dry_run,
                ),
                dry_run=dry_run,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                append=True,
            )
        elif dest_mode:
            runtime.run(
                remote_chmod(vm, f"chmod {shlex.quote(dest_mode)} {shlex.quote(dest_raw)}", dry_run=dry_run),
                dry_run=dry_run,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                append=True,
            )
    finally:
        if source_sudo and source.exists():
            source.unlink(missing_ok=True)


def post_install_run(
    vm: dict[str, Any],
    command: str,
    dry_run: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> None:
    runtime.run(
        remote_shell_cmd(vm, command, dry_run=dry_run),
        dry_run=dry_run,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        append=True,
    )
