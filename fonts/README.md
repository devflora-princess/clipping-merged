# fonts/ — bundled caption fonts (SIL Open Font License 1.1)

Both files are free OFL fonts, bundled so captions look the same on
any machine without installing fonts system-wide. libass finds them
via the `fontsdir` option the renderer passes to ffmpeg's `ass`
filter; Pillow can also load them directly.

- `Montserrat-Black.ttf` — Montserrat Black (weight 900), the
  Hormozi-style caption font. Static (non-variable) build from
  https://github.com/JulietaUla/Montserrat (fonts/ttf/Montserrat-Black.ttf).
  NOTE: the variable Montserrat from google/fonts loads as Thin in
  Pillow/libass — do NOT substitute the variable file here.
- `Anton-Regular.ttf` — Anton, condensed fallback when a line is too
  wide for Montserrat Black. From https://github.com/google/fonts
  (ofl/anton/Anton-Regular.ttf).

If both files are missing, the renderer falls back to DejaVu Sans
Bold (system font) and says so in its output — the clip still renders.
