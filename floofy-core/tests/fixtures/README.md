# Fixture payloads

Trimmed `static/dist` trees from three real host versions, produced by
`scripts/make_fixture_payload.py` (task 3.7, Requirement 13.3):

| Fixture | Edition | Seed chunk | Closure | Bytes |
|---|---|---|---|---|
| `dist-0.6.0` | external (public stable wheel) | `scan-eye-DP8Wdilb.js` | 8 | ≈ 50 KB |
| `dist-0.7.0.4` | internal | `TunnelQrCard-B5ylGs4W.js` | 5 | ≈ 48 KB |
| `dist-0.7.0.5` | internal | `TunnelQrCard-C7TY_tEZ.js` | 5 | ≈ 48 KB |

Each holds the real `index.html` (its inline import map, `modulepreload` hints and
module entry tag are the Patcher's anchors), one real seed chunk with its `.br`/`.gz`
sidecars, *skeletons* of the seed's importer closure (one `import "./<name>";` per
chunk the original referenced, so `floofy_core.aliasgraph` computes the same graph)
and 0-byte stubs of everything else the shell references. `FIXTURE.json` records
the version, edition, seed, closure, per-file byte counts and the hash of the
vanilla shell — never a path.

Regenerate from a read-only source dist:

```bash
python scripts/make_fixture_payload.py --source <payload>/kiro_crew/static/dist --out floofy-core/tests/fixtures/dist-<ver>
```

When the source dist is not vanilla (another tool left aliases and backups beside
the host files), point `--index` at that tool's backup of `index.html` and
`--exclude` its file-name globs; the edition adapters' READMEs give the exact
commands for their installs. `tests/test_fixtures.py` drives every fixture through
apply → restore (byte-identical), idempotent double apply, every drift class, the
lost-manifest sweep and the alias closure recorded in `FIXTURE.json`.
