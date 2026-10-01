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

# Per flavour: the metapackage, the session process the pre-systemd check waits for, the display
# manager and the session name per release (read from each release's own packages on 2026-09-28:
# the .desktop files under /usr/share/xsessions and wayland-sessions, the metapackage dependencies),
# the releases it was an official flavour on, and an optional disk size. A value that is the same on
# every release may be a plain string instead of a {release: value} table.
L = "lightdm"
FLAVOURS: dict[str, dict[str, Any]] = {
    "xubuntu": {"label": "Xubuntu", "desktop": "Xfce", "package": "xubuntu-desktop", "process": "xfce4-session",
                "releases": RELEASES, "dm": {"8.04": "gdm", "10.04": "gdm", "12.04": L, "14.04": L, "16.04": L, "18.04": L,
                                             "20.04": L, "22.04": L, "26.04": L},
                "session": "xubuntu", "screensaver": "xfce"},
    # KDE 3 on 8.04, KDE 4 with kdm on 10.04, LightDM on 12.04/14.04 (kde-plasma), SDDM from 15.04.
    "kubuntu": {"label": "Kubuntu", "desktop": "KDE", "package": "kubuntu-desktop", "process": "ksmserver",
                "releases": RELEASES, "dm": {"8.04": "kdm", "10.04": "kdm", "12.04": "kdm", "14.04": L, "16.04": "sddm", "18.04": "sddm",
                                             "20.04": "sddm", "22.04": "sddm", "26.04": "sddm"},
                "session": {"8.04": "kde", "10.04": "kde", "12.04": "kde-plasma", "14.04": "kde-plasma", "default": "plasma"},
                "screensaver": ""},
    # LXDE on LightDM until 18.04, LXQt on SDDM from 18.10; the session file is Lubuntu.desktop throughout.
    "lubuntu": {"label": "Lubuntu", "desktop": "LXDE", "package": "lubuntu-desktop", "process": "lxsession",
                "releases": ("12.04", "14.04", "16.04", "18.04", "20.04", "22.04", "26.04"),
                "dm": {"12.04": L, "14.04": L, "16.04": L, "18.04": L, "20.04": "sddm", "22.04": "sddm", "26.04": "sddm"},
                "desktop_by_release": {"20.04": "LXQt", "22.04": "LXQt", "26.04": "LXQt"},
                "session": "Lubuntu", "screensaver": "xfce"},
    "ubuntu-mate": {"label": "Ubuntu MATE", "desktop": "MATE", "package": "ubuntu-mate-desktop", "process": "mate-session",
                    "releases": ("16.04", "18.04", "20.04", "22.04", "26.04"), "dm": L, "session": "mate", "screensaver": ""},
    # Budgie 10.10 is Wayland-only and Ubuntu Budgie moved to SDDM for 26.04 (ubuntu-budgie-desktop depends on sddm).
    "ubuntu-budgie": {"label": "Ubuntu Budgie", "desktop": "Budgie", "package": "ubuntu-budgie-desktop", "process": "budgie-session",
                      "releases": ("18.04", "20.04", "22.04", "26.04"), "dm": {"18.04": L, "20.04": L, "22.04": L, "26.04": "sddm"},
                      "session": "budgie-desktop", "screensaver": ""},
    "ubuntu-unity": {"label": "Ubuntu Unity", "desktop": "Unity", "package": "ubuntu-unity-desktop", "process": "unity-panel-ser",
                     "releases": ("26.04",), "dm": L, "session": "unity", "screensaver": "", "disk": "40G"},
    "ubuntu-cinnamon": {"label": "Ubuntu Cinnamon", "desktop": "Cinnamon", "package": "ubuntucinnamon-desktop", "process": "cinnamon-sessio",
                        "releases": ("26.04",), "dm": L, "session": "cinnamon", "screensaver": "", "disk": "40G"},
    # The original Edubuntu (ubuntu-desktop plus the ubuntu-edu-* sets, until 14.04) and the revived one (23.04+).
    "edubuntu": {"label": "Edubuntu", "desktop": "GNOME", "package": "edubuntu-desktop", "process": "gnome-session",
                 "releases": ("8.04", "10.04", "12.04", "14.04", "26.04"),
                 "dm": {"8.04": "gdm", "10.04": "gdm", "12.04": L, "14.04": L, "26.04": "gdm"},
                 "desktop_by_release": {"12.04": "Unity", "14.04": "Unity"},
                 "session": {"8.04": "gnome", "10.04": "gnome", "12.04": "ubuntu", "14.04": "ubuntu", "default": "ubuntu"},
                 "screensaver": "", "disk": "60G"},
}


def per_release(value: Any, release: str) -> Any:
    """A flavour field that is either one value or a {release: value} table (with an optional default)."""
    if isinstance(value, dict):
        return value.get(release, value.get("default"))
    return value


def session_of(flavour: dict[str, Any], release: str) -> str:
    return str(per_release(flavour["session"], release))


GETTY = {
    "8.04": "printf 'start on runlevel 2\\nstart on runlevel 3\\nstart on runlevel 4\\nstart on runlevel 5\\nstop on runlevel 0\\nstop on runlevel 1\\nstop on runlevel 6\\nrespawn\\nexec /sbin/getty -L 115200 ttyS0 vt102\\n' > /etc/event.d/ttyS0",
    "upstart": "printf 'start on stopped rc RUNLEVEL=[2345]\\nstop on runlevel [!2345]\\nrespawn\\nexec /sbin/getty -L 115200 ttyS0 vt102\\n' > /etc/init/ttyS0.conf",
    "systemd": "systemctl enable serial-getty@ttyS0.service",
}
DM_BINARY = {"lightdm": "/usr/sbin/lightdm", "sddm": "/usr/bin/sddm", "gdm": "/usr/sbin/gdm3"}
DM_SERVICE = {"lightdm": "lightdm", "sddm": "sddm", "gdm": "gdm3"}
NO_RELEASE_PROMPT = "sed -i 's/^Prompt=.*/Prompt=never/' /etc/update-manager/release-upgrades"
NO_APT_PERIODIC = ("printf '%s\\n' 'APT::Periodic::Enable \"0\";' 'APT::Periodic::Update-Package-Lists \"0\";' "
                   "'APT::Periodic::Download-Upgradeable-Packages \"0\";' > /etc/apt/apt.conf.d/10periodic")


def autologin_command(dm: str, release: str, flavour: dict[str, Any]) -> str:
    """The late command (d-i eras) that makes the display manager log the lab user in."""
    if dm == "gdm":
        target = "/etc/gdm/gdm.conf-custom" if release == "8.04" else "/etc/gdm/custom.conf"
        # GDM 2.30 (10.04) with no saved session for the user logged into the "xterm" session (a
        # bare xterm, verified with [debug] on 2026-09-28): ~/.dmrc plus gdm's own cache name it.
        session = session_of(flavour, release)
        return (f"printf '[daemon]\\nAutomaticLoginEnable=true\\nAutomaticLogin={{{{user}}}}\\nTimedLoginEnable=false\\n' > {target}; "
                f"printf '[Desktop]\\nSession={session}\\nLanguage=en_US.UTF-8\\n' > /home/{{{{user}}}}/.dmrc; chown {{{{user}}}}: /home/{{{{user}}}}/.dmrc; "
                f"install -d -o {{{{user}}}} -g {{{{user}}}} /var/cache/gdm/{{{{user}}}}; cp /home/{{{{user}}}}/.dmrc /var/cache/gdm/{{{{user}}}}/dmrc; chown {{{{user}}}}: /var/cache/gdm/{{{{user}}}}/dmrc")
    if dm == "lightdm":
        session = session_of(flavour, release)
        return (f"printf '[SeatDefaults]\\nautologin-user={{{{user}}}}\\nautologin-user-timeout=0\\nuser-session={session}\\n' "
                "> /etc/lightdm/lightdm.conf")
    if dm == "kdm":
        rc = "/etc/kde3/kdm/kdmrc" if release == "8.04" else "/etc/kde4/kdm/kdmrc"
        # kdm reads the session from ~/.dmrc like gdm; without one it may start a failsafe session.
        return (f"sed -i -e 's/^#*AutoLoginEnable=.*/AutoLoginEnable=true/' -e 's/^#*AutoLoginUser=.*/AutoLoginUser={{{{user}}}}/' "
                f"-e 's/^#*AutoLoginAgain=.*/AutoLoginAgain=true/' {rc}; "
                f"grep -q '^AutoLoginEnable=true' {rc} || printf '[X-:0-Core]\\nAutoLoginEnable=true\\nAutoLoginUser={{{{user}}}}\\n' >> {rc}; "
                f"printf '[Desktop]\\nSession={session_of(flavour, release)}\\n' > /home/{{{{user}}}}/.dmrc; chown {{{{user}}}}: /home/{{{{user}}}}/.dmrc")
    if dm == "sddm":
        return f"printf '[Autologin]\\nUser={{{{user}}}}\\nSession={session_of(flavour, release)}\\n' > /etc/sddm.conf"
    raise SystemExit(f"no autologin recipe for {dm} on {release}")


def screensaver_commands(kind: str, release: str) -> list[str]:  # kind "" = nothing to disable
    """Keep the screen unlocked on the d-i eras (the report photographs the desktop after minutes)."""
    if kind == "xfce":
        if release in PRESEED_OLD:  # xscreensaver
            return ["printf 'mode: off\\nlock: False\\n' > /home/{{user}}/.xscreensaver && chown {{user}}: /home/{{user}}/.xscreensaver || true"]
        return ["printf '[Desktop Entry]\\nType=Application\\nHidden=true\\n' > /etc/xdg/autostart/light-locker.desktop || true"]  # light-locker
    return []


def dropin(dm: str, flavour: dict[str, Any], release: str) -> dict[str, str]:
    """The cloud-init write_files entry of the autoinstall eras (what the 24.04 flavours use)."""
    if dm == "lightdm":
        return {"path": "/etc/lightdm/lightdm.conf.d/vmctl-autologin.conf", "permissions": "0644",
                "content": f"[Seat:*]\nautologin-user={{{{user}}}}\nautologin-user-timeout=0\nautologin-session={session_of(flavour, release)}\n"}
    if dm == "sddm":
        return {"path": "/etc/sddm.conf.d/vmctl-autologin.conf", "permissions": "0644",
                "content": f"[Autologin]\nUser={{{{user}}}}\nSession={session_of(flavour, release)}\n"}
    if dm == "gdm":
        return {"path": "/etc/gdm3/custom.conf", "permissions": "0644",
                "content": "[daemon]\nAutomaticLoginEnable=true\nAutomaticLogin={{user}}\n"}
    raise SystemExit(f"no drop-in for {dm}")


def profile(key: str, flavour: dict[str, Any], release: str, base_names: dict[str, str], port: int) -> dict[str, Any]:
    name = f"{key}-{release}"
    hostname = f"{key}-{release.replace('.', '')}"
    package, dm = flavour["package"], per_release(flavour["dm"], release)
    desktop = (flavour.get("desktop_by_release") or {}).get(release, flavour["desktop"])
    label = base_names[f"ubuntu-{release}"].replace("Ubuntu ", f"{flavour['label']} ", 1).replace("Desktop (", f"({desktop}, ", 1)
    out: dict[str, Any] = {
        "name": label, "extends": f"ubuntu-{release}-base",
        "meta": {"slug": key, "status": "experimental", "groups": ["ubuntu-flavors", f"{key}-releases"]},
        "notes": (f"{flavour['label']} on the Ubuntu {release} medium of the release series: the same unattended recipe as "
                  f"ubuntu-{release} with the {package} metapackage and the {dm} autologin of that era. Generated by "
                  "tools/gen_ubuntu_flavors.py; experimental until a live run passes (vmctl check-vms " + name + ")."),
    }
    if flavour.get("disk"):
        out["disk"] = {"size": flavour["disk"]}
    if release in AUTOINSTALL:
        install = ({"late_commands": [f"curtin in-target --target=/target -- sh -c 'apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y {package} spice-vdagent qemu-guest-agent'"]}
                   if release == "20.04" else {"packages": [package, "spice-vdagent", "qemu-guest-agent"]})
        # The server ISO plus a flavour metapackage also lands gdm3, which wins the display-manager
        # alternative: xubuntu-20.04/22.04/26.04 sat on the GDM greeter (2026-09-28). The install
        # therefore pins the flavour's own display manager, and the first boot writes the autologin
        # for it and for gdm3 alike (what the 24.04 flavours do).
        binary, service = DM_BINARY[dm], DM_SERVICE[dm]
        # `systemctl enable` cannot pick a display manager: the units are static and the choice is
        # the display-manager.service alias the packages create (xubuntu-22.04 came up with no
        # display manager at all after a disable + enable --force, 2026-09-28: verified live that
        # the alias alone brings LightDM and the Xfce autologin up).
        pin = (f"curtin in-target --target=/target -- sh -c 'echo {binary} > /etc/X11/default-display-manager; "
               f"ln -sf /lib/systemd/system/{service}.service /etc/systemd/system/display-manager.service'")
        install["late_commands"] = install.get("late_commands", []) + [pin]
        files = [dropin(dm, flavour, release)]
        if dm != "gdm":
            files.append(dropin("gdm", flavour, release))
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
    if release in ("16.04", "18.04"):
        # The same pin as the autoinstall eras: the server CD plus a metapackage may land a second
        # display manager, and the alias decides which one starts.
        late.append(f"echo {DM_BINARY[dm]} > /etc/X11/default-display-manager; "
                    f"ln -sf /lib/systemd/system/{DM_SERVICE[dm]}.service /etc/systemd/system/display-manager.service")
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
        # x-session-manager too: a session started through the alternative has that comm, cut to
        # 15 characters (debian-7's GNOME 3.4, 2026-09-28).
        process = f"{per_release(flavour['process'], release)}|x-session-manag(er)?"
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
        releases = [r for r in (args.releases or RELEASES) if r in flavour["releases"]]
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
            print(f"{name:22s} port {port}  extends ubuntu-{release}-base  dm {per_release(flavour['dm'], release)}  session {session_of(flavour, release)}")
    if args.write and added:
        OUTPUT.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{len(added)} profile(s) written to {OUTPUT.relative_to(ROOT)}; now: tools/bump_profile.py --init")
    elif added:
        print(f"{len(added)} profile(s) would be added (--write)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
