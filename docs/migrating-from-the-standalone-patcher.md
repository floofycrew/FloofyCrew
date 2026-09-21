# Migrating from the standalone theme patcher

For the owner of an install that was patched by the standalone
`patch_kirocrew_branding.py` (the ancestor of FloofyCrew's Patcher): how to hand
the Rimuru branding over to the `rimuru-branding` mod. Parity was verified on a
scratch copy of 0.7.0.5 (`mods/rimuru-branding/PARITY.md`); swapping a live
install is a deliberate, owner-run step — nothing in the repository does it for
you. Install-specific paths (where the standalone patcher's checkout lives, which
payloads it patched) are in the spec's `BLOCKERS.md` entry for 5.5.

## Before you start

* The standalone patcher and FloofyCrew must not both hold the same payload:
  the old aliases (`*-o3r.js`, `*.o3rbak`) and FloofyCrew's backups
  (`*.floofybak`) would interleave and `restore` on either side would only undo
  its own. Restore with the standalone script **first**.
* A dormant payload (an older version directory the launcher no longer runs) is
  still patched, and FloofyCrew patches every payload present (Requirement
  6.4), so every version directory matters, not only the current one.
* The theme pack in the host's theme store (`<host home>/themes/rimuru/`) is the
  same pack the mod ships (`mods/rimuru-branding/theme/`); the mod's `theme`
  part re-installs it, so nothing is lost if the store copy is removed.

## Checklist

1. Stop the running dashboard/gateway so no process serves half-restored files.
2. Restore every payload with the standalone script's own commands (it sweeps
   `*.o3rbak` and `*-o3r.*` in every version directory it discovers):

   ```bash
   python <patcher>/bin/patch_kirocrew_branding.py restore
   python <patcher>/bin/patch_kirocrew_branding.py status
   ```

   `status` must list no `PATCHED` chunk and no `backup:` line for any assets
   directory. Spot-check: no file named `*-o3r.*` or `*.o3rbak` remains under
   any payload's `kiro_crew/static/dist/`.
3. Install the Loader app and initialise FloofyCrew (task 6.1 ships `floofy
   init`; until then the recipe in `loader-app/README.md` "Dev-host recipe",
   pointed at the live home): consent, the `agent.apps_trusted` grant, the
   re-apply trigger.
4. Install and enable the two mods — `floofy install mods/custom-themes --now`
   then `floofy enable custom-themes` (the first frame and the theme-reset guard
   for every custom theme), then `floofy install mods/rimuru-branding --now` and
   `floofy enable rimuru-branding` (the pack; since 2.0.0 it has no code parts of
   its own and depends on the former).
5. Keep the theme selected: `dashboard.theme_color` in `<host home>/config.json`
   should read `custom-rimuru` (the boot script's cold-client fallback is
   captured from there at patch time).
6. Verify: `python -m floofy_core.cli_patch verify` (served bytes match the
   manifest) and `python -m floofy_core.cli_spa verify --spa --port <port>
   --token <token>` (24/24 fingerprints on 0.7.0.5), then reload the dashboard:
   the first frame is already the Rimuru palette, the sidebar shows the slime
   logo and "GREAT SAGE", the favicon is the slime.
7. Retire the standalone patcher: archive its checkout and remove any scheduled
   job or shell alias that re-ran `patch` after host updates — FloofyCrew's
   re-apply trigger (Loader `on_startup` + the hourly timer from 6.4) replaces it.

## Rolling back

`python -m floofy_core.cli_patch restore --all` returns every payload to vanilla
(manifest first, then the `*.floofybak` sweep, then the Loader app's
`ui/patched/` and `ui/boot/`); the theme pack stays in the store and the
standalone script can be re-run if ever needed.


Today the commands in steps 3–6 are `floofy init`, `floofy install
mods/rimuru-branding --now` (then `floofy enable rimuru-branding`) and `floofy
verify --spa`; the module-level invocations above still work and are what the
Loader runs underneath ([cli.md](cli.md)).

---
Covers Requirements 2.6, 5.4, 5.8.
