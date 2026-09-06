"""Render and manage persistent libvirt definitions using existing VM storage."""
from __future__ import annotations

import argparse
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from vmctl import qemu, runtime, ui
from vmctl.errors import VMError


def domain_name(name: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}", name):
        raise VMError("Invalid libvirt domain name: use letters, digits, underscores, dots and hyphens")
    return name


def render_domain_xml(name: str, vm: dict[str, Any]) -> str:
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
        code, _, nvram = qemu.resolve_efi_firmware(fw)
        ET.SubElement(os, "loader", readonly="yes", type="pflash").text = str(code.resolve())
        ET.SubElement(os, "nvram").text = str(nvram.resolve())
    ET.SubElement(os, "boot", dev="hd")
    features = ET.SubElement(domain, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(features, "apic")
    ET.SubElement(domain, "cpu", mode="host-passthrough")
    shared = qemu.shared_dir_config(vm)
    if shared:
        backing = ET.SubElement(domain, "memoryBacking")
        ET.SubElement(backing, "source", type="memfd")
        ET.SubElement(backing, "access", mode="shared")
    devices = ET.SubElement(domain, "devices")
    disk = vm["disk"]
    fmt = str(disk["format"])
    bus = str(disk.get("interface", "virtio"))
    if bus not in ("virtio", "sata") or fmt not in ("qcow2", "vhd", "vpc", "raw"):
        raise VMError(f"Unsupported libvirt disk format/interface: {fmt}/{bus}")
    node = ET.SubElement(devices, "disk", type="file", device="disk")
    ET.SubElement(node, "driver", name="qemu", type="vpc" if fmt == "vhd" else fmt)
    ET.SubElement(node, "source", file=str(runtime.resolve_path(disk["path"]).resolve()))
    ET.SubElement(node, "target", dev="vda" if bus == "virtio" else "sda", bus=bus)
    net = ET.SubElement(devices, "interface", type="network")
    ET.SubElement(net, "source", network="default")
    model = str(vm.get("network_device", "virtio-net-pci"))
    ET.SubElement(net, "model", type="virtio" if model == "virtio-net-pci" else model)
    graphics = ET.SubElement(devices, "graphics", type="spice", autoport="yes")
    ET.SubElement(graphics, "listen", type="none")
    video = ET.SubElement(devices, "video")
    ET.SubElement(video, "model", type="virtio" if vm.get("video", {}).get("default") == "virtio-gl" else "qxl")
    if vm.get("audio"):
        ET.SubElement(devices, "sound", model="ich9")
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


def virsh_output(uri: str, *args: str) -> str:
    """Keep queries on runtime.run too, so all virsh access is mockable."""
    with tempfile.TemporaryDirectory(prefix="vmctl-virsh-") as tmp:
        output = Path(tmp) / "stdout"
        runtime.run(["virsh", "--connect", uri, *args], quiet=True, stdout_log=output)
        return output.read_text() if output.exists() else ""


def undefine(uri: str, name: str, dry_run: bool) -> None:
    """--nvram deletes the vars file: preserve guest firmware state in place."""
    command = ["virsh", "--connect", uri, "undefine", name, "--nvram"]
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
    disk = runtime.resolve_path(vm["disk"]["path"])
    if not disk.is_file():
        raise VMError(f"Installed disk missing: {disk}. Install the VM with vmctl first")
    xml = render_domain_xml(name, vm)
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
        if replace_existing:
            undefine(args.connect, name, False)
        runtime.run(["virsh", "--connect", args.connect, "define", str(destination)], dry_run=args.dry_run)
        if args.autostart:
            runtime.run(["virsh", "--connect", args.connect, "autostart", name], dry_run=args.dry_run)
    ui.print_kv("XML", str(destination))
    ui.print_note(f"Use virsh --connect {args.connect} domifaddr {name} for the DHCP address; SSH hostfwd is not exported.")
    ui.print_note(f"Use virsh/virt-manager after export, or vmctl unexport-libvirt {args.vm} --name {name} --connect {args.connect} before returning to vmctl.")
    return 0


def unexport(args: argparse.Namespace) -> int:
    if not args.dry_run:
        runtime.require_command("virsh")
    undefine(args.connect, domain_name(args.name or args.vm), args.dry_run)
    ui.print_note("Would remove libvirt definition; disk and firmware vars preserved." if args.dry_run else
                  "Libvirt definition removed; disk and firmware vars preserved in artifacts.")
    return 0
