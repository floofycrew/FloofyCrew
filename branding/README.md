# FloofyCrew branding

## Chosen mark: `fox-sunset`

- `logo.svg` — the mark (512×512 viewBox, pink→orange card, peach fox, white tail tip).
- `logo-mono.svg` — single-colour silhouette using `currentColor`, no card
  (inline in docs / README badges / anywhere it must follow text colour).
- `icons/` — PNGs at 16, 32, 48, 64, 128, 180, 192, 256, 512 (`icon-N.png`),
  plus `apple-touch-icon.png` (180), `favicon-16.png`, `favicon-32.png`, and a
  multi-size `favicon.ico` (16/32/48/64/256, PNG-in-ICO). Transparent corners.
- `build_icons.py` — regenerates all of the above from `gen_logos.py`
  (`CHOSEN = "sunset"`); renders with headless Chromium, packs the ICO with stdlib.
- `wordmark.svg` (dark text, light surfaces), `wordmark-dark.svg` (peach text,
  dark surfaces), `wordmark-text.svg` (text only) — horizontal lockup, mark +
  "Floofy" in ink + "Crew" in the sunset gradient. Height 128 user units.
- `build_wordmark.py` — sets the text in Nunito ExtraBold (SIL OFL-1.1,
  [googlefonts/nunito](https://github.com/googlefonts/nunito)), kerned with
  HarfBuzz, and converts it to outlines so the SVGs need no font at render time.
  The font is downloaded to `.cache/` (gitignored) and pinned in `.font-sha256`;
  run with `uv run --no-project --with fonttools --with uharfbuzz python branding/build_wordmark.py`.

The Loader app ships the mark as its store icon: `loader-app/art/icon.svg` is a
byte-equal copy of `logo.svg` declared by `iconPath` in `loader-app/app.json`
(pinned by `loader-app/tests/test_manifest.py`).

## Terminal banner (`ascii/`)

- `ascii/source.html` — the coloured ASCII-art export of the mark (38 × 19 cells).
- `build_ascii.py` — parses it into `ascii/floofy.json` (one `[char, [r, g, b]]`
  entry per cell: what the CLI renders from), `ascii/floofy.ans` (24-bit ANSI, for
  `cat`) and `ascii/floofy.txt` (glyphs only). Standard library; re-run after
  replacing the export.
- Shipped copies, each byte-identical to the file here and pinned by a test:
  `floofy-core/floofy_core/cli/banner.json` (the CLI's package data,
  `floofy-core/tests/test_banner.py`) and the install scripts' `floofy.ans` /
  `floofy.txt` (`packaging/`). Regenerate, then copy — never edit a copy.

```html
<link rel="icon" href="icons/favicon.ico" sizes="any">
<link rel="icon" type="image/svg+xml" href="logo.svg">
<link rel="apple-touch-icon" href="icons/apple-touch-icon.png">
```

## Candidates

Logo candidates derived from the KiroCrew icon language (ghost arch body, two
pill eyes, flat rounded square) with FloofyCrew's twist: a fluffy scalloped
outline and fox / wolf ears.

- `gen_logos.py` — parametric generator. `python branding/gen_logos.py --sheet`
  rewrites `candidates/*.svg` and `candidates/index.html`. Two marks
  (`fox_mark`, `wolf_mark`) × named palettes in `PALETTES`.
- `candidates/` — round-2 SVGs (512×512 viewBox) plus `sheet.png` (dark / light /
  48px favicon size). `candidates/round1/` keeps the first pass (cat, bear,
  bunny, trio) for reference.

| Candidate | Ears | Palette |
|---|---|---|
| `fox-ember` | fox (tall, slim, leaning out) | orange `#FF8F3E→#F05A28`, cream body |
| `fox-charcoal` | fox | charcoal card, ember-orange body (dark-mode sibling of cream) |
| `fox-cream` | fox | cream card, ember-orange body (light-mode friendly) |
| `fox-sky` | fox | Rimuru sky blue `#63B9F7→#2F86E3` |
| `fox-arctic` | fox | pale blue card, slate-blue body (light-mode sibling of sky) |
| `fox-sunset` | fox | pink→orange gradient `#FF6B8B→#FF9A3D`, peach body `#FFE4CF`, white tail tip |
| `fox-sakura` | fox | pink `#FF8CB0→#F25A87` |
| `fox-coral` | fox | coral `#FF8272→#E9584A` |
| `fox-gold` | fox | gold `#F7BE4A→#E0901D` |
| `fox-mint` | fox | mint `#5ED4B5→#26A98A` |
| `fox-dusk` | fox | violet `#5B3FA8→#3B2A72`, lavender body |
| `fox-graphite` | fox | graphite card, white body, ember inner ear as the only accent |
| `wolf-midnight` | wolf (wide base, upright, blunter tip) | navy `#2B3160→#161A33`, frost body |
| `wolf-forest` | wolf | green `#1F8A70→#12604C` |
| `wolf-kiro` | wolf | KiroCrew purple `#7C55F5→#6A3BEA` |
| `fox-mono-mark` | fox, no inner ear | single colour, no background — favicon / wordmark / badges |

Ear sizes: fox 222×140 (lean 0.42), wolf 206×178 (lean 0.2).

Fox tail (`fox_tail`): a bent teardrop — thin where it leaves the body, fattest
mid-way, tapering to a point — built by offsetting a quadratic-bezier
centreline by a sine width profile. The last ~30 % is overdrawn in the palette's
`tip` colour with a chevron cut; `tip` is chosen per palette to contrast with
both the body and the background (deep shade of the bg on saturated cards,
light on charcoal, ember accent on graphite). The fox body is shifted left
(cx 210) so the tail rises beside the right ear. `fox_mark(tail=False)` gives
the centred tail-less version.

Preview render (no rsvg/inkscape on this box):

```sh
~/.cache/ms-playwright/chromium-1244/chrome-linux64/chrome --headless=new --no-sandbox \
  --disable-gpu --hide-scrollbars --window-size=1100,1240 \
  --screenshot=branding/candidates/sheet.png "file://$PWD/branding/candidates/index.html"
```
