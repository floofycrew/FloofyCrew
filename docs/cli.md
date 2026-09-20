# The `floofy` command

`floofy` is the FloofyCrew mod manager: a standard-library-only Python 3.12
program that runs on the KiroCrew host's own interpreter, shipped as a single
file (`floofy.pyz`), a wheel, or the host-specific installer of each edition.
`floofy --version` prints the FloofyCrew release and the API version;
`floofy --help` and `floofy <command> --help` print what is below. FloofyCrew is
unofficial and says so in every banner.

## Global options

| Option | Meaning |
|---|---|
| `--home DIR` | the host data home (default `$KIROCREW_HOME` or `~/.kiro/crew`); FloofyCrew lives in `<home>/floofy` |
| `--json` | machine-readable output (implies non-interactive) |
| `--yes`, `-y` | answer ordinary confirmations with yes — **never** the consent warning or a governance-altering file confirmation |
| `--root DIR` | an extra payload root to search (repeatable) |
| `--no-adapters` | search only `--root` directories and this interpreter (no edition adapters) |
| `--kirocrew PATH` | the host's `kirocrew` launcher (default: `PATH`, then the payload's own) |
| `--port PORT`, `--token TOKEN` | a running gateway's loopback port and a dashboard session token (default: the dashboard socket in the host home, a minted token) |
| `--quiet`, `-q` | less prose |
| `--vanilla` | the next gateway boot runs with every mod disabled, once |

## Commands

| Command | What it does | Notable options |
|---|---|---|
| `init` | acknowledge the one-time warning, install the Loader app (through the host's App Kit) and the re-apply trigger; offers the `agent.apps_trusted` grant | `--i-accept-the-risk` (automation; recorded), `--reaccept`, `--loader-app PATH`, `--no-loader-app`, `--reinstall-loader`, `--no-grant`, `--no-trigger`, `--early` (also the early shim) |
| `deinit` | remove the trigger(s), the early shim and the Loader app; restore every payload to vanilla | `--purge` (also the data home: mods, consent, audit log), `--keep-loader-app` |
| `doctor` | diagnose: host edition/version/channel/payloads, Loader state, early shim, triggers, governance warnings, consent record, drift per payload, the compat verdict for this host version | `--no-live` |
| `status` | installed mods with state, seam, compat badge, typed reason and warnings; dormant payloads marked | `--no-live` |
| `vanilla` | boot the host with every mod disabled, once (marker consumed by the next gateway start) | `--cancel` |
| `search [query]` | search the registry cache (`registry refresh` fills it); shows the newest version known to work here | |
| `install <id[@version] \| path \| archive \| https URL>` | disclose parts, seams, declared hosts and flags; confirm code kinds; stage into `pending/` while a gateway runs, else install now; code mods land disabled | `--now`, `--enable`, `--sha256`, `--confirm-governance-target PATH` (per file, typed), `--accept-flags` |
| `uninstall <id>` | reverse the seam parts, restore recorded values, re-apply the patch set | `--now`, `--keep-config` |
| `enable <id>` / `disable <id>` | flip the flag, reload a running gateway, re-apply patches | `--no-reload` |
| `update [ids] [--all]` | move installed registry mods to the newest version the matrix knows to work on this host | `--check`, `--now`, `--accept-flags` |
| `dev <path>` | symlink a checkout into `mods/<id>`, enable host dev mode where applicable, stream its log | `--follow`, `--unlink`, `--no-reload` |
| `apply` | revert-then-patch every payload with the enabled mods' patches (what the re-apply trigger runs as `apply --if-changed`) | `--if-changed`, `--payload ID`, `--no-verify`, `--confirm-governance-target PATH` |
| `restore` | return every payload to vanilla by manifest and sweep — works without a manifest | `--all` (default), `--payload ID`, `--no-sweep` |
| `verify` | compare served bytes with the deployment manifests over the dashboard socket / loopback; tell dormant from failed | `--spa` (the browser reporter), `--bundle` (offline fingerprints), `--extra-surfaces`, `--timeout` |
| `validate <dir \| archive>` | schema, `files[]` hashes, parts, targets (engineering-rule targets rejected, governance files flagged), network scan | |
| `new <kind>` | scaffold a valid mod: manifest, README, LICENSE stub, smoke test, CI workflow | `--id`, `--name`, `--dir`, `--author` |
| `yeet [ids]` | park mods in the per-host-version quarantine, list it, or restore a parked set | `--list`, `--restore HOSTVER`, `--reason` |
| `hold` | pause the host's own auto-update — explicit and logged, never a default | `--duration`, `--release` |
| `profile {list,save,use,export,import}` | named mod sets with pinned versions (lockfiles); export/import as one file | |
| `registry {add,remove,list,refresh,defaults,host-registry}` | sources and their trust, the local cache, the edition's default source (verified against the pinned key), the host App Store rows | `add`: `--trust`, `--allow-unsigned` (audited), `--public-key`, `--key-id`, `--name`, `--host-registry` |
| `audit` | read the audit log (every mutating operation) | `--tail N`, `--op OP` |
| `which <sha256>` | resolve a file's hash to `mod@version` (registry cache and installed mods) | |

## Exit codes and output

`0` success; `1` a refused or failed operation (the message says why); `2` a
usage error; `3` no consent record. With `--json` every command prints one
document — the same one the manager page reads through the Loader's `/cli`
route, which runs an allowlisted subset of these commands in-process.

## Where things live

```
<host home>/floofy/
  consent.json      the acknowledgement          mods/<id>/           installed mods (+ .floofy/ runtime state)
  enabled.json      per-mod flags                pending/             staged installs and removals
  deploy/<payload>.json  deployment manifests    quarantine/<hostver>/  yeeted mods per host version
  cache/            registry index + compat      profiles/            named sets, floofy.lock.json
  registries.json   sources and trust            audit.jsonl          every mutating operation
  host-state.json   last seen payloads/version   early.json, early.log  the early shim's list and log
```

The Loader app itself is installed under `<host home>/apps/floofycrew/` by the
host; FloofyCrew never writes under a host payload root except through the
Patcher, with a manifest and backups ([patching.md](patching.md)).

---
Covers Requirements 6.1, 6.6, 7.1, 7.2, 7.4, 7.5, 7.6, 8.4, 8.5, 14.1, 14.2.
