# floofycrew v1.3.1 (public edition)

Release tag `v1.3.1` of https://github.com/floofycrew/FloofyCrew.

FloofyCrew is an **unofficial** modding ecosystem for KiroCrew. This release ships the `floofy`
manager (zipapp and wheel) and the Loader app (a KiroCrew App), built from the shared core plus
the public edition adapter. It contains no KiroCrew code and never replaces the host (design DR-1).

## Supports (host versions with `loader: ok` in the compatibility matrix)

- not recorded in this build (the release stage passes `--supports supports.json`)

## Assets

| File | sha256 |
|---|---|
| `dist/floofy.pyz` | `0987df885752fe1cf42fadc78eacea0bb5d64a469a868fb0be2c54d0867130eb` |
| `dist/floofycrew-1.3.1-py3-none-any.whl` | `568a557f88547e349e94fccbb67e6aca09bab7993dfd82e3abe6f1c9f431998b` |
| `dist/floofycrew-loader-app-1.3.1.zip` | `ade3a2d4a2a0f5354ab6487b261783e544085ac163f170e634be1a9808de3706` |

Verify a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux) or `shasum -a 256 -c SHA256SUMS --ignore-missing` (macOS).
