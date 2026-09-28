#!/usr/bin/env python3
"""Generate the Ubuntu flavour history: one profile per (flavour, LTS release) on the release bases.

    tools/gen_ubuntu_flavors.py xubuntu            # what it would add (default: every release the flavour existed on)
    tools/gen_ubuntu_flavors.py xubuntu --write    # add the missing ones to vms/profiles/ubuntu-flavors-history.json
    tools/gen_ubuntu_flavors.py xubuntu kubuntu --releases 22.04 26.04 --write

Every profile extends the release base of vms/profiles/ubuntu-lts.json (`ubuntu-<release>-base`: the
medium and the machine of that era) and carries only the flavour: the metapackage, the display
manager's autologin for that era, the desktop check and a port. They are born `experimental` (the
full matrix sets them aside; `vmctl check-vms <name>` runs one) and without `meta.verified`: a live
PASS promotes a profile by hand. Profiles already in the output file are never rewritten, so a fix
made after a live run survives a re-run of this tool; ports are taken from --first-port upwards,
skipping every port the catalog already uses.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vmctl import config, qemu  # noqa: E402

OUTPUT = ROOT / "vms" / "profiles" / "ubuntu-flavors-history.json"
RELEASES = ("8.04", "10.04", "12.04", "14.04", "16.04", "18.04", "20.04", "22.04", "26.04")
PRESEED_OLD = ("8.04", "10.04", "12.04")      # alternate CD, upstart, no systemd
PRESEED_NEW = ("14.04", "16.04", "18.04")     # server CD; systemd from 16.04
AUTOINSTALL = ("20.04", "22.04", "26.04")     # live-server, cloud-init

# Per flavour: the metapackage (also the d-i task on the alternate CDs), the session process the
# pre-systemd check waits for, the display manager per era (gdm on the 8.04/10.04 media, then what
# the flavour shipped), the session names the display managers know, and the first LTS it existed on.
FLAVOURS: dict[str, dict[str, Any]] = {
    "xubuntu": {"label": "Xubuntu", "desktop": "Xfce", "package": "xubuntu-desktop", "process": "xfce4-session",
                "since": "8.04", "dm": {"8.04": "gdm", "10.04": "gdm", "12.04": "lightdm", "14.04": "lightdm",
                                        "16.04": "lightdm", "18.04": "lightdm", "20.04": "lightdm", "22.04": "lightdm", "26.04": "lightdm"},
                "lightdm_session": "xubuntu", "screensaver": "xfce"},
}

GETTY = {
    "8.04": "printf 'start on runlevel 2\\nstart on runlevel 3\\nstart on runlevel 4\\nstart on runlevel 5\\nstop on runlevel 0\\nstop on runlevel 1\\nstop on runlevel 6\\nrespawn\\nexec /sbin/getty -L 115200 ttyS0 vt102\\n' > /etc/event.d/ttyS0",
    "upstart": "printf 'start on stopped rc RUNLEVEL=[2345]\\nstop on runlevel [!2345]\\nrespawn\\nexec /sbin/getty -L 115200 ttyS0 vt102\\n' > /etc/init/ttyS0.conf",
    "systemd": "systemctl enable serial-getty@ttyS0.service",
}
NO_RELEASE_PROMPT = "sed -i 's/^Prompt=.*/Prompt=never/' /etc/update-manager/release-upgrades"
NO_APT_PERIODIC = ("printf '%s\\n' 'APT::Periodic::Enable \"0\";' 'APT::Periodic::Update-Package-Lists \"0\";' "
                   "'APT::Periodic::Download-Upgradeable-Packages \"0\";' > /etc/apt/apt.conf.d/10periodic")


def autologin_command(dm: str, release: str, flavour: dict[str, Any]) -> str:
    """The late command (d-i eras) that makes the display manager log the lab user in."""
    if dm == "gdm":
        target = "/etc/gdm/gdm.conf-custom" if release == "8.04" else "/etc/gdm/custom.conf"
        return f"printf '[daemon]\\nAutomaticLoginEnable=true\\nAutomaticLogin={{{{user}}}}\\nTimedLoginEnable=false\\n' > {target}"
    if dm == "lightdm":
        session = flavour["lightdm_session"]
        return (f"printf '[SeatDefaults]\\nautologin-user={{{{user}}}}\\nautologin-user-timeout=0\\nuser-session={session}\\n' "
                "> /etc/lightdm/lightdm.conf")
    if dm == "kdm":
        rc = "/etc/kde3/kdm/kdmrc" if release == "8.04" else "/etc/kde4/kdm/kdmrc"
        return (f"sed -i -e 's/^#*AutoLoginEnable=.*/AutoLoginEnable=true/' -e 's/^#*AutoLoginUser=.*/AutoLoginUser={{{{user}}}}/' "
                f"-e 's/^#*AutoLoginAgain=.*/AutoLoginAgain=true/' {rc}")
    raise SystemExit(f"no autologin recipe for {dm} on {release}")


def screensaver_commands(kind: str, release: str) -> list[str]:
    """Keep the screen unlocked on the d-i eras (the report photographs the desktop after minutes)."""
    if kind == "xfce":
        if release in PRESEED_OLD:  # xscreensaver
            return ["printf 'mode: off\\nlock: False\\n' > /home/{{user}}/.xscreensaver && chown {{user}}: /home/{{user}}/.xscreensaver || true"]
        return ["printf '[Desktop Entry]\\nType=Application\\nHidden=true\\n' > /etc/xdg/autostart/light-locker.desktop || true"]  # light-locker
    return []


def dropin(dm: str, flavour: dict[str, Any]) -> dict[str, str]:
    """The cloud-init write_files entry of the autoinstall eras (what the 24.04 flavours use)."""
    if dm == "lightdm":
        return {"path": "/etc/lightdm/lightdm.conf.d/vmctl-autologin.conf", "permissions": "0644",
                "content": f"[Seat:*]\nautologin-user={{{{user}}}}\nautologin-user-timeout=0\nautologin-session={flavour['lightdm_session']}\n"}
    if dm == "sddm":
        return {"path": "/etc/sddm.conf.d/vmctl-autologin.conf", "permissions": "0644",
                "content": f"[Autologin]\nUser={{{{user}}}}\nSession={flavour['sddm_session']}\n"}
    if dm == "gdm":
        return {"path": "/etc/gdm3/custom.conf", "permissions": "0644",
                "content": "[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin={{user}}\n"}
    raise SystemExit(f"no drop-in for {dm}")


def profile(key: str, flavour: dict[str, Any], release: str, base_names: dict[str, str], port: int) -> dict[str, Any]:
    name = f"{key}-{release}"
    hostname = f"{key}-{release.replace('.', '')}"
    package, dm = flavour["package"], flavour["dm"][release]
    label = base_names[f"ubuntu-{release}"].replace("Ubuntu ", f"{flavour['label']} ", 1).replace("Desktop (", f"({flavour['desktop']}, ", 1)
    out: dict[str, Any] = {
        "name": label, "extends": f"ubuntu-{release}-base",
        "meta": {"slug": key, "status": "experimental", "groups": ["ubuntu-flavors", f"{key}-releases"]},
        "notes": (f"{flavour['label']} on the Ubuntu {release} medium of the release series: the same unattended recipe as "
                  f"ubuntu-{release} with the {package} metapackage and the {dm} autologin of that era. Generated by "
                  "tools/gen_ubuntu_flavors.py; experimental until a live run passes (vmctl check-vms " + name + ")."),
    }
    if release in AUTOINSTALL:
        install = ({"late_commands": [f"curtin in-target --target=/target -- sh -c 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y {package} spice-vdagent qemu-guest-agent'"]}
                   if release == "20.04" else {"packages": [package, "spice-vdagent", "qemu-guest-agent"]})
        # The server ISO plus a flavour metapackage also lands gdm3, which wins the display-manager
        # alternative: xubuntu-20.04/22.04/26.04 sat on the GDM greeter (2026-09-28). The install
        # therefore pins the flavour's own display manager, and the first boot writes the autologin
        # for it and for gdm3 alike (what the 24.04 flavours do).
        binary = {"lightdm": "/usr/sbin/lightdm", "sddm": "/usr/bin/sddm", "gdm": "/usr/sbin/gdm3"}[dm]
        service = "gdm3" if dm == "gdm" else dm
        # `systemctl enable` cannot pick a display manager: the units are static and the choice is
        # the display-manager.service alias the packages create (xubuntu-22.04 came up with no
        # display manager at all after a disable + enable --force, 2026-09-28: verified live that
        # the alias alone brings LightDM and the Xfce autologin up).
        pin = (f"curtin in-target --target=/target -- sh -c 'echo {binary} > /etc/X11/default-display-manager; "
               f"ln -sf /lib/systemd/system/{service}.service /etc/systemd/system/display-manager.service'")
        install["late_commands"] = install.get("late_commands", []) + [pin]
        files = [dropin(dm, flavour)]
        if dm != "gdm":
            files.append(dropin("gdm", flavour))
        out["autoinstall"] = {"hostname": hostname, **install}
        out["cloud_init"] = {"hostname": hostname, "write_files": files,
                             "runcmd": [f"ln -sf /lib/systemd/system/{service}.service /etc/systemd/system/display-manager.service",
                                        NO_RELEASE_PROMPT,  # or update-notifier's upgrade dialog sits on the desktop (xubuntu-20.04/22.04 clips, 2026-09-28)
                                        "groupadd -f autologin; groupadd -f nopasswdlogin; usermod -aG autologin,nopasswdlogin {{user}} || true",
                                        "systemctl enable --now serial-getty@ttyS0.service"]}
        out["ssh_provision"] = {"hostname": hostname, "ssh_host_port": port, "post_install_run": [
            f"~/bin/verify-desktop --user \"{{{{user}}}}\" --service display-manager.service --package-manager dpkg --package {package}"]}
        return out
    getty = GETTY["8.04"] if release == "8.04" else GETTY["upstart"] if release in ("10.04", "12.04", "14.04") else GETTY["systemd"]
    late = [autologin_command(dm, release, flavour), getty, NO_RELEASE_PROMPT, *screensaver_commands(flavour["screensaver"], release), NO_APT_PERIODIC]
    # pkgsel/include, never a tasksel task: on the Ubuntu alternate CDs tasksel ignores the flavour
    # tasks (xubuntu-8.04/10.04/12.04 came up on a text login with no desktop at all, 2026-09-28),
    # while apt pulls the metapackage from the mirror on every era; universe holds the flavours.
    preseed: dict[str, Any] = {"packages": [package, "openssh-server", "sudo"]}
    if release in PRESEED_OLD:
        preseed["extra"] = ["d-i apt-setup/universe boolean true"]
    out["preseed_config"] = {**preseed, "hostname": hostname, "late_commands": late}
    ssh: dict[str, Any] = {"hostname": hostname, "ssh_host_port": port}
    if release in ("16.04", "18.04"):
        ssh["post_install_run"] = [f"~/bin/verify-desktop --user \"{{{{user}}}}\" --service display-manager.service --package-manager dpkg --package {package}"]
        ssh["copy_from_host"] = [{"source": "vms/profile-files/common/bin/verify-desktop", "dest": "/home/{{user}}/bin/verify-desktop", "dest_mode": "755"}]
    else:
        process = flavour["process"]
        ssh["post_install_run"] = [f"for i in $(seq 1 90); do pgrep -u {{{{user}}}} -x '{process}' >/dev/null && break; sleep 2; done; "
                                   f"pgrep -u {{{{user}}}} -x '{process}' >/dev/null && dpkg-query -W -f='${{Status}}\\n' {package} | grep -q 'install ok installed'"]
    out["ssh_provision"] = ssh
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("flavours", nargs="+", choices=sorted(FLAVOURS))
    parser.add_argument("--releases", nargs="+", choices=RELEASES, help="only these releases (default: every one the flavour existed on)")
    parser.add_argument("--first-port", type=int, default=2282)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    tracked = config.load_tracked(ROOT / "vms" / "profiles")
    used_ports = {int(((vm.get("ssh_provision") or vm.get("cloud_init") or {}).get("ssh_host_port")) or 0) for vm in tracked.values()}
    used_ports |= {port for vm in tracked.values() for port in qemu.host_ports(vm)}  # every hostfwd, not only SSH
    base_names = {name: str(vm["name"]) for name, vm in tracked.items() if name.startswith("ubuntu-") and name[7:] in RELEASES}
    document = json.loads(OUTPUT.read_text(encoding="utf-8")) if OUTPUT.exists() else {"vms": {}}
    port = args.first_port
    added = []
    for key in args.flavours:
        flavour = FLAVOURS[key]
        releases = [r for r in (args.releases or RELEASES) if RELEASES.index(r) >= RELEASES.index(flavour["since"])]
        for release in releases:
            name = f"{key}-{release}"
            if name in tracked or name in document["vms"]:
                print(f"{name:22s} exists, kept")
                continue
            while port in used_ports:
                port += 1
            document["vms"][name] = profile(key, flavour, release, base_names, port)
            used_ports.add(port)
            added.append(name)
            print(f"{name:22s} port {port}  extends ubuntu-{release}-base  dm {flavour['dm'][release]}")
    if args.write and added:
        OUTPUT.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{len(added)} profile(s) written to {OUTPUT.relative_to(ROOT)}; now: tools/bump_profile.py --init")
    elif added:
        print(f"{len(added)} profile(s) would be added (--write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
