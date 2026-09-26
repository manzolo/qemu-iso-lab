"""Host prerequisite helpers: OS detection, install hints, `vmctl setup --install`, interactive prompt."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from vmctl import qemu, runtime, state, ui
from vmctl.errors import VMError

# What `vmctl setup --install NAME` installs: every name `vmctl setup` checks, plus the firmware,
# mapped to the package that brings it on each package manager. Textual is not a distro package:
# it goes into the repository's .venv-tui, where bin/vmtui looks for it first.
TOOL_PACKAGES: dict[str, dict[str, str]] = {
    "qemu-system-x86_64": {"apt": "qemu-system-x86", "pacman": "qemu-desktop"},
    "qemu-img": {"apt": "qemu-utils", "pacman": "qemu-img"},
    "python3": {"apt": "python3", "pacman": "python"},
    "ovmf": {"apt": "ovmf", "pacman": "edk2-ovmf"},
    "ssh": {"apt": "openssh-client", "pacman": "openssh"},
    "scp": {"apt": "openssh-client", "pacman": "openssh"},
    "ssh-keygen": {"apt": "openssh-client", "pacman": "openssh"},
    "dialog": {"apt": "dialog", "pacman": "dialog"},
    "fzf": {"apt": "fzf", "pacman": "fzf"},
    "remote-viewer": {"apt": "virt-viewer", "pacman": "virt-viewer"},
    "virsh": {"apt": "libvirt-clients", "pacman": "libvirt"},
    "swtpm": {"apt": "swtpm", "pacman": "swtpm"},
    "sgdisk": {"apt": "gdisk", "pacman": "gdisk"},
    "7z": {"apt": "p7zip-full", "pacman": "p7zip"},
    "xorriso": {"apt": "xorriso", "pacman": "xorriso"},
    "cloud-localds": {"apt": "cloud-image-utils", "pacman": "cloud-image-utils"},
    "virtiofsd": {"apt": "virtiofsd", "pacman": "virtiofsd"},
    "growisofs": {"apt": "dvd+rw-tools", "pacman": "dvd+rw-tools"},
    "ddrescue": {"apt": "gddrescue", "pacman": "ddrescue"},
    "ddrescuelog": {"apt": "gddrescue", "pacman": "ddrescue"},
    "partclone.extfs": {"apt": "partclone", "pacman": "partclone"},
    "partclone.ntfs": {"apt": "partclone", "pacman": "partclone"},
    "partclone.fat": {"apt": "partclone", "pacman": "partclone"},
    "partclone.exfat": {"apt": "partclone", "pacman": "partclone"},
    "sfdisk": {"apt": "fdisk", "pacman": "util-linux"},
    "ntfsresize": {"apt": "ntfs-3g", "pacman": "ntfs-3g"},
}
TEXTUAL = "textual"
# The upstream static virtiofsd (musl, no dependencies), for a distribution without a usable
# package: Ubuntu 22.04 has none, only QEMU's C daemon, which needs root. The zip is the one
# attached to the v1.14.0 release notes on gitlab.com/virtio-fs/virtiofsd; its SHA-256 was
# measured on download (2026-09-26): the project publishes no checksum.
VIRTIOFSD_RELEASE = {
    "version": "1.14.0",
    "url": "https://gitlab.com/-/project/21523468/uploads/f505704014ae7a816e515f2a05a93d8b/virtiofsd-v1.14.0.zip",
    "sha256": "2e4fe9571f492b00baa34bc4e708e950039c5da05b830b31a8d179cb6ac8978e",
    "member": "target/x86_64-unknown-linux-musl/release/virtiofsd",
}


# How `vmctl setup` groups what it checks: one line per group when everything is there, and one
# line per missing tool (purpose + package) underneath. Every name of REQUIRED_COMMANDS and
# OPTIONAL_COMMANDS belongs to exactly one group (a test fails otherwise).
SETUP_GROUPS: list[tuple[str, list[str]]] = [
    ("Required", ["qemu-system-x86_64", "qemu-img", "python3"]),
    ("Dashboard", ["textual", "fzf", "dialog"]),
    ("SSH", ["ssh", "scp", "ssh-keygen"]),
    ("Installers", ["xorriso", "cloud-localds", "7z", "growisofs", "virtiofsd"]),
    ("Viewer, libvirt", ["remote-viewer", "virsh", "swtpm"]),
    ("Disk import, flash", ["sgdisk", "sfdisk", "ntfsresize", "ddrescue", "ddrescuelog",
                            "partclone.extfs", "partclone.ntfs", "partclone.fat", "partclone.exfat"]),
]


def compact_names(names: list[str]) -> str:
    """``a · b · partclone.{extfs,ntfs}``: one family of tools shares its prefix."""
    parts: list[str] = []
    families: dict[str, list[str]] = {}
    for name in names:
        prefix, dot, suffix = name.partition(".")
        if dot:
            if prefix not in families:
                families[prefix] = []
                parts.append(prefix + ".")
            families[prefix].append(suffix)
        else:
            parts.append(name)
    return " · ".join(
        f"{part}{{{','.join(families[part[:-1]])}}}" if part.endswith(".") and len(families[part[:-1]]) > 1
        else f"{part}{families[part[:-1]][0]}" if part.endswith(".") else part
        for part in parts)


def tool_package(name: str) -> str:
    if name == TEXTUAL:
        return ".venv-tui, no sudo"
    return TOOL_PACKAGES[name][package_manager() or "apt"] + " package"


def textual_location() -> str:
    python = textual_python()
    return ".venv-tui" if python == str(textual_venv() / "bin/python") else str(python)


def kvm_status() -> tuple[bool, str]:
    """/dev/kvm usable by this user: without it every guest runs under TCG, many times slower."""
    device = Path("/dev/kvm")
    if not device.exists():
        return False, "/dev/kvm missing: guests run without acceleration (enable virtualization in the firmware, load kvm_intel/kvm_amd)"
    if not os.access(device, os.R_OK | os.W_OK):
        return False, "/dev/kvm is not writable by this user: add yourself to the kvm group and log in again"
    return True, "/dev/kvm"


def read_os_release() -> dict[str, str]:
    os_release = Path("/etc/os-release")
    if not os_release.is_file():
        return {}

    data: dict[str, str] = {}
    for line in os_release.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, value = line.split("=", 1)
        data[key] = value.strip().strip('"')
    return data


def host_install_hints() -> list[str]:
    commands = host_install_commands()
    if commands is not None:
        return [runtime.shell_join(cmd) for cmd in commands]
    return [
        "Install QEMU, Python 3, make, OVMF/edk2 firmware, OpenSSH client tools, and libvirt/virsh with your distro package manager.",
        "Optional: Textual for the vmtui dashboard (make install textual), dialog/fzf for the classic TUI menus, virt-viewer for vmctl attach, virtiofsd for shared_dir profiles.",
        "Optional: cloud-localds/genisoimage/xorriso for seed ISOs, 7z (p7zip) for bootstrap-windows, growisofs (dvd+rw-tools) + python bcrypt for bootstrap-pfsense, swtpm for Windows on libvirt, sgdisk (gdisk) for flash/import GPT repair.",
        "Optional: GNU ddrescue (gddrescue on Debian/Ubuntu), partclone, and sfdisk for allocated-block disk imports.",
    ]


def package_manager() -> str | None:
    """'apt' or 'pacman' from /etc/os-release, None on a distribution this file has no packages for."""
    os_release = read_os_release()
    distro_tokens = {
        token
        for key in ("ID", "ID_LIKE")
        for token in os_release.get(key, "").replace(",", " ").split()
        if token
    }
    if {"arch", "cachyos", "manjaro"} & distro_tokens:
        return "pacman"
    if {"debian", "ubuntu"} & distro_tokens:
        return "apt"
    return None


def host_install_commands() -> list[list[str]] | None:
    manager = package_manager()
    if manager == "pacman":
        return [[
            "sudo",
            "pacman",
            "-S",
            "qemu-desktop",
            "qemu-base",
            "edk2-ovmf",
            "python",
            "openssh",
            "libvirt",
            "dialog",
            "make",
            "fzf",
            "cloud-image-utils",
            "xorriso",
            "virtiofsd",
            "virt-viewer",
            "p7zip",
            "dvd+rw-tools",
            "python-bcrypt",
            "swtpm",
            "gdisk",
            "ddrescue",
            "partclone",
            "util-linux",
        ]]
    if manager == "apt":
        return [
            ["sudo", "apt", "update"],
            [
                "sudo",
                "apt",
                "install",
                "-y",
                "qemu-system-x86",
                "qemu-utils",
                "ovmf",
                "python3",
                "openssh-client",
                "libvirt-clients",
                "libvirt-daemon-system",
                "make",
                "dialog",
                "fzf",
                "cloud-image-utils",
                "xorriso",
                "virtiofsd",
                "virt-viewer",
                "p7zip-full",
                "dvd+rw-tools",
                "python3-bcrypt",
                "swtpm",
                "gdisk",
                "gddrescue",
                "partclone",
                "fdisk",
            ],
        ]
    return None


def textual_python() -> str | None:
    """The interpreter bin/vmtui would open the dashboard with, same candidates in the same order:
    $VMTUI_TEXTUAL_PYTHON alone when set, else the repo's .venv-tui, then python3."""
    override = os.environ.get("VMTUI_TEXTUAL_PYTHON")
    candidates = [override] if override else [str(textual_venv() / "bin/python"), "python3"]
    for candidate in candidates:
        try:
            probe = subprocess.run([candidate, "-c", "import textual"], stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL, timeout=30, check=False)
        except (OSError, subprocess.TimeoutExpired):
            continue
        if probe.returncode == 0:
            return candidate
    return None


def textual_venv() -> Path:
    return state.ROOT / ".venv-tui"


def tool_present(name: str) -> bool:
    """Same lookup the flows use: virtiofsd lives in /usr/libexec, 7z may be 7zz/7za, Textual is
    a Python module that bin/vmtui looks for in .venv-tui first, the firmware is a file pair."""
    if name == TEXTUAL:
        return textual_python() is not None
    if name == "ovmf":
        return any(Path(code).exists() for code, _ in state.COMMON_OVMF_PAIRS)
    if name == "virtiofsd":
        return qemu.find_virtiofsd() is not None
    if name == "7z":
        return any(shutil.which(candidate) for candidate in ("7z", "7zz", "7za"))
    return shutil.which(name) is not None


def installable_names() -> list[str]:
    return [*TOOL_PACKAGES, TEXTUAL]


def missing_tools() -> list[str]:
    return [name for name in installable_names() if not tool_present(name)]


def apt_available(packages: list[str]) -> set[str] | None:
    """The packages apt can install on this release (``apt-cache policy`` shows a candidate), or
    None when that cannot be asked. Ubuntu 22.04 has no ``virtiofsd`` package (the daemon ships in
    qemu-system-common), and one unknown name makes ``apt install`` refuse the whole list."""
    if not packages or shutil.which("apt-cache") is None:
        return None
    try:
        # LC_ALL=C: the labels are translated ("Candidato:" on an Italian host, which made every
        # package look unavailable and the install skip them all).
        result = subprocess.run(["apt-cache", "policy", *packages], capture_output=True, text=True,
                                stdin=subprocess.DEVNULL, timeout=60, check=False,
                                env={**os.environ, "LC_ALL": "C", "LANGUAGE": ""})
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode:
        return None
    found: set[str] = set()
    current = None
    for line in result.stdout.splitlines():
        if line and not line[0].isspace() and line.endswith(":"):
            current = line[:-1]
        elif current and line.strip().startswith("Candidate:") and line.split(":", 1)[1].strip() != "(none)":
            found.add(current)
    # Nothing recognised at all is an output this parser does not understand, not a release
    # without QEMU: install the list unfiltered and let apt say what it lacks.
    return found or None


def package_install_commands(names: list[str], manager: str, extra: list[str] | None = None) -> list[list[str]]:
    packages = list(dict.fromkeys([*(TOOL_PACKAGES[name][manager] for name in names), *(extra or [])]))
    if manager == "apt":
        available = apt_available(packages)
        if available is not None:
            for package in [package for package in packages if package not in available]:
                ui.print_status("warn", f"{package}: no apt package on this release, skipped", ok=False)
            packages = [package for package in packages if package in available]
    if not packages:
        return []
    if manager == "pacman":
        return [["sudo", "pacman", "-S", "--needed", *packages]]
    return [["sudo", "apt", "update"], ["sudo", "apt", "install", "-y", *packages]]


def textual_venv_usable() -> bool:
    """.venv-tui exists *and* has pip: a `python3 -m venv` that failed for want of ensurepip
    (python3-venv missing on Debian/Ubuntu) leaves the directory and its python behind without
    pip, and the next setup then died on "No module named pip" (Lubuntu 22.04, 2026-09-26)."""
    python = textual_venv() / "bin/python"
    if not python.exists():
        return False
    try:
        probe = subprocess.run([str(python), "-m", "pip", "--version"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return probe.returncode == 0


def python_has_ensurepip() -> bool:
    """`python3 -m venv` needs ensurepip, which Debian/Ubuntu ship in python3-venv: ask for that
    package only when it is really missing, or every setup would want sudo for nothing."""
    try:
        return subprocess.run(["python3", "-c", "import ensurepip"], stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=60, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def textual_install_commands() -> list[list[str]]:
    venv = textual_venv()
    if textual_venv_usable():
        commands: list[list[str]] = []
    elif (venv / "bin/python").exists():
        commands = [["python3", "-m", "venv", "--clear", str(venv)]]
    else:
        commands = [["python3", "-m", "venv", str(venv)]]
    return [*commands, [str(venv / "bin/python"), "-m", "pip", "install", "--quiet", "-e", f"{state.ROOT}[tui]"]]


def install_upstream_virtiofsd(dry_run: bool = False) -> Path:
    """Fetch the pinned upstream static virtiofsd into the repository's .tools/ (no sudo)."""
    import hashlib
    import urllib.request
    import zipfile

    dest = state.ROOT / qemu.VIRTIOFSD_LOCAL
    release = VIRTIOFSD_RELEASE
    ui.print_command(["download", release["url"], "->", str(dest)])
    if dry_run:
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    archive = dest.with_name("virtiofsd.zip.part")
    try:
        request = urllib.request.Request(release["url"], headers={"User-Agent": state.HTTP_USER_AGENT})
        with urllib.request.urlopen(request, timeout=120) as response, archive.open("wb") as fh:
            shutil.copyfileobj(response, fh)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != release["sha256"]:
            raise VMError(f"virtiofsd {release['version']} download does not match its pinned SHA-256 ({digest})")
        partial = dest.with_name("virtiofsd.part")
        with zipfile.ZipFile(archive) as bundle, bundle.open(release["member"]) as source, partial.open("wb") as target:
            shutil.copyfileobj(source, target)
        partial.chmod(0o755)
        partial.replace(dest)
    except (OSError, zipfile.BadZipFile, KeyError) as exc:
        raise VMError(f"Unable to install virtiofsd {release['version']} from {release['url']}: {exc}") from exc
    finally:
        archive.unlink(missing_ok=True)
    ui.print_status("ok", f"virtiofsd {release['version']} installed: {ui.pretty_path(dest)}")
    return dest


def install_tools(names: list[str], *, assume_yes: bool = False, dry_run: bool = False) -> None:
    """`vmctl setup --install [NAME...]`: the named tools, or every missing one when none is named;
    a name already present is reported and skipped. Asks before running anything unless --yes."""
    unknown = [name for name in names if name not in installable_names()]
    if unknown:
        raise VMError(f"Unknown tool(s): {', '.join(unknown)}. Installable: {', '.join(installable_names())}")
    if names:
        for name in names:
            if tool_present(name):
                ui.print_status("ok", f"{name} is already installed")
        wanted = [name for name in dict.fromkeys(names) if not tool_present(name)]
    else:
        wanted = missing_tools()
    if not wanted:
        ui.print_note("Nothing to install.")
        return

    system = [name for name in wanted if name != TEXTUAL]
    manager = package_manager()
    # No usable distribution virtiofsd (Ubuntu 22.04 has no package, only QEMU's C daemon that
    # needs root): the upstream static build goes into .tools/ instead.
    upstream_virtiofsd = False
    if "virtiofsd" in system and manager == "apt":
        # python3 is always there: asked alone, a missing name gives an empty answer, which
        # apt_available reads as "cannot tell" and the install would try apt anyway.
        available = apt_available([TOOL_PACKAGES["virtiofsd"]["apt"], "python3"])
        if available is not None and TOOL_PACKAGES["virtiofsd"]["apt"] not in available:
            system.remove("virtiofsd")
            upstream_virtiofsd = True
    if system and manager is None:
        raise VMError(f"No package list for this distribution; install {', '.join(system)} with its package manager "
                      f"({' '.join(host_install_hints())})")
    # Debian and Ubuntu split ensurepip out of python3: `python3 -m venv` fails without python3-venv.
    extra = (["python3-venv"] if TEXTUAL in wanted and manager == "apt" and not textual_venv_usable()
             and not python_has_ensurepip() else [])
    commands = package_install_commands(system, manager, extra) if (system or extra) and manager else []
    if TEXTUAL in wanted:
        commands += textual_install_commands()

    for name in wanted:
        if name == TEXTUAL:
            source = ".venv-tui (pip install -e \".[tui]\")"
        elif name == "virtiofsd" and upstream_virtiofsd:
            source = f"upstream static build {VIRTIOFSD_RELEASE['version']} into {qemu.VIRTIOFSD_LOCAL} (no sudo)"
        else:
            source = TOOL_PACKAGES[name][manager or "apt"]
        ui.print_note(f"{name} <- {source}")
    for cmd in commands:
        ui.print_note(f"  {runtime.shell_join(cmd)}")
    if not (assume_yes or dry_run) and not runtime.confirm_default_no("Run these commands?"):
        raise VMError("Not confirmed (pass --yes to skip the question in scripts)")
    if upstream_virtiofsd:
        install_upstream_virtiofsd(dry_run=dry_run)
    for cmd in commands:
        try:
            runtime.run(cmd, dry_run=dry_run)
        except subprocess.CalledProcessError as exc:
            hint = " (on Debian/Ubuntu `python3 -m venv` needs the python3-venv package)" if cmd[1:3] == ["-m", "venv"] else ""
            raise VMError(f"Installation failed: {runtime.shell_join(cmd)}{hint}") from exc


def prompt_yes_no(prompt: str) -> bool:
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return False
    try:
        answer = input(f"{prompt} [y/N] ").strip().lower()
    except EOFError:
        return False
    return answer in {"y", "yes"}


def prompt_yes_no_default_yes(prompt: str) -> bool:
    if not getattr(sys.stdin, "isatty", lambda: False)():
        return True
    try:
        answer = input(f"{prompt} [Y/n] ").strip().lower()
    except EOFError:
        return True
    if not answer:
        return True
    return answer in {"y", "yes"}


def vmctl_on_path(prefix: Path | None = None) -> bool:
    """True when `vmctl` resolves to this checkout's shim from the current PATH (setup.sh links it)."""
    found = shutil.which("vmctl")
    if not found:
        return False
    try:
        return Path(found).resolve() == (state.ROOT / "bin" / "vmctl").resolve()
    except OSError:
        return False


def render_welcome(*, profiles: int, on_path: bool, kvm: tuple[bool, str], textual: bool, missing: list[str]) -> str:
    """The screen `vmctl welcome` prints and setup.sh ends with: what to do next, in order, with
    commands that work on this host (no make; `./bin/vmctl` while ~/.local/bin is not on PATH)."""
    from vmctl import ui

    cmd = "vmctl" if on_path else "./bin/vmctl"
    tui = "vmtui" if on_path else "./bin/vmtui"
    kvm_ok, kvm_note = kvm
    rows = [
        ("In your browser", f"{cmd} web --open", "install, boot, console, SSH, every command"),
        ("In the terminal", tui, "the same lab as a dashboard"),
        ("A first VM, no clicks", f"{cmd} bootstrap-preseed debian-server", "Debian server, ~10 min, ISO downloaded"),
        ("Use it", f"{cmd} shell debian-server", "SSH in; `start`, `stop`, `attach` for the rest"),
        ("What exists", f"{cmd} list · {cmd} status", "profiles, disks, what is running"),
        ("Make it yours", "vms/profiles/local.json", "your user, SSH key, ISOs (copy local.json.example)"),
        ("A whole lab", f"{cmd} group install netlab", "pfSense + Pi-hole + client; proxmox-lab: 3 nodes"),
        ("Help", f"{cmd} --help · {cmd} <command> --help", "docs/ and README.md for the rest"),
    ]
    width = max(len(command) for _, command, _ in rows)
    lines = ["", ui.style("  QEMU ISO Lab is ready", ui.BOLD, ui.GREEN),
             ui.style("  " + "─" * 70, ui.CYAN)]
    facts = [f"{profiles} profiles", ("KVM ok" if kvm_ok else "no KVM: slow guests"), ("Textual dashboard ok" if textual else "Textual missing: vmtui falls back to fzf/dialog")]
    lines.append("  " + ui.style(" · ".join(facts), ui.CYAN))
    if missing:
        lines.append("  " + ui.style(f"still missing: {', '.join(missing)}  →  {cmd} setup --install", ui.YELLOW))
    if not kvm_ok:
        lines.append("  " + ui.style(kvm_note, ui.YELLOW))
    lines.append("")
    for label, command, note in rows:
        lines.append(f"  {label:<22}{ui.style(command.ljust(width), ui.BOLD)}  {ui.style(note, ui.CYAN)}")
    lines.append("")
    if not on_path:
        lines.append("  " + ui.style("~/.local/bin is not on your PATH yet", ui.YELLOW) + ": the commands above use ./bin/ from this")
        lines.append("  directory. To use plain vmctl/vmtui everywhere: open a new login shell, or")
        lines.append("    " + ui.style('export PATH="$HOME/.local/bin:$PATH"', ui.BOLD) + "   (add it to ~/.zshrc or ~/.bashrc)")
        lines.append("")
    lines.append("  " + ui.style(f"Print this again any time: {cmd} welcome", ui.CYAN))
    lines.append("")
    return "\n".join(lines)
