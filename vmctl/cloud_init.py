"""Cloud-init and unattended installer seed image generation."""
from __future__ import annotations

import json
import shlex
import shutil
from pathlib import Path

from typing import Any

from vmctl import ui, runtime
from vmctl.errors import VMError


def _get_vm_section(vm: dict[str, Any], key: str) -> dict[str, Any] | None:
    config = vm.get(key)
    if config is None:
        return None
    if not isinstance(config, dict):
        raise VMError(f"Invalid {key} config: expected object")
    return config


def cloud_init_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    return _get_vm_section(vm, "cloud_init")


def ssh_provision_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    return _get_vm_section(vm, "ssh_provision")


def ssh_access_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    return ssh_provision_config(vm) or cloud_init_config(vm)


def autoinstall_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    return _get_vm_section(vm, "autoinstall")


def unattended_config(vm: dict[str, Any]) -> dict[str, Any] | None:
    return _get_vm_section(vm, "unattended")


def _vm_artifact_subdir(vm: dict[str, Any], subdir: str) -> Path:
    return runtime.resolve_path(vm["disk"]["path"]).parent / subdir


def cloud_init_artifact_dir(vm: dict[str, Any]) -> Path:
    return _vm_artifact_subdir(vm, "cloud-init")


def autoinstall_artifact_dir(vm: dict[str, Any]) -> Path:
    return _vm_artifact_subdir(vm, "autoinstall")


def unattended_artifact_dir(vm: dict[str, Any]) -> Path:
    return _vm_artifact_subdir(vm, "unattended")


def collect_ssh_authorized_keys(config: dict[str, Any], allow_missing_file: bool = False, ssh_public_key: Path | None = None) -> list[str]:
    keys: list[str] = []
    inline_keys = config.get("ssh_authorized_keys") or []
    for key in inline_keys:
        value = str(key).strip()
        if value:
            keys.append(value)
    keys_file = str(config.get("ssh_authorized_keys_file") or "").strip()
    if keys_file:
        path = runtime.expand_host_path(keys_file)
        if not path.is_file():
            if allow_missing_file:
                return keys
            raise VMError(f"SSH authorized keys file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                keys.append(value)
    if ssh_public_key is not None:
        if not ssh_public_key.is_file():
            if allow_missing_file:
                return keys
            raise VMError(f"SSH authorized keys file not found: {ssh_public_key}")
        for line in ssh_public_key.read_text(encoding="utf-8").splitlines():
            value = line.strip()
            if value and not value.startswith("#"):
                keys.append(value)
    return keys


def _authorized_keys_for_vm(vm: dict[str, Any], dry_run: bool = False) -> list[str]:
    cfg = ssh_access_config(vm)
    if cfg is None:
        return []
    ssh_public_key = None
    if not cfg.get("ssh_authorized_keys") and not cfg.get("ssh_authorized_keys_file"):
        from vmctl import ssh

        ssh_public_key = ssh.resolve_ssh_public_key(vm, cfg, dry_run=dry_run)
    return collect_ssh_authorized_keys(cfg, allow_missing_file=dry_run, ssh_public_key=ssh_public_key)


def render_cloud_init_payload(config: dict[str, Any], dry_run: bool = False, include_user: bool = True) -> dict[str, Any]:
    payload: dict[str, Any] = {"package_update": True}
    user = str(config.get("user") or "").strip()
    if include_user and user:
        user_entry: dict[str, Any] = {"name": user}
        ssh_keys = collect_ssh_authorized_keys(config, allow_missing_file=dry_run)
        if ssh_keys:
            user_entry["ssh_authorized_keys"] = ssh_keys
        payload["users"] = [user_entry]
    if config.get("packages"):
        payload["packages"] = list(config["packages"])
    if config.get("runcmd"):
        payload["runcmd"] = list(config["runcmd"])
    if config.get("write_files"):
        payload["write_files"] = list(config["write_files"])
    return payload


def render_cloud_init_user_data(vm: dict[str, Any], dry_run: bool = False) -> str:
    config = cloud_init_config(vm)
    if config is None:
        raise VMError("VM profile does not define cloud_init")
    payload = render_cloud_init_payload(config, dry_run=dry_run, include_user=True)
    return "#cloud-config\n" + json.dumps(payload, indent=2) + "\n"


def render_cloud_init_meta_data(vm_name: str, vm: dict[str, Any]) -> str:
    config = cloud_init_config(vm)
    if config is None:
        raise VMError("VM profile does not define cloud_init")
    return json.dumps(
        {"instance-id": config.get("instance_id", vm_name), "local-hostname": config.get("hostname", vm_name)},
        indent=2,
    ) + "\n"


def create_seed_image(artifact_dir: Path, user_data: str, meta_data: str, dry_run: bool = False) -> Path:
    return create_iso_with_files(
        artifact_dir,
        {
            "user-data": user_data,
            "meta-data": meta_data,
        },
        dry_run=dry_run,
        volume_id="cidata",
        prefer_cloud_localds=True,
    )


def create_iso_with_files(
    artifact_dir: Path,
    files: dict[str, str],
    *,
    dry_run: bool = False,
    volume_id: str,
    prefer_cloud_localds: bool = False,
) -> Path:
    user_data_path = artifact_dir / "user-data"
    meta_data_path = artifact_dir / "meta-data"
    seed_path = artifact_dir / "seed.iso"
    runtime.ensure_parent(user_data_path)
    materialized_paths: list[Path] = []
    for relative_path, content in files.items():
        path = artifact_dir / relative_path
        materialized_paths.append(path)
        runtime.ensure_parent(path)
        if not dry_run:
            path.write_text(content, encoding="utf-8")
    if prefer_cloud_localds and set(files) == {"user-data", "meta-data"} and shutil.which("cloud-localds"):
        cmd = ["cloud-localds", str(seed_path), str(user_data_path), str(meta_data_path)]
    elif shutil.which("genisoimage"):
        cmd = ["genisoimage", "-output", str(seed_path), "-volid", volume_id, "-joliet", "-rock"]
        cmd += [str(path) for path in materialized_paths]
    elif shutil.which("xorriso"):
        cmd = ["xorriso", "-as", "mkisofs", "-output", str(seed_path), "-volid", volume_id, "-joliet", "-rock"]
        cmd += [str(path) for path in materialized_paths]
    else:
        raise VMError("Missing seed builder: install cloud-localds, genisoimage, or xorriso")
    runtime.run(cmd, dry_run=dry_run)
    return seed_path


def create_cloud_init_seed(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    if cloud_init_config(vm) is None:
        raise VMError(f"VM '{vm_name}' does not define cloud_init")
    return create_seed_image(
        cloud_init_artifact_dir(vm),
        render_cloud_init_user_data(vm, dry_run=dry_run),
        render_cloud_init_meta_data(vm_name, vm),
        dry_run=dry_run,
    )


def cloud_init_drive_args(seed_path: Path) -> list[str]:
    return ["-drive", f"file={seed_path},format=raw,if=virtio,media=cdrom,readonly=on"]


def render_autoinstall_user_data(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> str:
    config = autoinstall_config(vm)
    if config is None:
        raise VMError("VM profile does not define autoinstall")
    ci = cloud_init_config(vm)
    username = str(config.get("username") or (ci or {}).get("user") or "").strip()
    hostname = str(config.get("hostname") or (ci or {}).get("hostname") or vm_name).strip()
    password_hash = str(config.get("password_hash") or "").strip()
    if not username:
        raise VMError("autoinstall.username is required")
    if not password_hash:
        raise VMError("autoinstall.password_hash is required")
    autoinstall_section: dict[str, Any] = {
        "version": 1,
        "identity": {"hostname": hostname, "password": password_hash, "realname": str(config.get("realname") or username), "username": username},
        "keyboard": {"layout": str(config.get("keyboard_layout") or "us")},
        "locale": str(config.get("locale") or "en_US.UTF-8"),
        "storage": {"layout": {"name": str(config.get("storage_layout") or "direct")}},
    }
    payload: dict[str, Any] = {"autoinstall": autoinstall_section}
    timezone = str(config.get("timezone") or "").strip()
    if timezone:
        autoinstall_section["timezone"] = timezone
    updates = str(config.get("updates") or "").strip()
    if updates:
        if updates not in {"security", "all"}:
            raise VMError("autoinstall.updates must be one of: security, all")
        autoinstall_section["updates"] = updates
    ssh_keys = _authorized_keys_for_vm(vm, dry_run=dry_run)
    autoinstall_section["ssh"] = {
        "install-server": bool(config.get("install_ssh", True)),
        "authorized-keys": ssh_keys,
        "allow-pw": bool(config.get("allow_password", not ssh_keys)),
    }
    if config.get("packages"):
        autoinstall_section["packages"] = list(config["packages"])
    if ci is not None:
        autoinstall_section["user-data"] = render_cloud_init_payload(ci, dry_run=dry_run, include_user=False)
    return "#cloud-config\n" + json.dumps(payload, indent=2) + "\n"


def create_autoinstall_seed(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    if autoinstall_config(vm) is None:
        raise VMError(f"VM '{vm_name}' does not define autoinstall")
    meta_data = json.dumps({"instance-id": f"{vm_name}-autoinstall", "local-hostname": vm_name}, indent=2) + "\n"
    return create_seed_image(
        autoinstall_artifact_dir(vm),
        render_autoinstall_user_data(vm_name, vm, dry_run=dry_run),
        meta_data,
        dry_run=dry_run,
    )


def unattended_type(vm: dict[str, Any]) -> str | None:
    config = unattended_config(vm)
    if config is None:
        return None
    kind = str(config.get("type") or "").strip()
    if not kind:
        raise VMError("unattended.type is required")
    return kind


def installer_requires_cloud_init(vm: dict[str, Any]) -> bool:
    return autoinstall_config(vm) is not None


def installer_requires_unattended(vm: dict[str, Any]) -> bool:
    return autoinstall_config(vm) is not None or unattended_config(vm) is not None


def _unattended_identity(vm_name: str, vm: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    config = unattended_config(vm)
    if config is None:
        raise VMError("VM profile does not define unattended")
    ssh_cfg = ssh_access_config(vm) or {}
    username = str(config.get("username") or ssh_cfg.get("user") or "").strip()
    hostname = str(config.get("hostname") or ssh_cfg.get("hostname") or vm_name).strip()
    password_hash = str(config.get("password_hash") or "").strip()
    if not username:
        raise VMError("unattended.username is required")
    if not password_hash:
        raise VMError("unattended.password_hash is required")
    identity = {
        "username": username,
        "hostname": hostname,
        "realname": str(config.get("realname") or username).strip(),
        "password_hash": password_hash,
    }
    return config, identity


def render_preseed_config(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> str:
    config, identity = _unattended_identity(vm_name, vm)
    ssh_keys = _authorized_keys_for_vm(vm, dry_run=dry_run)
    packages = ["openssh-server", "sudo"]
    for package in config.get("packages", []):
        value = str(package).strip()
        if value and value not in packages:
            packages.append(value)
    lines = [
        "d-i auto-install/cloak_initrd_preseed boolean true",
        "d-i auto-install/enable boolean true",
        "d-i debconf/priority string critical",
        f"d-i netcfg/get_hostname string {identity['hostname']}",
        f"d-i netcfg/get_domain string {str(config.get('domain') or 'local')}",
        f"d-i time/zone string {str(config.get('timezone') or 'UTC')}",
        f"d-i clock-setup/utc boolean {str(config.get('utc', True)).lower()}",
        f"d-i debian-installer/locale string {str(config.get('locale') or 'en_US.UTF-8')}",
        f"d-i keyboard-configuration/xkb-keymap select {str(config.get('keyboard_layout') or 'us')}",
        "d-i partman-auto/method string regular",
        "d-i partman-auto/choose_recipe select atomic",
        "d-i partman-partitioning/confirm_write_new_label boolean true",
        "d-i partman/choose_partition select finish",
        "d-i partman/confirm boolean true",
        "d-i partman/confirm_nooverwrite boolean true",
        "d-i apt-setup/non-free boolean true",
        "d-i apt-setup/contrib boolean true",
        "tasksel tasksel/first multiselect standard, ssh-server",
        f"d-i pkgsel/include string {' '.join(packages)}",
        "popularity-contest popularity-contest/participate boolean false",
        f"d-i passwd/user-fullname string {identity['realname']}",
        f"d-i passwd/username string {identity['username']}",
        f"d-i passwd/user-password-crypted password {identity['password_hash']}",
        "d-i passwd/user-default-groups string audio cdrom video sudo",
        "d-i grub-installer/only_debian boolean true",
        "d-i grub-installer/with_other_os boolean true",
        "d-i finish-install/reboot_in_progress note",
    ]
    if ssh_keys:
        quoted_keys = " ".join(shlex.quote(key) for key in ssh_keys)
        user = identity["username"]
        remote_cmd = (
            f"mkdir -p /home/{user}/.ssh && "
            f"printf '%s\\n' {quoted_keys} > /home/{user}/.ssh/authorized_keys && "
            f"chown -R {user}:{user} /home/{user}/.ssh && "
            f"chmod 700 /home/{user}/.ssh && "
            f"chmod 600 /home/{user}/.ssh/authorized_keys"
        )
        lines.append(
            "d-i preseed/late_command string "
            f"in-target /bin/sh -c {shlex.quote(remote_cmd)}"
        )
    return "\n".join(lines) + "\n"


def render_kickstart_config(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> str:
    config, identity = _unattended_identity(vm_name, vm)
    ssh_keys = _authorized_keys_for_vm(vm, dry_run=dry_run)
    packages = ["@core", "openssh-server", "sudo"]
    for package in config.get("packages", []):
        value = str(package).strip()
        if value and value not in packages:
            packages.append(value)
    lines = [
        "text",
        "cdrom",
        "eula --agreed",
        f"lang {str(config.get('locale') or 'en_US.UTF-8')}",
        f"keyboard {str(config.get('keyboard_layout') or 'us')}",
        f"timezone {str(config.get('timezone') or 'UTC')} --utc",
        f"network --bootproto=dhcp --activate --hostname={identity['hostname']}",
        "firewall --enabled --service=ssh",
        "selinux --enforcing",
        "ignoredisk --only-use=vda",
        "zerombr",
        "clearpart --all --initlabel",
        "autopart --type=lvm",
        "bootloader --timeout=1",
        f"rootpw --lock",
        f"user --name={identity['username']} --groups=wheel --password={identity['password_hash']} --iscrypted --gecos={shlex.quote(identity['realname'])}",
        f"services --enabled=sshd",
        "reboot",
        "",
        "%packages",
    ]
    lines += packages
    lines += ["%end"]
    if ssh_keys:
        user = identity["username"]
        lines += [
            "",
            "%post",
            f"mkdir -p /home/{user}/.ssh",
            "cat > /home/{user}/.ssh/authorized_keys <<'EOF'".format(user=user),
            *ssh_keys,
            "EOF",
            f"chown -R {user}:{user} /home/{user}/.ssh",
            f"chmod 700 /home/{user}/.ssh",
            f"chmod 600 /home/{user}/.ssh/authorized_keys",
            "%end",
        ]
    return "\n".join(lines) + "\n"


def render_unattended_file(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> tuple[str, str, str]:
    kind = unattended_type(vm)
    if kind == "debian-preseed":
        return ("preseed.cfg", "cidata", render_preseed_config(vm_name, vm, dry_run=dry_run))
    if kind == "fedora-kickstart":
        return ("ks.cfg", "OEMDRV", render_kickstart_config(vm_name, vm, dry_run=dry_run))
    raise VMError(f"Unsupported unattended.type: {kind}")


def create_unattended_seed(vm_name: str, vm: dict[str, Any], dry_run: bool = False) -> Path:
    if unattended_config(vm) is None:
        raise VMError(f"VM '{vm_name}' does not define unattended")
    filename, volume_id, content = render_unattended_file(vm_name, vm, dry_run=dry_run)
    return create_iso_with_files(
        unattended_artifact_dir(vm),
        {filename: content},
        dry_run=dry_run,
        volume_id=volume_id,
    )


def unattended_http_filename(vm_name: str, vm: dict[str, Any]) -> str | None:
    kind = unattended_type(vm)
    if kind == "debian-preseed":
        return "preseed.cfg"
    return None


def unattended_kernel_append(
    vm_name: str,
    vm: dict[str, Any],
    headless: bool = False,
    http_url: str | None = None,
) -> str:
    config = unattended_config(vm)
    if config is None:
        raise VMError("VM profile does not define unattended")
    kind = unattended_type(vm)
    serial_args = " console=ttyS0,115200n8" if headless else ""
    if kind == "debian-preseed":
        locale = str(config.get("locale") or "en_US.UTF-8")
        keymap = str(config.get("keyboard_layout") or "us")
        language = str(config.get("language") or locale.split("_", 1)[0] or "en")
        country = str(config.get("country") or (locale.split("_", 1)[1].split(".", 1)[0] if "_" in locale else "US"))
        extra = str(config.get("append_extra") or "").strip()
        suffix = f" {extra}" if extra else ""
        if http_url:
            return (
                "auto priority=critical "
                f"language={language} country={country} locale={locale} "
                f"debian-installer/locale={locale} localechooser/supported-locales={locale} "
                "console-setup/ask_detect=false "
                f"keyboard-configuration/xkb-keymap={keymap} keymap={keymap} "
                f"url={http_url} preseed/url={http_url}{serial_args}{suffix}"
            ).strip()
        return (
            "auto priority=critical "
            f"language={language} country={country} locale={locale} "
            f"debian-installer/locale={locale} localechooser/supported-locales={locale} "
            "console-setup/ask_detect=false "
            f"keyboard-configuration/xkb-keymap={keymap} keymap={keymap} "
            f"file=/preseed.cfg preseed/file=/preseed.cfg{serial_args}{suffix}"
        ).strip()
    if kind == "fedora-kickstart":
        extra = str(config.get("append_extra") or "").strip()
        suffix = f" {extra}" if extra else ""
        return f"inst.ks=hd:LABEL=OEMDRV:/ks.cfg inst.text{serial_args}{suffix}".strip()
    raise VMError(f"Unsupported unattended.type: {kind}")
