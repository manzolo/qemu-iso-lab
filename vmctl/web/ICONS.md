# Catalog icons

The dashboard uses a local SVG subset of [Font Logos](https://github.com/lukas-w/font-logos),
distributed under the [Unlicense](FONT-LOGOS-LICENSE.txt). No font or CDN is needed at runtime.
Brand names and logos remain trademarks of their respective owners; the icons identify the
corresponding guest products and do not imply endorsement. They are not claimed as original
artwork or as marks free of third-party rights.

Source revision: `d3bf5d299e54595db1b19681a0cc57ab10454857`.
Source directory: https://github.com/lukas-w/font-logos/tree/d3bf5d299e54595db1b19681a0cc57ab10454857/vectors

`distro-icons.svg` contains these source files, keyed by the dashboard family:

| Family | Font Logos SVG |
| --- | --- |
| alma | almalinux.svg |
| alpine | alpine.svg |
| arch | archlinux.svg |
| cachyos | cachyos.svg |
| centos | centos.svg |
| debian | debian.svg |
| endeavouros | endeavour.svg |
| fedora | fedora.svg |
| freebsd | freebsd.svg |
| kali | kali-linux.svg |
| kubuntu | kubuntu.svg |
| linux | tux.svg |
| mint | linuxmint.svg |
| neon | kde-neon.svg |
| nixos | nixos.svg |
| opensuse | opensuse.svg |
| popos | pop-os.svg |
| rhel | redhat.svg |
| rocky | rocky-linux.svg |
| ubuntu | ubuntu.svg |
| void | void.svg |

The original geometry and aspect ratios are preserved inside SVG symbols. XML metadata,
editor attributes and scripts were removed; identifiers were prefixed per symbol, and black
fills inherit `currentColor`. Only static SVG drawing elements and attributes are included.

Families absent from that subset use original generic pictograms from this project under
CC0-1.0, as do the action icons. These fallbacks contain no initials and do not reproduce the
respective vendor logos. To add a family, update `catalogIcons` and `osIconRules` in
`index.html`; document any additional third-party asset and its license here.
