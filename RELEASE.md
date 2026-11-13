# floofycrew v1.3.12 (public edition)

Release tag `v1.3.12` of https://github.com/floofycrew/FloofyCrew.

FloofyCrew is an **unofficial** modding ecosystem for KiroCrew. This release ships the `floofy`
manager (zipapp and wheel) and the Loader app (a KiroCrew App), built from the shared core plus
the public edition adapter. It contains no KiroCrew code and never replaces the host (design DR-1).

## Supports (host versions with `loader: ok` in the compatibility matrix)

- external/insider: 0.7.0rc5, 0.7.0rc6, 0.7.0rc7, 0.7.0rc8, 0.7.0rc9, 0.8.0rc1, 0.8.0rc2, 0.8.0rc3, 0.8.0rc4, 0.8.0rc10, 0.10.0rc4
- external/stable: 0.6.0, 0.7.0, 0.7.1, 0.7.2, 0.9.0
- internal/beta: 0.7.0.5, 0.7.0.7, 0.7.0.8, 0.8.0.6, 0.10.0.2
- internal/stable: 0.7.0.5, 0.7.0.8, 0.8.0.6, 0.8.0.9, 0.9.0.4

## Assets

| File | sha256 |
|---|---|
| `dist/floofy.pyz` | `094dacf46b78185e2bb07c5bd27630719b8f994c45d9ef2078f1cad17e6033a9` |
| `dist/floofycrew-1.3.12-py3-none-any.whl` | `cbc1e0341f14732e09dd610dc6c9b7d756016b1f6a6b5d314e979471697cf9d2` |
| `dist/floofycrew-loader-app-1.3.12.zip` | `3d4c85f7cc120af0172879186319a0760f1c05e236759a641667020a6f15cc8a` |
| `dist/supports.json` | `8ebb294fe0f3184c4a22087b88e1c3a7609337bb1a84d5209b79ddfdb202f71d` |

Verify a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux) or `shasum -a 256 -c SHA256SUMS --ignore-missing` (macOS).
