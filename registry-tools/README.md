# registry-tools

Tooling for the mod registries: build `index.json` from mod repositories and
release assets, produce and verify the detached signature over canonical JSON,
validate submissions in CI (schema, `sha256` of every file, `floofy validate`,
licence present, no host-name masquerade) and emit the KiroCrew-compatible
`app-registry.json` for app-kind mods. The same tooling drives both registries —
the public GitHub repository and the internal one — so it takes the source name,
endpoint and signing key as parameters and must not hard-code either registry's
identity. Edition-neutral: CI scans this directory for internal identifiers.
