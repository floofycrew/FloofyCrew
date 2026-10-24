# floofycrew v1.3.11 (public edition)

Release tag `v1.3.11` of https://github.com/floofycrew/FloofyCrew.

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
| `dist/floofy.pyz` | `c90a903afda98d1e9796e1c0c2dd2e21515e7ca8d9ab615329c8faa5b81c67a9` |
| `dist/floofycrew-1.3.11-py3-none-any.whl` | `1c47c1cef56c9eafa7da1fe490f352c627c89192959868b80c8e310da08c97c9` |
| `dist/floofycrew-loader-app-1.3.11.zip` | `2509222a7434deef2fb5d43415a7eb74dabb452c9ffd4cc5e24cf448dda6568c` |
| `dist/supports.json` | `978008d3e20a5633bdd20de0c6e7a596980b79a335ca2b735f1b39a0a6f1ff03` |

Verify a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux) or `shasum -a 256 -c SHA256SUMS --ignore-missing` (macOS).
