# Catalog icons

The icons of the web dashboard (`catalogIcons` in `index.html`) are original artwork of this
project, released under CC0-1.0. They reproduce **no third-party operating-system logo**: a
profile is told apart by a family colour (Ubuntu orange, Debian red, Fedora blue... colours are
not protectable), a bold monogram, or a generic pictogram — a window with four panes, a
mountain, a snowflake, a shield, a rack of servers, three lines of a poem.

To add a family: one entry `key: [label, "#colour", glyph]` in `catalogIcons` and a rule in
`osIconRules` (matched against the profile name and label, then `meta.family`). Keep glyphs
generic; do not paste a vendor's logo, even a CC0 tracing of it, because the mark stays theirs.
