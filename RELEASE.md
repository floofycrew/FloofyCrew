# floofycrew v1.3.10 (public edition)

Release tag `v1.3.10` of https://github.com/floofycrew/FloofyCrew.

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
| `dist/floofy.pyz` | `ce343c630445534d53981e6328ae85c075241ffdf217fd31ce6e8f6b90a51d31` |
| `dist/floofycrew-1.3.10-py3-none-any.whl` | `40f5b538ec36b326a17ad20b927a74e1126210710a630b74b51599d6ecdbbe02` |
| `dist/floofycrew-loader-app-1.3.10.zip` | `4f2db79fca0ba40f3e092b548c2542a6bf57dc057320a303af41de8e94d152b3` |
| `dist/supports.json` | `7613c6961ab57baa129d585bad5b24baa09b2bbfee3e5c99d1c4081fc372ecb1` |

Verify a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux) or `shasum -a 256 -c SHA256SUMS --ignore-missing` (macOS).
