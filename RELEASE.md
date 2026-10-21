# floofycrew v1.3.8 (public edition)

Release tag `v1.3.8` of https://github.com/floofycrew/FloofyCrew.

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
| `dist/floofy.pyz` | `cad9e7909e8357c4eaf9b1338fa612695f52ab59593209dc9db8da3470dae370` |
| `dist/floofycrew-1.3.8-py3-none-any.whl` | `5a9c95e0de323767c02bd3138fa24042cac15dd491daba5b25227bbc16616bf7` |
| `dist/floofycrew-loader-app-1.3.8.zip` | `f66d4459f7f9df44b37116d475c1d27acdb87a6919a58011f81a8572d50de402` |
| `dist/supports.json` | `4dbadb893a7c4d35e5b14d71ea6f750766b80fb8fcfacea11ef450078494b4ec` |

Verify a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux) or `shasum -a 256 -c SHA256SUMS --ignore-missing` (macOS).
