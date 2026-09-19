# docs

Public, edition-neutral documentation for FloofyCrew users and mod authors: the
`floofy.json` manifest reference, the seam guide (which kind lands where and why),
the Python and SPA APIs with the deprecation policy, the patch and fingerprint
authoring guide with the reporter, publishing to a registry, and the consent and
trust model. Everything here ships with the public repository, so it must not
mention internal systems, hostnames or people — CI scans this directory with
`scripts/check_no_internal_identifiers.py`; internal-only notes (the build desk,
the internal registry, internal provisioning) belong under `forge/docs/` or the
internal edition adapter directory.
