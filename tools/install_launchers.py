"""Install the Linux application-menu entry for the current user."""
import os
import shutil
import sys
from pathlib import Path


def desktop_exec(path: Path) -> str:
    # Exec field quoting, then Desktop Entry string escaping. Literal % is %%.
    value = str(path).replace("%", "%%")
    for char in ('\\', '"', '`', '$'):
        value = value.replace(char, '\\' + char)
    return '"' + value.replace('\\', '\\\\') + '"'


ICON = Path("vmctl/web/qemu-iso-lab.svg")  # also the dashboard's and the catalog site's favicon


def install(root: Path, data_home: Path) -> Path:
    applications = data_home / "applications"
    applications.mkdir(parents=True, exist_ok=True)
    # The icon theme's scalable directory: every desktop resolves `Icon=qemu-iso-lab` from there.
    icons = data_home / "icons" / "hicolor" / "scalable" / "apps"
    icons.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / ICON, icons / "qemu-iso-lab.svg")
    desktop = applications / "qemu-iso-lab.desktop"
    desktop.write_text(
        "[Desktop Entry]\nType=Application\nName=QEMU ISO Lab\n"
        "Comment=Open your virtual machine dashboard\n"
        f"Exec={desktop_exec(root / 'bin' / 'qemu-iso-lab')}\n"
        "Icon=qemu-iso-lab\nTerminal=true\nCategories=System;Emulator;\n"
        "Keywords=QEMU;VM;virtual machine;\n",
        encoding="utf-8",
    )
    return desktop


if __name__ == "__main__":
    data_home = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local/share")
    print(f"  application menu: {install(Path(sys.argv[1]).resolve(), data_home)}")
