"""Render and manage persistent libvirt definitions using existing VM storage."""
from __future__ import annotations

import argparse
import os
import re
import shlex
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from vmctl import config, netlab, qemu, runtime, ui
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


def render_domain_xml(name: str, vm: dict[str, Any], firmware: tuple[Path, Path] | None = None) -> str:
    domain = ET.Element("domain", type="kvm")
    ET.SubElement(domain, "name").text = domain_name(name)
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
            ET.SubElement(os, "loader", readonly="yes", type="pflash", format="qcow2").text = str(firmware[0].resolve())
            ET.SubElement(os, "nvram", format="qcow2").text = str(firmware[1].resolve())
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
    ET.SubElement(node, "source", file=str(runtime.resolve_path(disk["path"]).resolve()))
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


def export_firmware(vm_name: str, vm: dict[str, Any], dry_run: bool) -> tuple[Path, Path] | None:
    """The EFI firmware of an exported VM as qcow2 copies under artifacts/<vm>/libvirt/: with the
    raw vars vmctl uses, virt-manager's snapshots are refused ("internal snapshots of a VM with
    pflash based firmware require QCOW2 nvram format", libvirt 12, 2026-10-04). The variables are
    converted from vmctl's own file at every export and converted back by unexport."""
    if vm["firmware"].get("type") != "efi":
        return None
    code, template, nvram = qemu.resolve_efi_firmware(vm["firmware"])
    base = runtime.vm_artifact_base(vm_name) / "libvirt"
    code_copy, vars_copy = base / FIRMWARE_CODE, base / FIRMWARE_VARS
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


def export(args: argparse.Namespace, vm: dict[str, Any]) -> int:
    name = domain_name(args.name or args.vm)
    if vm.get("extra_disks"):
        raise VMError(f"'{args.vm}' has extra_disks: the libvirt export renders the main disk only")
    disk = runtime.resolve_path(vm["disk"]["path"])
    if not disk.is_file():
        raise VMError(f"Installed disk missing: {disk}. Install the VM with vmctl first")
    firmware = export_firmware(args.vm, vm, args.dry_run)
    xml = render_domain_xml(name, vm, firmware)
    destination = runtime.vm_artifact_base(args.vm) / "libvirt" / f"{name}.xml"
    replace_existing = False
    if not args.no_define and not args.dry_run:
        runtime.require_command("virsh")
        existing = virsh_output(args.connect, "list", "--all", "--name").splitlines()
        if name in existing:
            if not args.replace:
                raise VMError(f"Libvirt domain '{name}' already exists; use --replace")
            replace_existing = True
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


def unexport(args: argparse.Namespace, vm: dict[str, Any]) -> int:
    name = domain_name(args.name or args.vm)
    exported_nvram: Path | None = None
    if not args.dry_run:
        runtime.require_command("virsh")
        nvram_text = ET.fromstring(virsh_output(args.connect, "dumpxml", name) or "<domain/>").findtext("os/nvram")
        exported_nvram = Path(nvram_text) if nvram_text else None
    undefine(args.connect, name, args.dry_run)
    import_firmware(args.vm, vm, exported_nvram, args.dry_run)
    if not args.dry_run:
        warn_foreign_owner(vm)
    ui.print_note("Would remove libvirt definition; disk and firmware vars preserved." if args.dry_run else
                  "Libvirt definition removed; disk and firmware vars preserved in artifacts.")
    return 0
