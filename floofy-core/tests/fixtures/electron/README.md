# Electron shell fixtures

`app.asar` is a reference archive packed by `@electron/asar` 3.4.1
(`createPackageWithOptions(src, out, {dot: true})`) from the tree in `src/`, so the
Python reader in `floofy_core.asar` is tested against bytes the real tool wrote
(pickle framing, string offsets, per-file `integrity` records, UTF-8 header,
binary member). `src/` holds the same files as plain text for the tests that
rebuild an archive with the Python packer and compare member by member.

`src/mochi/petOverlays.js` and `src/crew-companion/petOverlay.js` are **synthetic**
stand-ins written for FloofyCrew: they carry only the few lines the
`mochi-pet-zoom-fix` mod's fingerprints anchor on, in the upstream shape, plus
decoys (a second `loadURL`, a second `webPreferences` block) that must not match.
They are not copies of the host's files.
