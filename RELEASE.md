# floofycrew v1.3.6 (public edition)

Release tag `v1.3.6` of https://github.com/floofycrew/FloofyCrew.

FloofyCrew is an **unofficial** modding ecosystem for KiroCrew. This release ships the `floofy`
manager (zipapp and wheel) and the Loader app (a KiroCrew App), built from the shared core plus
the public edition adapter. It contains no KiroCrew code and never replaces the host (design DR-1).

## Supports (host versions with `loader: ok` in the compatibility matrix)

- not recorded in this build (the release stage passes `--supports supports.json`)

## Assets

| File | sha256 |
|---|---|
| `dist/floofy.pyz` | `d4af78d35aa88545ee52f01591f38b9510ef46d69429f98ddc3ec830635d2136` |
| `dist/floofycrew-1.3.6-py3-none-any.whl` | `a614a5a0f4eda870afb2e9518a666d3af4d920f9adc4809dab6b3a40b4a74bcf` |
| `dist/floofycrew-loader-app-1.3.6.zip` | `4b3efd79a0839419b5adc998ce998253140948ff36e102ce4d8fe8c52942a589` |

Verify a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux) or `shasum -a 256 -c SHA256SUMS --ignore-missing` (macOS).
