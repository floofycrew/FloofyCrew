# Authoring patches and fingerprints

A `patch` part is the only kind that modifies host **payload** files (a payload
is one installed copy of the host on disk — a version directory, a venv, a
desktop bundle; one machine may hold several). Reach for it last
([seams.md](seams.md)): the Patcher makes it reversible, idempotent and
self-verifying, but every host release can move the bytes a patch matches, and
the reporter is what tells you first.

## The descriptor

```json
{
  "schema": 1,
  "target": "kiro_crew/static/dist/assets/useTheme-*.js",
  "mode": "import-map",
  "find": "mc-custom-theme-",
  "description": "why this patch exists and what it changes",
  "appliesTo": ">=0.7.0 <0.9.0",
  "fromBuild": "0.7.0",
  "toBuild": "0.9.0",
  "ops": [
    {"op": "replace", "regex": true,
     "fingerprint": "(?<=\\.has\\([a-z]\\)\\|\\|)[A-Za-z_$][\\w$]*\\([A-Za-z_$][\\w$]*\\)(?=\\}\\},\\[)",
     "content": "void 0", "marker": "||void 0}},[", "description": "theme reset guard"}
  ]
}
```

- `target` — the payload-relative file. Host Python sources, the launcher and
  the install bookkeeping files are refused (engineering rule); the host's
  governance files are allowed but flagged governance-altering and need a
  per-file typed confirmation at install ([consent-and-trust.md](consent-and-trust.md)).
  A glob on a hashed name (`useTheme-*.js`) must resolve to exactly one file — a
  fingerprint on the *name*, never on the hash.
- `appliesTo` — a range on the host's `X.Y.Z` base; `fromBuild` (inclusive) /
  `toBuild` (exclusive) gate on the full host version, Vencord-style. Outside
  the gates the descriptor is `NotApplicable` and simply not applied.
- `find` — an optional literal that must occur in the target text before any op
  applies: module identification, the way Vencord's `find` names a module.
- `ops[]` — in order: `insert-before`, `insert-after`, `replace` (literal
  `content`, no group references) each need a `fingerprint` that matches
  **exactly once** (a literal, or a regex with `regex: true`; `occurrence:
  first|last` names one of a class of locations, e.g. "the first `<script`");
  `append-head` needs only `content`. `marker` is a literal whose presence means
  the op is already applied; without one the op's own `content` is the marker.

**Per-op atomicity.** An op either applies fully or is skipped with a typed
diagnostic naming the file and the op index — `NotApplicable`, `AlreadyApplied`,
`FingerprintMiss` (zero matches), `FingerprintAmbiguous` (several). A skip never
partially rewrites; the other ops still apply. Write fingerprints against
**structure** (`.has(x)||f(y)` with identifier classes), never against a
minifier's chosen identifier, and give every op a `marker` so a re-run is a no-op.

## Two ways to reach a hashed chunk

Hashed assets under `/assets/` are served immutable: a client that ever loaded
the app never refetches a cached name, and lazy chunks are imported by their
hashed names from other immutable chunks. So a chunk can never simply be edited
in place. The Patcher offers two paths; choose with `mode`.

**`mode: "import-map"` (preferred, design spike 1.4).** The target chunk stays
untouched. The Patcher writes a patched copy with rebased imports under the
Loader app's `ui/patched/<chunk>` (data home, served `no-cache`, survives host
updates as a file) and adds one key to the host's inline import map in
`index.html` — `"/assets/<chunk>": "/apps/floofycrew/ui/patched/<chunk>"` —
dropping the chunk's `modulepreload` hint in the same op. Every importer,
including dynamic `import()`, evaluates the copy. No alias names, no sidecar
sidelining; the *mapping* is re-derived per host version.

**In place + alias graph (fallback).** For a target the import map cannot reach
— the entry chunk loaded by `<script src>`, `index.html` itself, a
non-JavaScript asset — the Patcher edits the file, then republishes the chunk's
**importer closure** under new names `<stem>-<runtag>-floofy.<ext>` with member
references rebased, repoints the never-cached `index.html` (`<script src>`,
`modulepreload`, import-map values) at the aliases, and sidelines the
pre-compressed `.br` / `.gz` sidecars of every file patched in place (a server
preferring them would serve stale compressed bytes). One `runtag` (digest of the
patched bytes) names the set, so re-runs are stable and `restore` sweeps
`*-floofy.*` and the backups.

## Electron shell targets: `electron:<member>`

The desktop app's main-process code — the pet overlay, the app windows, the
tray — ships inside the shell's `app.asar` (`KiroCrew.app/Contents/Resources/`
on macOS, `resources/` on Linux and Windows), where no import map, hook or
route reaches it. A descriptor whose `target` is `electron:<path inside
app.asar>` patches one member of that archive:

```json
{"schema": 1, "target": "electron:mochi/petOverlays.js", "find": "function createOverlayForDisplay(",
 "ops": [{"op": "insert-after", "fingerprint": "acceptFirstMouse: true,\n    webPreferences: {",
          "content": "\n      partition: \"persist:mochi-pet\",\n      zoomFactor: 1.0,", "marker": "partition: \"persist:mochi-pet\""}]}
```

Declare the part with `"side": "electron"` so the manager describes it as a
desktop-shell change (the validator warns on a mismatch). The ops, fingerprints
and markers are the ordinary ones; what differs is the container:

- **The archive is the file.** The Patcher reads `app.asar`, applies every
  `electron:` descriptor to its member's text, re-lays the archive (later offsets
  move, the member's `integrity` record is recomputed) and commits it through the
  same path as any other target — `app.asar.floofybak` beside it, original and
  patched hashes in the deployment manifest, drift classification, revert-then-patch,
  `floofy restore --all` (the lost-manifest sweep covers the shell's `Resources/`
  directory too). An archive that no descriptor changes is never re-serialised.
- **The shell is found by walking up** from the payload's `kiro_crew` package to
  the nearest `Resources`/`resources` directory holding an `app.asar` — never
  sideways, never by searching the disk. A gateway-only payload (a version
  directory on Linux, a pipx venv) has no shell: every `electron:` descriptor is
  `NotApplicable` there and the mod changes nothing.
- **Fuses are read before writing.** Electron validates asar integrity only when
  the fuse `EnableEmbeddedAsarIntegrityValidation` is on; the Patcher reads the
  fuse wire from the shell's Electron binary and skips a locked shell with
  `IntegrityLocked` rather than leave an app that refuses to launch. A shell whose
  wire cannot be read is patched (the state is unknown, not locked).
- **A missing member** (an older or newer shell layout) is `NotApplicable` for
  that descriptor alone; a corrupt archive is an error and nothing is written.
- **Relaunch the desktop app** after `apply`: main-process code loads once per
  process. `floofy verify` cannot fetch the archive from the dashboard, so it
  reports the shell as checked by hash only; `floofy status` shows the drift
  class of `app.asar` like any patched file.

The archive is sealed by the bundle's code signature: rewriting it changes a
signed resource, exactly as the hand-verified fix a shell patch usually ports
did. That is a consented, logged and reversible change to the user's own install
([consent-and-trust.md](consent-and-trust.md)); nothing here touches the
signature of the main executable, and `restore` puts the sealed bytes back.

## What the Patcher records and how it stays honest

- **Deployment manifest** per payload at `<data home>/deploy/<payload-id>.json`:
  for every touched file its path, `sha256(original)`, `sha256(patched)`, the
  owning mod and part, the host version and a timestamp. Backups sit beside the
  file as `<name>.floofybak`; added files carry the `-floofy` name marker.
- **Drift classification** before every apply or remove — `clean` (our bytes),
  `host-updated` (the host re-laid the file or moved to another version: re-derive
  from the new original), `user-edited` (unknown bytes with our backup still
  present and no version change: **skip and report**, never overwrite),
  `mod-deleted` (gone).
- **Revert-then-patch.** `floofy apply` restores originals from the backups
  first and then re-applies the whole enabled set, so a half-applied state heals
  itself and disabling one mod removes exactly its effect.
- **Every payload present** is patched, not only the current one — the running
  gateway may still serve an older payload after an update; dormant payloads are
  marked in `floofy status`. Manifests and backups of payloads that vanished are
  garbage-collected.
- **Verify.** `floofy verify` compares the bytes the running gateway serves with
  the deployment manifest (over the dashboard socket or loopback) and tells a
  dormant payload ("not serving") from a failure; `--spa` adds the browser
  reporter, `--bundle` the offline fingerprint check. The served version is
  learned from `/api/health`, which the host answers with `version` only to a
  direct-local loopback TCP caller — never over the unix socket — so the probe
  asks the socket, then retries once over `127.0.0.1:<port>` (the port in the
  socket's name) and keeps the socket for everything else; a `--test-mode`
  gateway (`dashboard-0.sock`) counts as running with an unknown version.
- **Restore.** `floofy restore --all` returns every payload to vanilla by
  manifest and by glob sweep (`*.floofybak`, `*-floofy.*`, the Loader's
  `ui/patched/` and `ui/boot/`), and works when the manifest is lost.

## Fingerprints for surfaces and Python anchors

The same discipline applies to the two other fingerprint sets FloofyCrew keeps:
the SPA host's **surfaces** (`ui/surfaces.json`: content fingerprints for host UI
surfaces — DOM selectors over attributes that survive minification, or literals
in a chunk named by its stem; [spa-api.md](spa-api.md)) and the Loader's
**Python anchors** (`floofy_core/anchors.json`: import paths and route names the
hook registry relies on). Both carry the host build they were written against.

## The reporter, and what to do when it misses

`floofy verify --spa` runs the reporter in headless Chromium against the served
dashboard: every surface fingerprint and every recorded patch is re-evaluated
against the live document and the **original** chunk text, and the output is
the compatibility-matrix shape — `spaFingerprints {matched, total, missed[]}`,
with `pythonAnchors {matched, total}` from the anchor check. The Forge runs the
same on every new host release of both editions and writes the row; the manager
runs it when it detects a new host version ([updating.md](updating.md)).

When a fingerprint misses on a new build: diff the chunk against the previous
release (the stem is stable, the hash is not), find the structure your op
matched, and write a fingerprint that survives the minifier's choices — a class
of identifiers, a neighbouring literal, a `(?<=…)` / `(?=…)` anchor on syntax the
host cannot rename. Keep several fallback fingerprints when a site is volatile,
bump `fromBuild`, and let the smoke test in your mod (scaffolded by `floofy
new`) assert the op applies exactly once on the fixture bundle you commit.

---
Covers Requirements 4.4, 4.5, 5.2, 5.3, 5.4, 5.5, 5.6, 5.7, 5.8, 5.10, 5.11, 14.3.
