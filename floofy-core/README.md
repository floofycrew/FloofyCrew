# floofy-core

The shared core of FloofyCrew, published as-is to both editions: the `floofy.json`
manifest schema and validator, the dependency resolver, the Patcher engine, the
registry client and signature verification, the compatibility-matrix format, the
Loader app runtime and the SPA host. Everything here is stdlib-only Python 3.12
(plus ES modules for the dashboard side) and must run unchanged on the host's own
interpreter. It must not contain any edition-specific value — no internal
hostnames, package names, account identifiers or people — those belong in
`editions/*`; CI enforces this with `scripts/check_no_internal_identifiers.py`.
Tests live in `tests/`.
