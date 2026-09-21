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

## Updating FloofyCrew itself

FloofyCrew checks for its own updates **at most once a day**: `floofy doctor`,
`floofy status`, the interactive `floofy` and the App compare the running
`floofy_core.__version__` with the newest release for your edition and channel —
the public edition reads the GitHub releases of the FloofyCrew repository, the
internal edition the `FloofyCrew` package's `RELEASE.md` on `mainline` — and show
one line only when that release's *supports* list names your running host
version:

```
update: FloofyCrew 1.2.0 is available and supports your host 0.7.0.5 (internal/beta); you run 1.1.0: `floofy self-update` — release notes: …
```

The check is a single HTTPS `GET` that sends nothing identifying beyond itself
(no query parameters, no host facts, a generic `User-Agent`; an edition adapter
with a network identity adds the same session cookie its registry uses). Its
result is cached under
`floofy/cache/self-update.json` — `{checkedAt, edition, channel, latest{version,
supports, url, sha256}, notice}` — so no command waits for it twice a day; an
unreachable endpoint is cached as `unreachable` and stays silent until the next
day, and the check never blocks a command for more than a few seconds. It is
skipped with `--offline` (a cached notice may still show) and switched off with
`floofy config set updates.check false` (`floofy config get` shows the setting;
`self-update --check` asks anyway, because then you are asking).

`floofy self-update` installs that release through the edition's installer path
with the same verification as a fresh install: it downloads the release's
`SHA256SUMS`, then `floofy.pyz` and the Loader app archive (the GitHub release
assets; the package's `dist/` blobs at the tag `v<version>` on the internal
edition), refuses anything whose digest does not match — the release notes'
own table must agree with `SHA256SUMS` too — and then **swaps the installed
zipapp atomically**: the new file is written beside the old one as
`floofy.pyz.new` and renamed over it, so the `~/.local/bin/floofy` wrapper runs
the new release from its next invocation. The CLI is not the gateway, so this
never disturbs a running one. The Loader app archive is **staged** under
`floofy/pending/self-update/` (with a marker `pending/self-update.json`) and
installed through the host App Kit — the same `kirocrew app install` path
`floofy init` uses — the next time `floofy apply` runs without a gateway (the
re-apply trigger runs it before a gateway starts and hourly), or right away with
`floofy self-update --now`. `doctor` and `status` say when a Loader app update is
staged; every step writes an `op: self-update` audit row. A release that does
not list your host is refused unless you pass `--force`; a `floofy` running from a
checkout or a package install is told to update the way it was installed
(`--target PYZ` names an installed zipapp explicitly).

In the manager App the same notice carries an **Update & restart** button
(1.1.6): it runs `self-update --now` through the App's typed route — the CLI's
own "Install FloofyCrew X over Y?" question appears in the App's modal — and,
once the Loader app is installed, asks once more and restarts the gateway
through `POST /host/restart`, which hands the restart to the host's own
`kirocrew restart` (service-aware: a systemd/launchd unit is restarted by its
service manager, a foreground gateway is stopped and a detached replacement
started and verified). The dashboard reloads when the new gateway answers. The
same red **Restart KiroCrew** button sits in the App's header, on the "staged"
line of the Mods page and in Settings, for staged installs and vanilla requests;
every restart is an `op: host-restart` audit row. The CLI keeps its stance: a
`floofy` command never restarts a live gateway by itself.

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
Covers Requirements 6.1, 6.2, 6.3, 6.4, 6.5, 6.6, 7.6, 7.7, 9.2, 11.6.
