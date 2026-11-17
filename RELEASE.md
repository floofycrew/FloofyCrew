# floofycrew v1.3.15 (public edition)

Release tag `v1.3.15` of https://github.com/floofycrew/FloofyCrew.

FloofyCrew is an **unofficial** modding ecosystem for KiroCrew. This release ships the `floofy`
manager (zipapp and wheel) and the Loader app (a KiroCrew App), built from the shared core plus
the public edition adapter. It contains no KiroCrew code and never replaces the host (design DR-1).

## Supports (host versions with `loader: ok` in the compatibility matrix)

- external/insider: 0.7.0rc5, 0.7.0rc6, 0.7.0rc7, 0.7.0rc8, 0.7.0rc9, 0.8.0rc1, 0.8.0rc2, 0.8.0rc3, 0.8.0rc4, 0.8.0rc10, 0.10.0rc4, 0.10.0rc5, 0.11.0rc1, 0.11.0rc2, 0.11.0rc3, 0.11.0rc4, 0.11.0rc5, 0.12.0rc4, 0.12.0rc5, 0.12.0rc6, 0.12.0rc7, 0.12.0rc8, 0.12.0rc9, 0.12.0rc10, 0.12.1rc3, 0.12.2rc2
- external/nightly: 0.8.0.dev20260920060902, 0.8.0.dev20260921061338, 0.8.0.dev20260922061137, 0.8.0.dev20260924061032, 0.8.0.dev20260928053132, 0.9.0.dev20260929061324, 0.9.0.dev20260930061058, 0.9.0.dev20261005061801, 0.9.0.dev20261007060958, 0.9.0.dev20261009061050, 0.10.0.dev20261012014346, 0.10.0.dev20261012201303, 0.10.0.dev20261014061000, 0.10.0.dev20261015061203, 0.11.0.dev20261019061302, 0.11.0.dev20261020164600, 0.11.0.dev20261022060953, 0.11.0.dev20261025060904, 0.12.0.dev20261027011344, 0.12.0.dev20261027141113, 0.12.0.dev20261029060748, 0.12.0.dev20261031060601, 0.13.0.dev20261103060710, 0.13.0.dev20261104060801, 0.13.0.dev20261105032008, 0.13.0.dev20261106061507, 0.13.0.dev20261107060532, 0.13.0.dev20261111085733, 0.13.0.dev20261112101633
- external/stable: 0.6.0, 0.7.0, 0.7.1, 0.7.2, 0.9.0, 0.10.0, 0.11.0, 0.11.1, 0.11.2, 0.12.0, 0.12.1, 0.12.2
- internal/beta: 0.7.0.5, 0.7.0.7, 0.7.0.8, 0.8.0.6, 0.10.0.2, 0.10.0.3, 0.11.0.1, 0.11.0.3, 0.11.1.5, 0.12.0.1, 0.12.0.3, 0.12.0.4, 0.12.0.5, 0.12.0.11, 0.12.2.4
- internal/stable: 0.7.0.5, 0.7.0.8, 0.8.0.6, 0.9.0.4, 0.10.0.3, 0.11.0.3, 0.11.1.5, 0.11.2.1, 0.12.0.9, 0.12.2.4

## Assets

| File | sha256 |
|---|---|
| `dist/floofy.pyz` | `03af2e9d47681bf290c4d48e7be77a57902e928d874ae4e905c58b286e650fec` |
| `dist/floofycrew-1.3.15-py3-none-any.whl` | `838c9a73ccc86ad5a47ce06c3ac9ccffbbc10985e4c962cf90e6d2681eae147c` |
| `dist/floofycrew-loader-app-1.3.15.zip` | `48dd7173dbf79baafd2bb7ede2770cbebb58527967a11e4f26600fba71311763` |
| `dist/supports.json` | `fb75bd97c4efb91a085fdb774c97d11bf85d25a025a26933f1d0fc2407b6b3fb` |

Verify a download: `sha256sum -c SHA256SUMS --ignore-missing` (Linux) or `shasum -a 256 -c SHA256SUMS --ignore-missing` (macOS).
