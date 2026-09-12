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

from vmctl import cloud_init, flash, qemu, runtime, ui
from vmctl.errors import VMError


SSH_KEY_TYPES = ("ed25519", "rsa")


def ssh_key_type(vm: dict[str, Any]) -> str:
    """``ssh_provision.key_type``: ``ed25519`` (default) or ``rsa`` for guests whose sshd predates
    ed25519 (OpenSSH < 6.5, i.e. Ubuntu 12.04 and older); see also ``ssh_options``."""
    cfg = vm.get("ssh_provision") if isinstance(vm.get("ssh_provision"), dict) else None
    key_type = str((cfg or {}).get("key_type") or "ed25519")
    if key_type not in SSH_KEY_TYPES:
        raise VMError(f"Unsupported ssh_provision.key_type '{key_type}'. Choices: {', '.join(SSH_KEY_TYPES)}")
    return key_type


def generated_ssh_key_path(vm: dict[str, Any]) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / "ssh" / f"id_{ssh_key_type(vm)}"


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
    key_type = ssh_key_type(vm)
    bits = ["-b", "3072"] if key_type == "rsa" else []
    runtime.run(["ssh-keygen", "-q", "-t", key_type, *bits, "-N", "", "-C", f"vmctl {comment}", "-f", str(private)])
    return private


def key_needs_passphrase(path: Path) -> bool:
    """True when *path* is an OpenSSH private key that cannot be loaded without a passphrase.

    vmctl only ever runs ssh in BatchMode: such a key would make every probe fail silently and
    ``wait_for_ssh`` time out after an hour on a guest whose sshd is perfectly fine.
    """
    if shutil.which("ssh-keygen") is None:
        return False
    result = subprocess.run(["ssh-keygen", "-y", "-P", "", "-f", str(path)], capture_output=True, text=True, check=False)
    return result.returncode != 0 and "passphrase" in (result.stderr + result.stdout).lower()


def resolve_ssh_private_key(vm: dict[str, Any], cfg: dict[str, Any], dry_run: bool = False) -> Path | None:
    configured = _configured_ssh_key(cfg)
    if configured is not None:
        if not configured.is_file():
            if dry_run:
                return None
            raise VMError(f"SSH private key not found: {configured}")
        if not dry_run and key_needs_passphrase(configured):
            raise VMError(
                f"SSH private key {configured} is passphrase-protected: vmctl runs ssh in BatchMode and could never log in. "
                "Point ssh_key at a key without passphrase, or remove ssh_key to let vmctl generate one per VM."
            )
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
    # ``ssh_options``: extra ``-o`` settings for legacy guests, e.g. ``HostKeyAlgorithms=+ssh-rsa`` and
    # ``PubkeyAcceptedAlgorithms=+ssh-rsa`` for an sshd that only signs with SHA-1 (OpenSSH < 7.2).
    for option in cfg.get("ssh_options") or []:
        text = str(option).strip()
        if not text or "=" not in text:
            raise VMError(f"Invalid ssh_options entry {option!r}: expected 'Keyword=value'")
        opts += ["-o", text]
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


def wait_for_ssh(vm: dict[str, Any], timeout_sec: int, dry_run: bool = False, probe_command: str = "true") -> None:
    """Poll SSH until *probe_command* succeeds (``exit 0`` for guests whose login shell is cmd.exe)."""
    host, port, _ = ssh_target(vm)
    if dry_run:
        ui.print_note(f"Would wait for SSH on {host}:{port}")
        return
    deadline = time.monotonic() + timeout_sec
    probe_cmd = ssh_base_cmd(vm, dry_run=dry_run) + [probe_command]
    while time.monotonic() < deadline:
        try:
            result = subprocess.run(
                probe_cmd,
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                # A closed port fails instantly; the timeout only bites on an sshd that is up but
                # slow to answer (a legacy guest doing a reverse lookup took 5 s, verified live).
                timeout=15,
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


def ensure_passwordless_sudo(
    vm: dict[str, Any],
    dry_run: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> None:
    cfg = cloud_init.ssh_access_config(vm)
    if cfg is None:
        return
    password = str(cfg.get("sudo_password") or "")
    if not password:
        return
    if "\n" in password or "\r" in password:
        raise VMError("ssh_provision.sudo_password cannot contain newlines")

    _, _, user = ssh_target(vm)
    sudoers_path = f"/etc/sudoers.d/vmctl-{user}"
    sudoers_rule = f"{user} ALL=(ALL) NOPASSWD: ALL"
    install_rule = (
        f"printf '%s\\n' {shlex.quote(sudoers_rule)} > {shlex.quote(sudoers_path)} "
        f"&& chmod 0440 {shlex.quote(sudoers_path)}"
    )
    ui.print_note("Configuring passwordless sudo for SSH provisioning")
    runtime.run(
        ssh_base_cmd(vm, dry_run=dry_run)
        + [f"sudo -S -p '' sh -lc {shlex.quote(install_rule)}"],
        dry_run=dry_run,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        append=True,
        stdin_text=None if dry_run else password + "\n",
    )


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
    # ``apt`` by command line, not by process name: on Ubuntu <= 12.04 the daily cron script is
    # ``/bin/sh /etc/cron.daily/apt``, whose comm is "apt" and which sleeps up to 30 minutes before
    # running (verified live: the wait sat on it for the whole random delay).
    package_wait = (
        "while pgrep -f '^(/usr/bin/)?apt( |$)' >/dev/null || "
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
        # A re-run must be able to refresh a directory that is already there: git pack files and
        # other read-only content would make scp fail with "Permission denied" on the second copy.
        dest_q = shlex.quote(dest_raw)
        runtime.run(
            remote_mkdir(vm, f"mkdir -p {dest_q} && chmod -R u+w {dest_q}", dry_run=dry_run),
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


def reboot_guest(
    vm: dict[str, Any],
    dry_run: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> None:
    """Ask the guest to reboot over SSH, tolerating the connection it drops on the way down."""
    command = ssh_base_cmd(vm, dry_run=dry_run) + ["sudo systemctl reboot"]
    ui.print_command(command)
    if dry_run:
        return
    result = subprocess.run(command, capture_output=True, text=True)
    for path, text in ((stdout_log, result.stdout), (stderr_log, result.stderr)):
        if path is not None and text:
            runtime.ensure_parent(path)
            with path.open("a", encoding="utf-8") as stream:
                stream.write(text)
    # A closed connection is the expected outcome; anything else is worth seeing.
    if result.returncode not in (0, 255):
        raise VMError(f"Reboot request failed with exit status {result.returncode}: {result.stderr.strip()}")


def post_install_run_raw(
    vm: dict[str, Any],
    command: str,
    dry_run: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> None:
    """Run *command* through the guest's own login shell (cmd.exe on Windows), no ``sh -lc`` wrapper."""
    runtime.run(
        ssh_base_cmd(vm, dry_run=dry_run) + [command],
        dry_run=dry_run,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        append=True,
    )


def post_install_copy_raw(
    vm: dict[str, Any],
    entry: dict[str, Any],
    dry_run: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> None:
    """Plain ``scp -r source user@host:dest`` for guests without a POSIX shell (Windows OpenSSH).

    *dest* is a guest path in scp form (``C:/Users/lab/Desktop/tools``); sudo, dest_mode and
    the read-only fix-ups of :func:`post_install_copy` do not apply.
    """
    host, _, user = ssh_target(vm)
    source_raw = str(entry.get("source") or "").strip()
    dest_raw = str(entry.get("dest") or "").strip()
    if not source_raw or not dest_raw:
        raise VMError("copy_from_host entries require source and dest")
    for unsupported in ("source_sudo", "dest_sudo", "dest_mode"):
        if entry.get(unsupported):
            raise VMError(f"copy_from_host.{unsupported} is not supported for Windows guests")
    source = runtime.expand_host_path(source_raw)
    if not dry_run and not source.exists():
        ui.print_status("warn", f"Skipping missing host path: {source}", ok=False)
        return
    runtime.run(
        scp_base_cmd(vm, dry_run=dry_run) + ["-r", str(source), f"{user}@{host}:{dest_raw}"],
        dry_run=dry_run,
        stdout_log=stdout_log,
        stderr_log=stderr_log,
        append=True,
    )


def shared_dir_mountpoint(vm: dict[str, Any]) -> str:
    cfg = qemu.shared_dir_config(vm)
    return f"/mnt/{cfg['tag'] if cfg else 'shared'}"


def shared_dir_system_script(vm: dict[str, Any]) -> str:
    """Root part: fstab entry (systemd automount when available) and an immediate mount."""
    cfg = qemu.shared_dir_config(vm)
    assert cfg is not None
    tag, mnt = shlex.quote(cfg["tag"]), shlex.quote(shared_dir_mountpoint(vm))
    return (
        f"tag={tag}; mnt={mnt}; mkdir -p \"$mnt\"; "
        "if command -v systemctl >/dev/null 2>&1; then opts=nofail,x-systemd.automount,x-systemd.idle-timeout=60; else opts=nofail; fi; "
        "if ! grep -q \"^$tag[[:space:]]\" /etc/fstab; then "
        "printf '%s\\t%s\\tvirtiofs\\t%s\\t0\\t0\\n' \"$tag\" \"$mnt\" \"$opts\" >> /etc/fstab; fi; "
        "command -v systemctl >/dev/null 2>&1 && systemctl daemon-reload; "
        "mountpoint -q \"$mnt\" || mount -t virtiofs \"$tag\" \"$mnt\" || echo \"[vmctl] virtiofs mount of $tag deferred to next boot\""
    )


def shared_dir_user_script(vm: dict[str, Any]) -> str:
    """User part: ``~/<tag>`` and a link on the desktop (xdg DESKTOP dir, Desktop or Scrivania)."""
    cfg = qemu.shared_dir_config(vm)
    assert cfg is not None
    tag, mnt = shlex.quote(cfg["tag"]), shlex.quote(shared_dir_mountpoint(vm))
    return (
        f"tag={tag}; mnt={mnt}; ln -sfn \"$mnt\" \"$HOME/$tag\"; "
        "for d in \"$(xdg-user-dir DESKTOP 2>/dev/null)\" \"$HOME/Desktop\" \"$HOME/Scrivania\"; do "
        "[ -n \"$d\" ] && [ \"$d\" != \"$HOME\" ] && [ -d \"$d\" ] && ln -sfn \"$mnt\" \"$d/$tag\"; done; true"
    )


def provision_shared_dir(
    vm: dict[str, Any],
    dry_run: bool = False,
    stdout_log: Path | None = None,
    stderr_log: Path | None = None,
) -> None:
    """Make the virtiofs share of a ``shared_dir`` profile usable in a Linux guest (kvm-lab style):
    ``/mnt/<tag>`` in fstab with automount, mounted now, linked as ``~/<tag>`` and on the desktop."""
    if qemu.shared_dir_config(vm) is None:
        return
    ui.print_note(f"Mounting the virtiofs share at {shared_dir_mountpoint(vm)} and linking it on the desktop")
    runtime.run(remote_sudo_shell_cmd(vm, shared_dir_system_script(vm), dry_run=dry_run),
                dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log, append=True)
    runtime.run(remote_shell_cmd(vm, shared_dir_user_script(vm), dry_run=dry_run),
                dry_run=dry_run, stdout_log=stdout_log, stderr_log=stderr_log, append=True)
