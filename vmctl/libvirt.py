"""Render and manage persistent libvirt definitions using existing VM storage."""
from __future__ import annotations

import argparse
import os
import re
import shlex
import shutil
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from vmctl import config, netlab, qemu, runtime, ui, vmstate
from vmctl.errors import VMError


def domain_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", name):
        raise VMError("Invalid libvirt domain name: use letters, digits, underscores, dots and hyphens")
    return name


# The enlightenments of qemu.hyperv_cpu_flags, spelled out for libvirt. Not mode='passthrough':
# that makes the domain unmigratable, so virt-manager refuses a snapshot of the running VM. Not
# reenlightenment: the live snapshot is taken but its revert fails restoring that MSR (-22). Both
# found on windows11-studio, 2026-10-04, where this list runs Windows with WSL2 inside and reverts
# its running snapshots in ~20 s.
HYPERV_FEATURES = ("relaxed", "vapic", "spinlocks", "vpindex", "runtime", "synic", "stimer",
                   "reset", "frequencies", "tlbflush", "ipi")


def host_is_intel() -> bool:
    """evmcs (the enlightened VMCS nested Hyper-V uses) exists on Intel VMX only."""
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as cpuinfo:
            return any(line.startswith("vendor_id") and "GenuineIntel" in line for line in cpuinfo)
    except OSError:
        return False


def hyperv_features(features: ET.Element) -> None:
    hyperv = ET.SubElement(features, "hyperv", mode="custom")
    for name in HYPERV_FEATURES:
        node = ET.SubElement(hyperv, name, state="on")
        if name == "spinlocks":
            node.set("retries", "8191")
        if name == "stimer":
            ET.SubElement(node, "direct", state="on")
    if host_is_intel():
        ET.SubElement(hyperv, "evmcs", state="on")


# The mark of a domain made by `export-libvirt --copy-to-pool`: its disk and firmware are copies in a
# libvirt pool, so vmctl neither guards the profile's disk against it nor unexports it.
METADATA_NS = "https://github.com/manzolo/qemu-iso-lab"
ET.register_namespace("vmctl", METADATA_NS)


def xml_path(path: Path) -> str:
    """Absolute paths resolved (the batch worktree's artifacts is a symlink); a dry run's pool
    placeholder stays as it is."""
    return str(path.resolve()) if path.is_absolute() else str(path)


def render_domain_xml(name: str, vm: dict[str, Any], firmware: tuple[Path, Path] | None = None,
                      disk_path: Path | None = None, copied_from: str | None = None) -> str:
    domain = ET.Element("domain", type="kvm")
    ET.SubElement(domain, "name").text = domain_name(name)
    if copied_from is not None:
        metadata = ET.SubElement(domain, "metadata")
        ET.SubElement(metadata, f"{{{METADATA_NS}}}export", mode="copy", profile=copied_from)
    ET.SubElement(domain, "memory", unit="MiB").text = str(vm["memory_mb"])
    ET.SubElement(domain, "vcpu").text = str(vm["cpus"])
    os = ET.SubElement(domain, "os")
    machine = str(vm.get("machine", "q35"))
    # QEMU's portable aliases are q35 and pc; versioned pc-q35/pc-i440fx
    # names supplied by a profile are passed through unchanged.
    ET.SubElement(os, "type", arch="x86_64", machine=machine).text = "hvm"
    fw = vm["firmware"]
    if fw["type"] == "efi":
        if firmware is not None:
            # qcow2 copies (export_firmware): libvirt takes internal snapshots of a pflash VM only
            # with its variables in qcow2, and wants the loader in the same format.
            ET.SubElement(os, "loader", readonly="yes", type="pflash", format="qcow2").text = xml_path(firmware[0])
            ET.SubElement(os, "nvram", format="qcow2").text = xml_path(firmware[1])
        else:
            code, _, nvram = qemu.resolve_efi_firmware(fw)
            ET.SubElement(os, "loader", readonly="yes", type="pflash").text = str(code.resolve())
            ET.SubElement(os, "nvram").text = str(nvram.resolve())
    ET.SubElement(os, "boot", dev="hd")
    features = ET.SubElement(domain, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")
    if vm.get("vmport") is False:
        ET.SubElement(features, "vmport", state="off")
    if vm.get("hyperv") is True and not vm.get("cpu_model"):
        hyperv_features(features)
    ET.SubElement(domain, "cpu", mode="host-passthrough")
    if vm.get("hyperv") is True and not vm.get("cpu_model"):
        clock = ET.SubElement(domain, "clock", offset="utc")
        ET.SubElement(clock, "timer", name="hypervclock", present="yes")
    shared = qemu.shared_dir_config(vm)
    if shared:
        backing = ET.SubElement(domain, "memoryBacking")
        ET.SubElement(backing, "source", type="memfd")
        ET.SubElement(backing, "access", mode="shared")
    devices = ET.SubElement(domain, "devices")
    disk = vm["disk"]
    fmt = str(disk["format"])
    bus = str(disk.get("interface", "virtio"))
    if bus not in ("virtio", "sata", "ide") or fmt not in ("qcow2", "vhd", "vpc", "raw"):
        raise VMError(f"Unsupported libvirt disk format/interface: {fmt}/{bus}")
    node = ET.SubElement(devices, "disk", type="file", device="disk")
    ET.SubElement(node, "driver", name="qemu", type="vpc" if fmt == "vhd" else fmt)
    ET.SubElement(node, "source", file=xml_path(disk_path or runtime.resolve_path(disk["path"])))
    ET.SubElement(node, "target", dev={"virtio": "vda", "sata": "sda", "ide": "hda"}[bus], bus=bus)
    for spec in qemu.network_specs(vm, "runtime"):
        # slirp NICs land on libvirt's default NAT network; segment NICs on the libvirt network of
        # the same name (created by export when missing, see ensure_segment_networks).
        net = ET.SubElement(devices, "interface", type="network")
        ET.SubElement(net, "source", network="default" if spec["type"] == "user" else str(spec["name"]))
        if not spec["legacy"]:
            ET.SubElement(net, "mac", address=str(spec["mac"]))
        model = str(spec["device"])
        ET.SubElement(net, "model", type="virtio" if model == "virtio-net-pci" else model)
    graphics = ET.SubElement(devices, "graphics", type="spice", autoport="yes")
    ET.SubElement(graphics, "listen", type="none")
    video = ET.SubElement(devices, "video")
    ET.SubElement(video, "model", type="virtio" if vm.get("video", {}).get("default") == "virtio-gl" else "qxl")
    if vm.get("audio"):
        ET.SubElement(devices, "sound", model={"hda": "ich9", "ac97": "ac97"}[qemu.audio_device(vm)])
    if vm.get("usb_tablet"):
        ET.SubElement(devices, "controller", type="usb", model="qemu-xhci")
        ET.SubElement(devices, "input", type="tablet", bus="usb")
    channel = ET.SubElement(devices, "channel", type="unix")
    ET.SubElement(channel, "target", type="virtio", name="org.qemu.guest_agent.0")
    if shared:
        fs = ET.SubElement(devices, "filesystem", type="mount", accessmode="passthrough")
        ET.SubElement(fs, "driver", type="virtiofs")
        ET.SubElement(fs, "source", dir=str(qemu.shared_dir_source(vm).resolve()))
        ET.SubElement(fs, "target", dir=shared["tag"])
    if "windows_config" in vm:
        tpm = ET.SubElement(devices, "tpm", model="tpm-crb")
        ET.SubElement(tpm, "backend", type="emulator", version="2.0")
    ET.indent(domain)
    return ET.tostring(domain, encoding="unicode") + "\n"


FIRMWARE_CODE = "OVMF_CODE.qcow2"
FIRMWARE_VARS = "OVMF_VARS.qcow2"


def export_firmware(vm_name: str, vm: dict[str, Any], dry_run: bool, prefix: str = "") -> tuple[Path, Path] | None:
    """The EFI firmware of an exported VM as qcow2 copies under artifacts/<vm>/libvirt/: with the
    raw vars vmctl uses, virt-manager's snapshots are refused ("internal snapshots of a VM with
    pflash based firmware require QCOW2 nvram format", libvirt 12, 2026-10-04). The variables are
    converted from vmctl's own file at every export and converted back by unexport. A ``prefix``
    names staging copies (--copy-to-pool) that leave the copies of a linked export alone."""
    if vm["firmware"].get("type") != "efi":
        return None
    code, template, nvram = qemu.resolve_efi_firmware(vm["firmware"])
    base = runtime.vm_artifact_base(vm_name) / "libvirt"
    code_copy, vars_copy = base / f"{prefix}{FIRMWARE_CODE}", base / f"{prefix}{FIRMWARE_VARS}"
    if not dry_run:
        base.mkdir(parents=True, exist_ok=True)
    source_vars = nvram if dry_run or nvram.is_file() else template
    runtime.run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(code), str(code_copy)], dry_run=dry_run)
    runtime.run(["qemu-img", "convert", "-f", "raw", "-O", "qcow2", str(source_vars), str(vars_copy)], dry_run=dry_run)
    return code_copy, vars_copy


def import_firmware(vm_name: str, vm: dict[str, Any], exported_nvram: Path | None, dry_run: bool) -> None:
    """Back from libvirt: the qcow2 variables (what the guest changed while there) become vmctl's
    raw file again, and the copies go."""
    if exported_nvram is None or exported_nvram.name != FIRMWARE_VARS or vm["firmware"].get("type") != "efi":
        return
    _, _, nvram = qemu.resolve_efi_firmware(vm["firmware"])
    if exported_nvram.is_file() or dry_run:
        runtime.run(["qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(exported_nvram), str(nvram)], dry_run=dry_run)
    if not dry_run:
        exported_nvram.unlink(missing_ok=True)
        (exported_nvram.parent / FIRMWARE_CODE).unlink(missing_ok=True)


def segment_names(vm: dict[str, Any]) -> list[str]:
    return [str(spec["name"]) for spec in qemu.network_specs(vm, "runtime") if spec["type"] == "segment"]


def ensure_segment_networks(uri: str, vm_name: str, vm: dict[str, Any], dry_run: bool) -> list[Path]:
    """Define + start + autostart the libvirt network of every segment NIC that libvirt does not know yet.

    The network XML comes from the lab topology (bridge name, host address, no DHCP/DNS/NAT); a VM
    attached with ``vmctl lab attach`` resolves the same lab through the router that owns the segment.
    """
    written: list[Path] = []
    names = segment_names(vm)
    if not names:
        return written
    cfg = config.load_config()
    known = [] if dry_run else virsh_output(uri, "net-list", "--all", "--name").splitlines()
    active = [] if dry_run else virsh_output(uri, "net-list", "--name").splitlines()
    for name in names:
        owners = [router for router in netlab.routers(cfg) if netlab.topology(cfg, router)["lan"]["name"] == name]
        if not owners:
            raise VMError(f"Segment '{name}' is not the LAN of any network_lab router profile: cannot render its libvirt network")
        xml = netlab.segment_network_xml(netlab.topology(cfg, owners[0]))
        path = runtime.vm_artifact_base(vm_name) / "libvirt" / f"network-{name}.xml"
        if dry_run:
            print(xml, end="")
            ui.print_note(f"Would write {path}")
        else:
            runtime.ensure_parent(path)
            path.write_text(xml, encoding="utf-8")
        written.append(path)
        if name not in known:
            runtime.run(["virsh", "--connect", uri, "net-define", str(path)], dry_run=dry_run)
        if name not in active:
            runtime.run(["virsh", "--connect", uri, "net-start", name], dry_run=dry_run)
        runtime.run(["virsh", "--connect", uri, "net-autostart", name], dry_run=dry_run)
    return written


def virsh_output(uri: str, *args: str) -> str:
    """Keep queries on runtime.run too, so all virsh access is mockable."""
    with tempfile.TemporaryDirectory(prefix="vmctl-virsh-") as tmp:
        output = Path(tmp) / "stdout"
        runtime.run(["virsh", "--connect", uri, *args], quiet=True, stdout_log=output)
        return output.read_text() if output.exists() else ""


def undefine(uri: str, name: str, dry_run: bool) -> None:
    """--nvram deletes the vars file: preserve guest firmware state in place."""
    # --snapshots-metadata: libvirt refuses to undefine a domain with snapshots; the snapshots
    # themselves stay inside the qcow2 images.
    command = ["virsh", "--connect", uri, "undefine", name, "--nvram", "--snapshots-metadata"]
    if dry_run:
        runtime.run(command, dry_run=True)
        return
    if name in virsh_output(uri, "list", "--name").splitlines():
        raise VMError(f"Libvirt domain '{name}' is running; shut it down first")
    xml = ET.fromstring(virsh_output(uri, "dumpxml", name))
    nvram_text = xml.findtext("os/nvram")
    nvram = Path(nvram_text) if nvram_text else None
    saved = nvram.read_bytes() if nvram is not None and nvram.is_file() else None
    try:
        runtime.run(command)
    finally:
        if nvram is not None and saved is not None:
            nvram.write_bytes(saved)


def domain_is_copy(uri: str, name: str) -> bool:
    """Whether the domain was exported with --copy-to-pool (its metadata says so)."""
    try:
        xml = ET.fromstring(virsh_output(uri, "dumpxml", name) or "<domain/>")
    except ET.ParseError:
        return False
    mark = xml.find(f"metadata/{{{METADATA_NS}}}export")
    return mark is not None and mark.get("mode") == "copy"


def pool_path(uri: str, pool: str, dry_run: bool) -> Path:
    if dry_run:
        return Path(f"<pool {pool}>")
    try:
        xml = ET.fromstring(virsh_output(uri, "pool-dumpxml", pool) or "<pool/>")
    except ET.ParseError:
        xml = ET.Element("pool")
    path = xml.findtext("target/path")
    if not path:
        raise VMError(f"libvirt pool '{pool}' not found on {uri} (virsh --connect {uri} pool-list --all)")
    if xml.get("type") not in ("dir", "fs", "netfs"):
        raise VMError(f"libvirt pool '{pool}' is of type {xml.get('type')}: --copy-to-pool needs a directory pool")
    return Path(path)


def pool_volumes(uri: str, pool: str) -> set[str]:
    """The volume names of a pool (vol-list's first column; domain names carry no spaces)."""
    lines = virsh_output(uri, "vol-list", "--pool", pool).splitlines()
    return {line.split()[0] for line in lines[2:] if line.strip()}


def upload_volume(uri: str, pool: str, volume: str, source: Path, dry_run: bool, mode: str = "0600") -> None:
    """A new volume holding the source's bytes, through libvirtd: the pool directory may be root's,
    the user only needs the connection. The volume is created raw and sized to the file, the
    upload replaces its bytes and the pool refresh probes the real format (qcow2, vpc...). libvirt
    hands QEMU the disk and the variables at start but never the read-only loader, so that one is
    created 0644 (with vol-create-as's 0600 root: "Could not open ... OVMF_CODE: Permission
    denied", debian-server in the default pool, 2026-10-17)."""
    size = 0 if dry_run else source.stat().st_size
    volume_xml = ET.Element("volume")
    ET.SubElement(volume_xml, "name").text = volume
    ET.SubElement(volume_xml, "capacity", unit="bytes").text = str(size)
    ET.SubElement(volume_xml, "allocation", unit="bytes").text = "0"
    target = ET.SubElement(volume_xml, "target")
    ET.SubElement(target, "format", type="raw")
    ET.SubElement(ET.SubElement(target, "permissions"), "mode").text = mode
    definition = source.parent / f".pool-{volume}.xml"
    if not dry_run:
        definition.write_text(ET.tostring(volume_xml, encoding="unicode") + "\n", encoding="utf-8")
    try:
        runtime.run(["virsh", "--connect", uri, "vol-create", pool, str(definition)], dry_run=dry_run)
    finally:
        if not dry_run:
            definition.unlink(missing_ok=True)
    runtime.run(["virsh", "--connect", uri, "vol-upload", "--pool", pool, volume, str(source), "--sparse"], dry_run=dry_run)


# Where an export --copy-to-pool keeps what the guest on the copy expects from vmctl: the SSH key
# pair it was installed with and the record (the guest user). `vmctl clean` skips this directory, as
# it skips the checkpoints, so a copy taken back after a clean still opens over SSH (2026-10-19).
COPIES_DIR = "libvirt-copies"


def copy_keep_dir(vm_name: str, domain: str) -> Path:
    return runtime.vm_artifact_base(vm_name) / COPIES_DIR / domain


def keep_for_copy(vm_name: str, domain: str) -> None:
    keep = copy_keep_dir(vm_name, domain)
    if keep.exists():
        shutil.rmtree(keep)
    keep.mkdir(parents=True)
    keep.chmod(0o700)
    keys = runtime.vm_artifact_base(vm_name) / "ssh"
    if keys.is_dir():
        shutil.copytree(keys, keep / "ssh")
    record = vmstate.state_path(vm_name)
    if record.is_file():
        shutil.copy2(record, keep / "state.json")


def restore_for_copy(vm_name: str, domain: str) -> None:
    """The copy is vmctl's disk again: its SSH keys and guest user come back with it."""
    keep = copy_keep_dir(vm_name, domain)
    if not keep.is_dir():
        return
    if (keep / "ssh").is_dir():
        keys = runtime.vm_artifact_base(vm_name) / "ssh"
        if keys.exists():
            shutil.rmtree(keys)
        shutil.copytree(keep / "ssh", keys)
    if (keep / "state.json").is_file():
        guest = runtime.load_json_file(keep / "state.json").get("guest")
        if isinstance(guest, dict) and guest.get("user"):
            record = vmstate.load(vm_name)
            record["guest"] = guest
            vmstate.save(vm_name, record)
    shutil.rmtree(keep)
    if not any(keep.parent.iterdir()):
        keep.parent.rmdir()


def copy_to_pool(args: argparse.Namespace, vm: dict[str, Any], name: str, disk: Path) -> tuple[Path, tuple[Path, Path] | None]:
    """--copy-to-pool: the disk (an overlay flattened first) and the EFI firmware become volumes of
    the pool, named after the domain; the profile's disk is only read. Returns their paths."""
    uri, pool, dry_run = args.connect, args.pool, args.dry_run
    target = pool_path(uri, pool, dry_run)
    fmt = str(vm["disk"]["format"])
    overlay = fmt == "qcow2" and not dry_run and vmstate.qcow2_has_backing_file(disk)
    suffix = disk.suffix or f".{fmt}"
    volumes = [f"{name}{suffix}"]
    efi = vm["firmware"].get("type") == "efi"
    if efi:
        volumes += [f"{name}-{FIRMWARE_CODE}", f"{name}-{FIRMWARE_VARS}"]
    if not dry_run:
        taken = sorted(set(volumes) & pool_volumes(uri, pool))
        if taken:
            raise VMError(f"Pool '{pool}' already has {', '.join(taken)}: pick another --name, or remove it with "
                          f"virsh --connect {uri} vol-delete --pool {pool} <volume>")
    staging = runtime.vm_artifact_base(args.vm) / "libvirt"
    if not dry_run:
        staging.mkdir(parents=True, exist_ok=True)
    flat = staging / f".pool-{name}.qcow2"
    try:
        source = disk
        if overlay:
            # A cloud-image overlay: the pool gets one self-contained image, not a file pointing
            # back into isos/.
            runtime.run(["qemu-img", "convert", "-O", "qcow2", str(disk), str(flat)])
            source = flat
        upload_volume(uri, pool, volumes[0], source, dry_run)
        firmware = None
        if efi:
            staged = export_firmware(args.vm, vm, dry_run, prefix=f".pool-{name}-")
            assert staged is not None
            upload_volume(uri, pool, volumes[1], staged[0], dry_run, mode="0644")
            upload_volume(uri, pool, volumes[2], staged[1], dry_run)
            firmware = (target / volumes[1], target / volumes[2])
    finally:
        if not dry_run:
            for leftover in staging.glob(f".pool-{name}*"):
                leftover.unlink(missing_ok=True)
    runtime.run(["virsh", "--connect", uri, "pool-refresh", pool], dry_run=dry_run)
    return target / volumes[0], firmware


def export(args: argparse.Namespace, vm: dict[str, Any]) -> int:
    name = domain_name(args.name or args.vm)
    copy = bool(getattr(args, "copy_to_pool", False))
    if copy and args.no_define:
        raise VMError("--copy-to-pool talks to libvirt (the volumes are created through it): drop --no-define")
    if vm.get("extra_disks"):
        raise VMError(f"'{args.vm}' has extra_disks: the libvirt export renders the main disk only")
    disk = runtime.resolve_path(vm["disk"]["path"])
    if not disk.is_file():
        raise VMError(f"Installed disk missing: {disk}. Install the VM with vmctl first")
    destination = runtime.vm_artifact_base(args.vm) / "libvirt" / f"{name}.xml"
    replace_existing = False
    if not args.no_define and not args.dry_run:
        runtime.require_command("virsh")
        existing = virsh_output(args.connect, "list", "--all", "--name").splitlines()
        if name in existing:
            if not args.replace:
                raise VMError(f"Libvirt domain '{name}' already exists; use --replace")
            replace_existing = True
    if copy:
        disk_copy, firmware = copy_to_pool(args, vm, name, disk)
        if not args.dry_run:
            keep_for_copy(args.vm, name)
        xml = render_domain_xml(name, vm, firmware, disk_path=disk_copy, copied_from=args.vm)
    else:
        xml = render_domain_xml(name, vm, export_firmware(args.vm, vm, args.dry_run))
    if args.dry_run:
        print(xml, end="")
        ui.print_note(f"Would write {destination}")
        if args.replace and not args.no_define:
            undefine(args.connect, name, True)
    else:
        runtime.ensure_parent(destination)
        destination.write_text(xml, encoding="utf-8")
    if not args.no_define:
        ensure_segment_networks(args.connect, args.vm, vm, args.dry_run)
        if replace_existing:
            undefine(args.connect, name, False)
        runtime.run(["virsh", "--connect", args.connect, "define", str(destination)], dry_run=args.dry_run)
        if args.autostart:
            runtime.run(["virsh", "--connect", args.connect, "autostart", name], dry_run=args.dry_run)
    ui.print_kv("XML", str(destination))
    ui.print_note(f"Use virsh --connect {args.connect} domifaddr {name} for the DHCP address; SSH hostfwd is not exported.")
    if copy:
        ui.print_note(f"An independent copy in pool '{args.pool}': vmctl's disk of {args.vm} is untouched and stays usable. "
                      f"The domain is libvirt's alone; to delete it: virsh --connect {args.connect} undefine {name} --nvram, "
                      f"then virsh vol-delete --pool {args.pool} for each of its volumes (never --remove-all-storage).")
    else:
        ui.print_note(f"Use virsh/virt-manager after export, or vmctl unexport-libvirt {args.vm} --name {name} --connect {args.connect} before returning to vmctl.")
    return 0


def warn_foreign_owner(vm: dict[str, Any]) -> None:
    """libvirt chowns a domain's images to libvirt-qemu while it runs and normally gives them back
    at shutdown, but a snapshot revert can lose the remembered owner: the disk of windows-11 stayed
    libvirt-qemu:kvm 0644 after an export, a revert and an unexport (2026-10-04), and the next
    `vmctl start` died on "Permission denied". vmctl cannot chown without root and does not assume sudo exists: it names the command."""
    paths = [runtime.resolve_path(vm["disk"]["path"])]
    if vm["firmware"].get("type") == "efi":
        paths.append(qemu.resolve_efi_firmware(vm["firmware"])[2])
    foreign = [path for path in paths if path.exists() and path.stat().st_uid != os.getuid()]
    if foreign:
        ui.print_status("warn", "libvirt left these files to another owner; vmctl cannot open them until, as root "
                        "(sudo, doas or su -c):", ok=False)
        print(f"    chown {os.getuid()}:{os.getgid()} " + " ".join(shlex.quote(str(path)) for path in foreign))


def copy_volumes(xml: ET.Element, name: str) -> list[Path]:
    """The pool volumes copy_to_pool made for this domain, read from its XML: the disk, loader and
    vars named after the domain (`<name>.<ext>`, `<name>-OVMF_*`). Anything else the domain was
    given later in virt-manager (an ISO, a second disk) is not ours to delete."""
    paths = [node.get("file") for node in xml.findall("devices/disk[@device='disk']/source")]
    paths += [xml.findtext("os/loader"), xml.findtext("os/nvram")]
    found: list[Path] = []
    for text in paths:
        if not text:
            continue
        path = Path(text)
        if (path.stem == name or path.name.startswith(f"{name}-")) and path not in found:
            found.append(path)
    return found


def take_back_copy(args: argparse.Namespace, vm: dict[str, Any], name: str) -> int:
    """unexport-libvirt of a --copy-to-pool domain: the copy comes back to vmctl, like a linked
    export does. Its disk (and EFI vars) are downloaded through libvirtd (the pool may be root's)
    over the profile's disk, and only once they are in place the domain is undefined and the
    volumes it was made with deleted; never --remove-all-storage. A vmctl disk with data diverged
    from the copy since the export, so taking the copy over it needs --replace."""
    uri = args.connect
    if name in virsh_output(uri, "list", "--name").splitlines():
        raise VMError(f"Libvirt domain '{name}' is running; shut it down first")
    xml = ET.fromstring(virsh_output(uri, "dumpxml", name))
    volumes = copy_volumes(xml, name)
    disks = [path for path in volumes if path.name not in (f"{name}-{FIRMWARE_CODE}", f"{name}-{FIRMWARE_VARS}")]
    if len(disks) != 1:
        raise VMError(f"Libvirt domain '{name}' does not have the one disk volume --copy-to-pool made "
                      f"({', '.join(map(str, disks)) or 'none'}): bring it back by hand")
    nvram_text = xml.findtext("os/nvram")
    nvram_volume = Path(nvram_text) if nvram_text and Path(nvram_text) in volumes else None
    disk = runtime.resolve_path(vm["disk"]["path"])
    if vmstate.disk_facts(vm)["has_data"] and not getattr(args, "replace", False):
        raise VMError(f"vmctl has its own disk of {args.vm} with data ({disk}): it and the libvirt copy went separate ways "
                      f"after the export. --replace takes the copy over it (vmctl checkpoint create {args.vm} first to keep it)")
    ui.print_note(f"'{name}' is an independent copy (export-libvirt --copy-to-pool): it comes back as vmctl's disk of "
                  f"{args.vm} ({disk}), then the domain is undefined and its volumes deleted:"
                  + "".join(f"\n    {path}" for path in volumes))
    if not getattr(args, "yes", False) and not runtime.confirm_default_no(f"Bring the libvirt copy '{name}' back into vmctl?"):
        raise VMError("Not confirmed (pass --yes to skip the question in scripts)")
    runtime.ensure_parent(disk)
    staged_disk = disk.parent / f".libvirt-{name}{disk.suffix}.part"
    staged_vars = disk.parent / f".libvirt-{name}-vars.qcow2.part"
    raw_vars: Path | None = None
    try:
        runtime.run(["virsh", "--connect", uri, "vol-download", str(disks[0]), str(staged_disk), "--sparse"])
        if nvram_volume is not None and vm["firmware"].get("type") == "efi":
            nvram = qemu.resolve_efi_firmware(vm["firmware"])[2]
            runtime.ensure_parent(nvram)
            raw_vars = nvram.parent / f".libvirt-{name}-vars.raw.part"
            runtime.run(["virsh", "--connect", uri, "vol-download", str(nvram_volume), str(staged_vars)])
            runtime.run(["qemu-img", "convert", "-f", "qcow2", "-O", "raw", str(staged_vars), str(raw_vars)])
            os.replace(raw_vars, nvram)
        os.replace(staged_disk, disk)
    finally:
        for leftover in (staged_disk, staged_vars, raw_vars):
            if leftover is not None:
                leftover.unlink(missing_ok=True)
    vmstate.record_origin(args.vm, "libvirt", f"copy {name}")
    restore_for_copy(args.vm, name)
    # --keep-nvram: the vars are one of the volumes deleted below, like the disk.
    runtime.run(["virsh", "--connect", uri, "undefine", name, "--keep-nvram", "--snapshots-metadata"])
    for path in volumes:
        runtime.run(["virsh", "--connect", uri, "vol-delete", str(path)])
    ui.print_status("ok", f"Libvirt copy '{name}' is vmctl's disk of {args.vm} again; domain and volumes removed")
    return 0


def unexport(args: argparse.Namespace, vm: dict[str, Any]) -> int:
    name = domain_name(args.name or args.vm)
    exported_nvram: Path | None = None
    if not args.dry_run:
        runtime.require_command("virsh")
        if domain_is_copy(args.connect, name):
            return take_back_copy(args, vm, name)
        nvram_text = ET.fromstring(virsh_output(args.connect, "dumpxml", name) or "<domain/>").findtext("os/nvram")
        exported_nvram = Path(nvram_text) if nvram_text else None
    undefine(args.connect, name, args.dry_run)
    import_firmware(args.vm, vm, exported_nvram, args.dry_run)
    if not args.dry_run:
        warn_foreign_owner(vm)
    ui.print_note("Would remove libvirt definition; disk and firmware vars preserved." if args.dry_run else
                  "Libvirt definition removed; disk and firmware vars preserved in artifacts.")
    return 0
