# packaging

The two release tracks of FloofyCrew (design DR-3; Requirement 10.5). Both build
the **same** shared core, Loader app, SPA host, registry client and docs; only
the edition adapter differs, and `scripts/artifact_identity_report.py` measures
how much of the two trees is byte-identical (Requirement 10.4, target ≥ 80 %).
Everything here is standard-library Python 3.12 plus shell, reproducible
(fixed timestamps, sorted entries) and verified by `--check` (build twice,
compare every byte).

| Track | Directory | Output | Distribution |
|---|---|---|---|
| Internal (***REMOVED*** edition) | `internal/` | `build/FloofyCrew/`: `dist/floofy.pyz`, the Loader app (`app/floofycrew/` + archive), `SHA256SUMS`, `install.sh` (+ `banner/`), `app-registry.json`, `RELEASE.md` | the ***REMOVED*** data package `FloofyCrew` (`build.sh --out <clone>`; the package itself is an operator app-registry row); the community App Store entry is prepared in `internal/community-registry-submission.md` |
| Public (GitHub) | `public/` | `build/public/`: `dist/floofy.pyz`, the wheel, the Loader app archive, `SHA256SUMS`, plus the `loader-app` tree (`app/`, `app-registry.json`, `install.sh`, `README.md`, `RELEASE.md`) | GitHub release assets for tag `vX.Y.Z` (`.github/workflows/release.yml`), the generated `loader-app` branch, `curl -fsSL …/packaging/public/install.sh \| sh`; `public/registry-row.json` carries the app-registry row and the `registries.json` source with the pinned public key |

The wheel is written by `scripts/build_wheel.py` (a stdlib wheel writer: the
repository is a multi-root workspace with no build backend, so `uv build` has
nothing to build; the writer is deterministic and needs nothing installed).
Public artifacts are scanned for internal identifiers before they ship
(`scripts/check_no_internal_identifiers.py`); the internal package may name
internal systems and is not scanned. Neither track ever contains KiroCrew code
(design DR-1).

## The install scripts

Both editions' `install.sh` are POSIX `sh` with one structure (Requirement 15.2):
five numbered steps with a status glyph each (`✓`/`✗`/`·`, ASCII when the locale
is not UTF-8), the CLI's palette and banner — off when stdout is not a terminal,
under `NO_COLOR`, on `TERM=dumb` or with `--no-color`; `--no-banner` /
`FLOOFYCREW_NO_BANNER=1` skip the art alone — and numbered menus with a default
for the two enumerated answers: the re-apply trigger (`floofy init --trigger` /
`--no-trigger`) and whether to run `floofy init` now. A run without a terminal
takes the defaults, prints them and never blocks; the previous flags and
environment variables still work (`FLOOFYCREW_*`, `--no-init`, `--prefix`, flags
after `--` for the internal script), and `FLOOFYCREW_TRIGGER` / `--trigger`
pre-answer the menu. The banner is `branding/ascii/floofy.ans` (24-bit, when
`COLORTERM` says so) or `floofy.txt` in one colour: the internal package ships
them under `banner/`, the public one-liner embeds them (a piped script has no
files beside it). `packaging/tests/test_install_scripts.py` runs both scripts
headless (`setsid`, `</dev/null`) and on a pseudo-terminal.
