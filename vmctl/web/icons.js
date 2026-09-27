// Catalog icons: shared by the web dashboard (/assets/icons.js) and the published catalog
// site (tools/build_catalog_site.py copies it next to distro-icons.svg). No dependencies:
// window.ICON_SPRITE may point at the sprite (default: the dashboard's /assets path).
const ICON_SPRITE = typeof window !== "undefined" && window.ICON_SPRITE !== undefined ? window.ICON_SPRITE : "/assets/distro-icons.svg";  // "" = the sprite is inlined in the page
const iconEsc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
// Font Logos glyphs are vendored under Unlicense; generic fallbacks are original CC0 artwork.
// Sources, pinned revision and trademark notice: vmctl/web/ICONS.md.
const distroIconKeys = new Set("arch alma cachyos alpine centos debian endeavouros fedora freebsd kali neon kubuntu mint nixos opensuse popos rhel rocky linux ubuntu void".split(" "));
const iconPicto = (d) => `<path d="${d}"/>`;
const catalogIcons = {
  arch: ["Arch Linux", "#1793d1", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  cachyos: ["CachyOS", "#1cb1a4", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  endeavouros: ["EndeavourOS", "#7f3fbf", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  pearos: ["pearOS", "#6bbf59", iconPicto("M12 3c3-2 5-1 5-1-1 3-3 3-5 3m0-2v4c-4 0-3 4-6 7-4 6 8 10 12 3 2-4-4-6-4-10")],
  debian: ["Debian", "#d70a53", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  ubuntu: ["Ubuntu", "#e95420", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  lubuntu: ["Lubuntu", "#0068c8", iconPicto("M5 19c1-6 5-12 14-15-1 9-6 14-12 13m0 0L18 6m-8 7h5")],
  kubuntu: ["Kubuntu", "#0079c1", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  xubuntu: ["Xubuntu", "#2a5ea8", iconPicto("M12 3v7m0-4C4 5 3 17 8 20c3 2 9 1 10-4 1-5-2-9-6-10Z M9 13h.01M15 13h.01")],
  kali: ["Kali Linux", "#367bf0", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  mint: ["Linux Mint", "#87cf3e", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  popos: ["Pop!_OS", "#48b9c7", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  neon: ["KDE neon", "#27ae60", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  fedora: ["Fedora", "#51a2da", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  opensuse: ["openSUSE", "#73ba25", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  alpine: ["Alpine Linux", "#0d597f", iconPicto("M3 19 9 8l3 5 2.5-3.5L21 19Z")],
  nixos: ["NixOS", "#5277c3", iconPicto("M12 3v18M4.2 7.5l15.6 9M4.2 16.5l15.6-9M12 3l-2.5 2.5M12 3l2.5 2.5M12 21l-2.5-2.5M12 21l2.5-2.5")],
  void: ["Void Linux", "#478061", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  alma: ["AlmaLinux", "#0069da", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  rocky: ["Rocky Linux", "#10b981", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  centos: ["CentOS", "#a14ea0", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  rhel: ["RHEL family", "#ee0000", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  windows: ["Windows", "#0078d4", iconPicto("M4 5h16v14H4Z M12 5v14M4 12h16")],
  freebsd: ["FreeBSD", "#ab2b28", iconPicto("M4 5h16v13H4Z M8 9h8M8 13h5M9 21h6")],
  pfsense: ["pfSense", "#2d6db5", iconPicto("M12 3 4 6v6c0 4 4 7 8 9 4-2 8-5 8-9V6Z M8 10h8m-8 4h8m-4-4v4")],
  proxmox: ["Proxmox VE", "#e57000", iconPicto("M4 4h16v6H4Z M4 14h16v6H4Z M8 7h.01M11 7h5M8 17h.01M11 17h5")],
  haiku: ["Haiku", "#f0b400", iconPicto("M5 7.5h9M5 12h14M5 16.5h9")],
  reactos: ["ReactOS", "#0088cc", iconPicto("M3 12a9 5 0 1 0 18 0 9 5 0 1 0-18 0M12 3a5 9 0 1 0 0 18 5 9 0 1 0 0-18M12 11v2")],
  kolibrios: ["KolibriOS", "#3f8ed8", iconPicto("M3 8l9 4 8-8-2 10-6 2-4 5 1-6Z")],
  redox: ["Redox OS", "#c94a2b", iconPicto("M12 3l8 5v8l-8 5-8-5V8ZM12 8l4 2v4l-4 2-4-2v-4Z")],
  menuetos: ["MenuetOS", "#7a55b8", iconPicto("M4 6h5v5H4ZM15 6h5v5h-5ZM4 15h5v5H4ZM15 15h5v5h-5Z")],
  serenityos: ["SerenityOS", "#b8332e", iconPicto("M12 4a5 5 0 0 1 5 5c0 4-5 8-5 8s-5-4-5-8a5 5 0 0 1 5-5ZM5 20h14")],
  linux: ["Linux", "#f4b400", iconPicto("m5 6 6 6-6 6m8 0h6")],
  generic: ["Virtual machine", "#5c6b80", iconPicto("M3 4h18v13H3Z M8 21h8m-4-4v4M8 9h8m-8 3h5")],
  network: ["Network lab", "#2fa58f", iconPicto("M8 3h8v6H8Z M2 16h7v5H2Z M15 16h7v5h-7Z M12 9v4H5.5v3M12 13h6.5v3")],
  cluster: ["Virtualization cluster", "#d98a3c", iconPicto("M8 2h8v5H8Z M2 16h8v5H2Z M14 16h8v5h-8Z M12 7v4m-6 5v-5h12v5M10 18.5h4")]
};
// Match the profile identity first, then its display name, and finally the broader family.
// In particular, the Debian client of proxmox-lab is not a Proxmox hypervisor.
const osIconRules = [
  ["proxmox", /(?:^|\b)proxmox(?:-ve|\s+ve)(?:\b|_)/],
  ["pfsense", /\bpfsense\b/], ["cachyos", /\bcachyos\b/],
  ["endeavouros", /\bendeavouros\b/], ["pearos", /\bpearos\b/],
  ["lubuntu", /\blubuntu\b/], ["kubuntu", /\bkubuntu\b/], ["xubuntu", /\bxubuntu\b/],
  ["ubuntu", /\b(?:ubuntu|ubuntustudio|edubuntu)\b/],
  ["mint", /\b(?:linuxmint|linux mint)\b/], ["popos", /\b(?:popos|pop!_os)\b/],
  ["neon", /\bkde[ -]neon\b/], ["kali", /\bkali\b/],
  ["alma", /\balmalinux\b/], ["rocky", /\brocky\b/], ["centos", /\bcentos\b/],
  ["windows", /\bwindows(?:xp|nt|\d|\b)/], ["reactos", /\breactos\b/],
  ["freebsd", /\bfreebsd\b/], ["haiku", /\bhaiku\b/],
  ["kolibrios", /\bkolibri(?:os)?\b/], ["redox", /\bredox\b/], ["menuetos", /\bmenuet(?:os)?\b/], ["serenityos", /\bserenity(?:os)?\b/],
  ["nixos", /\bnixos\b/], ["alpine", /\balpine\b/], ["void", /\bvoid\b/],
  ["opensuse", /\b(?:opensuse|suse)\b/], ["fedora", /\bfedora\b/],
  ["debian", /\bdebian\b/], ["arch", /\b(?:arch|archlinux)\b/]
];
function osIconKey(r) {
  if (!r) return "generic";
  for (const text of [r.name, r.label]) {
    const found = osIconRules.find(([,pattern]) => pattern.test(String(text || "").toLowerCase()));
    if (found) return found[0];
  }
  const family = String(r.family || "").toLowerCase();
  return ({arch:"arch", debian:"debian", alpine:"alpine", fedora:"fedora", opensuse:"opensuse", nix:"nixos", nixos:"nixos", mint:"mint", void:"void", rhel:"rhel", kali:"kali", bsd:"freebsd", proxmox:"proxmox", windows:"windows", reactos:"reactos", haiku:"haiku", linux:"linux"})[family] || "generic";
}
// Dark ink on a light badge (Haiku yellow, Mint green), white on the others.
function iconInk(hex) { const n = parseInt(hex.slice(1), 16); return (0.299*(n>>16&255) + 0.587*(n>>8&255) + 0.114*(n&255)) > 160 ? "#0f1722" : "#ffffff"; }
function catalogIcon(key, large = false) {
  const [label,color,glyph] = catalogIcons[key] || catalogIcons.generic;
  const artwork = distroIconKeys.has(key) ? `<svg viewBox="0 0 24 24" fill="currentColor" stroke="none" focusable="false"><use href="${ICON_SPRITE}#${iconEsc(key)}" width="24" height="24"/></svg>` : `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" focusable="false">${glyph}</svg>`;
  return `<span class="catalog-icon${large ? " large" : ""}" data-icon="${iconEsc(key)}" style="--icon-color:${color};--icon-ink:${iconInk(color)}" title="${iconEsc(label)}" aria-hidden="true">${artwork}</span>`;
}
// End of catalog icon definitions.
