# Surviving host updates

KiroCrew updates itself: the internal edition drops a fresh version directory
every hour if one is available, the public edition promotes a shadow venv or a
new desktop bundle. Either way a new **payload** appears on disk and the old one
may keep serving until the gateway restarts. FloofyCrew's job is that an update
never silently strips your mods and never leaves a broken dashboard — on either
edition, without holding the update back.

## Re-apply triggers

`floofy init` installs the triggers that run `floofy apply --if-changed` after a
new payload lands; `floofy deinit` removes them; `floofy doctor` shows their
state.

| Trigger | Where | Both editions? |
|---|---|---|
| hourly user timer | a systemd user timer at minute 55 on Linux (after the internal edition's hourly auto-update at :50), a launchd agent on macOS | yes |
| Loader `on_startup` | the Loader app re-applies the enabled patch set on every gateway start (a re-apply trigger by itself once the Loader is installed) | yes |
| `kirocrew` PATH wrapper | `~/.local/bin/kirocrew` runs `floofy apply --if-changed` and then executes the real launcher — for venv installs whose launcher moves with the version | public edition |

`apply --if-changed` compares discovery with `host-state.json` and returns in a
few milliseconds when nothing changed; a quiet hour costs one payload scan.

## What happens when a new host version is detected

In the order the requirement lists:

1. **Reporter and anchors.** The SPA fingerprints are checked offline against
   the new payload's bundle and the Python anchors against its package — the
   compatibility-matrix shape (`spaFingerprints`, `pythonAnchors`), recorded for
   the manager page and for the Forge ([patching.md](patching.md)).
2. **Matrix.** The cached `compat.json` row for `edition × channel × hostVersion`
   is consulted: does the framework report `loader: ok` here, and what does each
   installed `mod@version` grade as — `tested`, `expected`, `broken`?
3. **Re-apply.** Revert-then-patch every payload with the enabled set:
   fingerprints that still match land; the others are reported as skips, never
   half-applied.
4. **Yeet.** Mods whose manifest (`kirocrew.version` with `strict: true`), matrix
   cell (`broken`) or own fingerprints (a miss on their own patch) do not cover
   the new version — plus anything the Loader asked to quarantine — move to
   `<host home>/floofy/quarantine/<hostver>/<id>/`, where `<hostver>` is the
   version they last worked on. Their seam parts are uninstalled, their enabled
   flag is preserved with them, and they carry the reason `Quarantined`. A
   **non-strict** mod whose range excludes the new version is *not* yeeted — its
   manifest says "load anyway with a warning", so it is loaded with the warning.
5. **Record.** The outcome is written to `hostchange-<hostver>.json` for the
   manager page and `host-state.json` is updated. The Loader publishes
   `host.version_changed` on its event bus.

The word *yeet* is borrowed from BSIPA, the Beat Saber mod loader, whose
per-version `Old <ver> Plugins/` directory solved the same problem years ago.

## Rollback

When the host is rolled back (the internal edition's rollback, an older venv
promoted again) the new current version names a quarantine directory: that set
is restored first — files back, seam parts reinstalled, flags restored — before
the ordinary re-apply. By hand: `floofy yeet --list`, `floofy yeet --restore
<hostver>`, `floofy yeet <id> --reason "…"` to park a mod yourself.

## Every payload, not just the current one

The running gateway may serve an older payload for hours after a new one was
installed. FloofyCrew therefore patches **every** payload present, marks the
ones not serving as *dormant* in `floofy status` and `floofy verify`, and
garbage-collects the manifests and backups of payloads that no longer exist.

## Updating mods

`floofy update --check` lists what would change; `floofy update --all` moves
every registry-installed mod to the newest version the matrix knows to work on
this exact host version (`tested` beats a newer `expected`; `broken` and
out-of-range versions are never picked). While a gateway runs, installs are
staged into `pending/` and applied at the next gateway start — the manager page
says "restart to apply" — unless you pass `--now`, which installs immediately
and reloads the running Loader.

## Holding the host back — explicitly

FloofyCrew never pauses host updates on its own. `floofy hold [--duration 7d]`
exposes the host's **own** pause mechanism as an explicit, logged action (an
audit row with the duration), and `floofy hold --release` resumes. Use it when a
release breaks a mod you rely on and the Forge has not shipped the repair yet;
the matrix row for the new version tells you when it has.

## Getting back to vanilla

`floofy --vanilla` boots the host once with every mod disabled (the marker is
consumed by the next gateway start). `floofy restore --all` returns every
payload to vanilla by manifest and by sweep, even when a manifest is lost.
`floofy deinit` removes the triggers, the early shim and the Loader app and
restores every payload; `--purge` also deletes the data home.

---
Covers Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.6, 9.2.
