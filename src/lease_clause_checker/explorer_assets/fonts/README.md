# Fonts

The explorer's fonts, served with the page so it makes no request to a font service and renders
offline. All three are under the SIL Open Font License 1.1 (the `OFL-*.txt` files here). Each
file covers Latin text; any other character falls back to the system font.

| Files | Font | From |
|---|---|---|
| `SourceSerif4-Latin.woff2`, `SourceSerif4-Italic-Latin.woff2` | Source Serif 4, variable (weight and optical size), roman and italic | Google Fonts' latin subset (v15), retrieved 2026-11-15 |
| `IBMPlexSans-{Regular,Medium,SemiBold,Italic}-Latin1.woff2` | IBM Plex Sans 400, 500, 600 and 400 italic | IBM's own Latin1 subsets, npm package `@ibm/plex-sans` 1.1.0 |
| `IBMPlexMono-{Regular,Medium}-Latin1.woff2` | IBM Plex Mono 400 and 500 | IBM's own Latin1 subsets, npm package `@ibm/plex-mono` 1.1.0 |

IBM Plex's licence reserves the name "Plex", and a subset counts as a modified version under
the OFL, so these are IBM's own subsets, unchanged. The licence of Google Fonts' release of
Source Serif 4, which these files come from, reserves no font name (Adobe's own release
reserves "Source").
The `unicode-range` of each `@font-face` in `explorer.css` is the range its source declares.
