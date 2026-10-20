# Third-party notices

FloofyCrew is licensed under the MIT License (see `LICENSE`). It is an
unofficial project, not affiliated with Kiro or KiroCrew. The portions listed
below are derived from KiroCrew and remain licensed under the Apache License,
Version 2.0 (`LICENSES/Apache-2.0.txt`); everything else is original.

## Derived from KiroCrew (Apache License 2.0)

KiroCrew — <https://github.com/kirodotdev/KiroCrew> — is licensed under the
Apache License, Version 2.0. Its NOTICE file's attribution, reproduced as the
license requires:

```
KiroCrew
Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

This product includes software developed at
Amazon.com, Inc. (https://www.amazon.com/).
```

(The remainder of the upstream NOTICE concerns components — bundled fonts, the
macOS computer-use path, the shell-command classifier — that FloofyCrew does
not include or derive from.)

What FloofyCrew derives from KiroCrew, in every distribution (the wheel, the
zipapp, the Loader app, both release trees):

- **`floofy_core/themes.py`** — the theme-pack validation is a Python port of
  KiroCrew's install-time theme validator (`kiro_crew/dashboard/theme_validate.py`).
  The module's docstring states what was ported and what was changed, as the
  license asks for modified files.
- **Short verbatim excerpts of KiroCrew source** quoted as *anchors*: the
  `find`/`fingerprint` strings of patch descriptors (for example
  `mods/mochi-pet-zoom-fix/patches/`), the chunk fingerprints in
  `floofy_core/anchors.json`, and equivalent strings in test fixtures. Each is a
  few lines, used to locate and verify a patch site; FloofyCrew never
  redistributes KiroCrew itself.

## Everything FloofyCrew attaches to stays where it is

FloofyCrew never ships, rebuilds or embeds a KiroCrew host. It attaches to the
KiroCrew the user already has; the Patcher's changes live in the user's data
home and are reversible (`floofy restore --all`). "Kiro" and "KiroCrew" are
used only to name the software FloofyCrew mods; FloofyCrew does not use their
branding as its own.
