# floofycrew v1.3.7 (public edition)

Release tag `v1.3.7` of https://github.com/floofycrew/FloofyCrew.

FloofyCrew is an **unofficial** modding ecosystem for KiroCrew. This release ships the `floofy`
manager (zipapp and wheel) and the Loader app (a KiroCrew App), built from the shared core plus
the public edition adapter. It contains no KiroCrew code and never replaces the host (design DR-1).

## Supports (host versions with `loader: ok` in the compatibility matrix)

- not recorded in this build (the release stage passes `--supports supports.json`)

## Assets

| File | sha256 |
|---|---|
| `dist/floofy.pyz` | `59886ca548f0697b4f6a09a8479cfcd7ef312911a5ed6d09a3daeedc12538e9c` |
| `dist/floofycrew-1.3.7-py3-none-any.whl` | `9fbb4d0f390480f57f0e1c086d0abc16b8411099b9a04839abcb3e723fca6a66` |
| `dist/floofycrew-loader-app-1.3.7.zip` | `743666187a4e0ac99359d8c613c5df8ff94a1b5523326beec85b236db15d08d3` |

Verify a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux) or `shasum -a 256 -c SHA256SUMS --ignore-missing` (macOS).
