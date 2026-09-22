# The `floofy` command

`floofy` is the FloofyCrew mod manager: a standard-library-only Python 3.12
program that runs on the KiroCrew host's own interpreter, shipped as a single
file (`floofy.pyz`), a wheel, or the host-specific installer of each edition.
`floofy --version` prints the FloofyCrew release and the API version;
`floofy --help` and `floofy <command> --help` print what is below; bare `floofy`
on a terminal opens the interactive mode of [interactive.md](interactive.md) (without a
terminal it prints the usage). FloofyCrew is unofficial and says so in every banner.

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
| `--no-color` | plain output: no ANSI styling and no banner — styling is also off when stdout is not a terminal, when `NO_COLOR` is set (any value) and with `--json`; `--json` output is byte-identical with and without styling ([terminal.md](terminal.md)) |
| `--offline` | never contact a network endpoint: the daily FloofyCrew update check is skipped (a cached notice may still show); `self-update` refuses ([updating.md](updating.md)) |
| `--vanilla` | the next gateway boot runs with every mod disabled, once |

## Commands

| Command | What it does | Notable options |
|---|---|---|
| `init` | acknowledge the one-time warning (on a terminal: the full-screen `[ I AGREE ]` control — Enter agrees, Esc/`q` declines; elsewhere: type `I ACCEPT`), install the Loader app (through the host's App Kit) and the re-apply trigger; offers the `agent.apps_trusted` grant | `--i-accept-the-risk` (automation; recorded), `--reaccept`, `--loader-app PATH`, `--no-loader-app`, `--reinstall-loader`, `--no-grant`, `--no-trigger`, `--trigger KIND` (only `user-timer` / `path-wrapper`, repeatable — the install scripts' menu answer), `--early` (also the early shim) |
| `deinit` | remove the trigger(s), the early shim and the Loader app; restore every payload to vanilla | `--purge` (also the data home: mods, consent, audit log), `--keep-loader-app` |
| `doctor` | diagnose: host edition/version/channel/payloads (the served version learned over the dashboard socket, then loopback TCP), Loader state, early shim, triggers, governance warnings, consent record, drift per payload, the compat verdict for this host version, and the one-line FloofyCrew update notice (the daily cached check) | `--no-live` |
| `status` | installed mods with state, seam, compat badge, source tier, typed reason and warnings; dormant payloads marked; the FloofyCrew update notice when a newer release supports this host | `--no-live` |
| `list` | installed mods, one line each: version, enabled, source tier, source kind and reference, commit | |
| `info <id>` | everything about one mod — manifest summary, the install record (kind, reference, commit, link facts), the source tier and what it means, the compat verdict for this host, parts and files; for a mod that is only in the registry cache, its record and the tier an install would have | |
| `vanilla` | boot the host with every mod disabled, once (marker consumed by the next gateway start) | `--cancel` |
| `search [query]` | search the registry cache (`registry refresh` fills it); shows the newest version known to work here, the record shape and the source tier an install would have (`listed`, or `tested` on a `tested` matrix cell) | |
| `install <id[@version] \| path \| archive \| https URL \| git reference>` | disclose parts, seams, declared hosts, flags and the source tier; confirm code kinds; stage into `pending/` while a gateway runs, else install now; a confirmed install lands **enabled**. A **git reference** — `ssh://<host>/<path>[@<tag>][#<subdir>]`, `https://<host>/<owner>/<repo>[.git][@<tag>][#<subdir>]` — is shallow-cloned with your own git credentials (the default branch when bare; the version and host compatibility come from the checkout's `floofy.json`, never a tag), its `files[]` verified and `floofy validate` run on the checkout, and the disclosure gains the line *unlisted source: no curator review, no compatibility data*, confirmed by typing `I ACCEPT` ([installing-from-a-link.md](installing-from-a-link.md)); a registry link record is resolved through the same clone and held to the record's commit, manifest hash and files | `--now`, `--disabled` (land switched off for a later `floofy enable`), `--sha256`, `--ref BRANCH\|COMMIT` (git reference: pin a branch or commit), `--accept-unlisted-source` (automation; recorded — `--yes` never accepts the line), `--confirm-governance-target PATH` (per file, typed), `--accept-flags` |
| `uninstall <id>` | reverse the seam parts, restore recorded values, re-apply the patch set | `--now`, `--keep-config` |
| `enable <id>` / `disable <id>` | flip the flag, reload a running gateway, re-apply patches | `--no-reload` |
| `update [ids] [--all]` | move installed registry mods to the newest version the matrix knows to work on this host | `--check`, `--now`, `--accept-flags` |
| `dev <path>` | symlink a checkout into `mods/<id>`, enable host dev mode where applicable, stream its log | `--follow`, `--unlink`, `--no-reload` |
| `apply` | revert-then-patch every payload with the enabled mods' patches (what the re-apply trigger runs as `apply --if-changed`). `--if-changed` also keeps the App's update reminders fresh unattended: the daily FloofyCrew release check (its own 24 h cache) and a registry-source refresh at most once a day — best-effort, never failing the run, disabled together with `updates.check` | `--if-changed`, `--payload ID`, `--no-verify`, `--confirm-governance-target PATH` |
| `restore` | return every payload to vanilla by manifest and sweep — works without a manifest | `--all` (default), `--payload ID`, `--no-sweep` |
| `verify` | compare served bytes with the deployment manifests over the dashboard socket / loopback; tell dormant from failed | `--spa` (the browser reporter), `--bundle` (offline fingerprints), `--extra-surfaces`, `--timeout` |
| `validate <dir \| archive>` | schema, `files[]` hashes, parts, targets (engineering-rule targets rejected, governance files flagged), network scan | |
| `new <kind>` | scaffold a valid mod: manifest, README, LICENSE stub, smoke test, CI workflow | `--id`, `--name`, `--dir`, `--author` |
| `yeet [ids]` | park mods in the per-host-version quarantine, list it, or restore a parked set | `--list`, `--restore HOSTVER`, `--reason` |
| `hold` | pause the host's own auto-update — explicit and logged, never a default | `--duration`, `--release` |
| `profile {list,save,use,export,import}` | named mod sets with pinned versions (lockfiles); export/import as one file | `use`: `--now`, `--check`, `--confirm-governance-target PATH`, `--accept-unlisted-source` |
| `registry {add,remove,list,refresh,defaults,host-registry}` | sources and their trust, the local cache, the edition's default source (verified against the pinned key), the host App Store rows | `add`: `--trust`, `--allow-unsigned` (audited), `--public-key`, `--key-id`, `--name`, `--host-registry` |
| `self-update` | update FloofyCrew itself to the newest release that supports this host: a fresh check, the disclosure, one confirmation, `SHA256SUMS`-verified downloads, the atomic swap of the installed `floofy.pyz`, the Loader app archive staged under `pending/self-update/` and installed through the host App Kit when no gateway runs; `op: self-update` audit rows ([updating.md](updating.md)) | `--check` (report only), `--now` (install the Loader app update even while a gateway runs), `--force` (a release that does not list this host), `--target PYZ` |
| `config {get,set}` | FloofyCrew's own settings in `floofy/config.json`: `updates.check` (default `true`) — `floofy config set updates.check false` stops the daily check | |
| `audit` | read the audit log (every mutating operation) | `--tail N`, `--op OP` |
| `which <sha256>` | resolve a file's hash to `mod@version` (registry cache and installed mods) | |

## Exit codes and output

`0` success; `1` a refused or failed operation (the message says why); `2` a
usage error; `3` no consent record; `4` a confirmation was needed and the
surface could not ask (only the manager App's routes reach it — they answer
`409` with the question). With `--json` every command prints one document —
the same one the manager App reads: its typed routes run these commands
in-process with `actor: app`, and the read-only ones also answer `POST /cli`.

## Where things live

```
<host home>/floofy/
  consent.json      the acknowledgement          mods/<id>/           installed mods (+ .floofy/ runtime state: source.json — kind, reference, commit, tier)
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
Covers Requirements 6.1, 6.6, 7.1, 7.2, 7.4, 7.5, 7.6, 7.7, 8.4, 8.5, 8.8, 8.11, 14.1, 14.2.
