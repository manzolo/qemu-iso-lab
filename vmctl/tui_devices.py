"""Compact device tables and detail panes, rendered from one vmctl snapshot."""
from __future__ import annotations

from typing import Any

from vmctl import runtime


def clean(value: Any) -> str:
    text = " ".join(str(value or "-").split())
    return "".join(char for char in text if char.isprintable())


def cell(value: Any, width: int, *, right: bool = False) -> str:
    text = clean(value)
    if len(text) > width:
        text = text[:width - 1] + "…"
    return text.rjust(width) if right else text.ljust(width)


def table_row(values: list[str], widths: list[int]) -> str:
    return " │ ".join(cell(value, width, right=index == 1)
                      for index, (value, width) in enumerate(zip(values, widths)))


def device_widths(columns: int) -> list[int]:
    # Leave room for panel margins, borders, padding and a scrollbar.
    return [14, 10, max(8, columns - 14 - 14 - 10 - 9 - 5 - 12), 9, 5]


def menu_header(columns: int) -> str:
    return table_row(["DEVICE", "SIZE", "MODEL", "SERIAL", "BUS"], device_widths(columns))


def filesystem_label(value: Any) -> str:
    """Add a single-cell, monochrome marker without replacing the filesystem name."""
    name = clean(value)
    kind = name.casefold()
    if kind == "-":
        return name
    if kind in {"ntfs", "ntfs3"}:
        icon = "⊞"
    elif kind in {"vfat", "fat", "fat12", "fat16", "fat32", "msdos", "exfat"}:
        icon = "↔"
    elif kind == "swap":
        icon = "≋"
    elif kind in {"crypto_luks", "bitlocker"}:
        icon = "▣"
    elif kind in {"ext2", "ext3", "ext4", "xfs", "btrfs", "f2fs", "zfs", "bcachefs"}:
        icon = "▤"
    else:
        icon = "·"
    return f"{icon} {name}"


def device_details(info: dict[str, Any], columns: int) -> str:
    free = info.get("unallocated_bytes")
    free_text = "unknown"
    if free is not None:
        free_text = f"{runtime.format_bytes(free)} ({free / 10**9:.1f} GB), approx."
    lines = [
        f"Device    {clean(info['path'])}   ({runtime.format_bytes(info['size'])})",
        f"Model     {clean(info.get('model'))}",
        f"Serial    {clean(info.get('serial'))}",
        f"Bus       {clean(info.get('tran')).upper()}",
        f"Unallocated  {free_text}",
        "",
    ]
    widths = [18, 10, 12, max(8, columns - 14 - 18 - 10 - 12 - 9)]
    lines.append(table_row(["PARTITION", "SIZE", "FILESYSTEM", "LABEL"], widths))
    lines.append("─" * (sum(widths) + 9))

    def visit(node: dict[str, Any]) -> None:
        path = clean(node.get("path") or node.get("name"))
        label = clean(node.get("label"))
        filesystem = filesystem_label(node.get("fstype"))
        lines.append(table_row([
            path, runtime.format_bytes(int(node.get("size") or 0)),
            filesystem, label,
        ], widths))
        # Preserve full values if they do not fit in a table cell.
        if len(path) > widths[0]:
            lines.append(f"  Path: {path}")
        if len(filesystem) > widths[2]:
            lines.append(f"  Filesystem: {filesystem}")
        if len(label) > widths[3]:
            lines.append(f"  Label: {label}")
        for child in node.get("children") or []:
            visit(child)

    children = info.get("children") or []
    if children:
        for child in children:
            visit(child)
    elif info.get("fstype"):
        visit(info)
    else:
        lines.append("No recognized partitions or filesystems")
    if free is not None:
        lines.append(table_row(["Unallocated", runtime.format_bytes(free), "-", "Free space"], widths))
    lines += ["", "Mounted at: " + (", ".join(clean(mp) for mp in info.get("mountpoints") or []) or "not mounted")]
    return "\n".join(lines)


def menu_items(devices: list[dict[str, Any]], columns: int) -> list[tuple[str, str]]:
    items = []
    for info in devices:
        serial = clean(info.get("serial"))
        if len(serial) > 9:
            serial = "…" + serial[-8:]
        row = table_row([
            info["path"], runtime.format_bytes(info["size"]), clean(info.get("model")),
            serial, clean(info.get("tran")).upper(),
        ], device_widths(columns))
        # fzf's third field holds printf %b text, shell-quoted by its {3}
        # placeholder. Escape literal backslashes before encoding line breaks.
        details = device_details(info, columns).replace("\\", "\\\\").replace("\n", "\\n")
        items.append((info["path"], row + "\t" + details))
    return items
