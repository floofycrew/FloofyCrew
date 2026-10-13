# agent-backend-doctor

Finds, un-caches and repairs KiroCrew's agent-backend harnesses — Claude Code,
Codex, OpenCode, pi — so that *"I installed it and KiroCrew still says missing"*
stops being a thing. A FloofyCrew mod: a `python-hook` in the gateway plus its
own page in the FloofyCrew App. Unofficial; not affiliated with Kiro or KiroCrew.

## The three host gaps it covers

All verified in the installed 0.7.0.5 and 0.8.0.6 builds, all reached through
`ctx.hooks.after` (docs/python-api.md) and feature-detected with `getattr`.

1. **Discovery.** The host resolves the Node ACP adapters (`claude-agent-acp`,
   `codex-acp`, `pi-acp`) through an env override, a vendored `node_modules`,
   mise, and a fixed list of bin directories (`~/.local/bin`, `~/.npm-packages/bin`,
   the CLI tooling's bin, mise / asdf / nvm / fnm, Homebrew, `/usr/local/bin`)
   plus the gateway's inherited `PATH`. An npm global prefix outside that list is
   invisible even when `node` itself was found — `~/.local/node` (npm from a
   tarball), `~/.npm-global`, a `prefix=` in `~/.npmrc`, `$NPM_CONFIG_PREFIX` —
   so `npm i -g` "succeeds" and the backend switch still reads *missing, run
   npm i -g …*. The hook sits `after` `acp.client._resolve_node_adapter_argv`:
   when the host answers `(None, searched)` it looks in exactly those prefixes for
   `<package>/dist/index.js` with `@agentclientprotocol/sdk` beside it (the host's
   own completeness rule) and answers `[<that tree's node>, entry]`. The
   plain-binary ladders (`opencode`, `pi`, 0.8's self-served table) get the same
   treatment for a `bin/<name>` launcher under those prefixes.
2. **The cached negative.** The spawn path resolves each adapter once per process
   behind an `_UNRESOLVED` sentinel and never invalidates it; the host's own probe
   only *reports* `restart_required`. On activation and on the page's
   *Re-resolve* the mod resets a cached miss (through
   `agent_sdk.forget_for_recheck` where the host has it, else by the same sentinel
   write fenced with `bump_resolution_generation` when present) and drops the probe
   cache, so the next session resolves fresh and `/api/acp-backends` flips to
   installed without a gateway restart.
3. **Codex's AWS credentials inside the sandbox** (`hook/awsenv.py`). A Codex
   whose `~/.codex/config.toml` says `model_provider = "amazon-bedrock"`
   authenticates through the AWS SDK's profile chain. Two host behaviours break
   that chain for the `codex-acp` child: Codex is a tool-gate-enforced harness,
   so its sandbox hides `~/.aws` (the host re-exposes `~/.aws/config` read-only,
   but only at the default location), and on the internal edition the
   credential broker that wraps every non-Claude spawn hands the child
   `AWS_CONFIG_FILE` / `AWS_SHARED_CREDENTIALS_FILE` pointers at a per-process
   copy of your config with every credential-fetching key stripped, plus
   `AWS_PROFILE=default`. The profile survives by name only, and the SDK says
   *failed to load AWS credentials: the credential provider was not enabled*.

   Before each Codex launch (`after` `AcpClient.__init__`, which both transports
   read the child's environment from; re-asserted `after`
   `CodexHarness.apply_spawn_env` on 0.8's runtime path) the mod runs a
   **preflight**: reads `config.toml` (never `auth.json`), selects the named
   profile plus its `source_profile` / `sso_session` chain from `~/.aws/config`
   with every static key dropped (names reported, values never read into the
   result), writes `<host home>/codex-aws/{config,credentials}` — `0700` dir,
   `0600` files, outside `~/.aws`, visible through the codex mask, the
   credentials file deliberately empty — checks both files exist, are regular,
   private and writable, and hands exactly `AWS_CONFIG_FILE`,
   `AWS_SHARED_CREDENTIALS_FILE`, `AWS_PROFILE`, `AWS_DEFAULT_PROFILE` to the
   child, replacing whatever the broker or a parent shell put there. A preflight
   that cannot pass (`profile_missing`, `chain_broken`, `static_only`,
   `no_credential_source`, `files_bad`) leaves the environment untouched and
   says why; `not_bedrock` is a pass with nothing to do.

   **What it refuses to do.** `~/.aws` is never exposed, the sandbox is never
   weakened, no static credential is ever copied, and no credential value or
   `credential_process` output is logged. A `credential_process` whose command
   line names a directory the mask hides gets a hint (`process_may_need_hidden_dir`),
   not a verdict: on the test box the profile's vend talks to a system daemon
   over a socket outside the home and works under the mask, which is exactly
   what the sandboxed probe below is for. When a vend does fail inside the
   child, the class is `credential_process` and the page names the manual
   action instead of papering over it.

## The page

One row per backend the build knows: the host's verdict (installed / missing /
unknown, with its install command and the restart flag), where the adapter or
binary is (on the host's search path, or found by the doctor and under which
rung), the CLI the adapter needs (`claude` via `CLAUDE_CODE_EXECUTABLE`, the
Codex binary via `CODEX_PATH`, `pi`), the gateway's cache state (not resolved /
resolved / cached not-found) and the models the adapter advertised on its last
session. Plus the host version, the hooks' state, the npm prefixes checked and
the host's full search path.

Two opt-in repairs:

- **Codex binary** — `codex-acp` ships its own Codex and runs a different one only
  when `CODEX_PATH` names it; the host leaves that variable alone. The switch
  exports it in the gateway process (every adapter child inherits `os.environ`),
  defaulting to the `codex` on the host's own search path; persisted in the mod's config, re-applied
  on activation, restored on deactivation.
- **Make it permanent** — symlink the found launchers (and `node` when the host's
  search misses it) into your own `~/.local/bin`, which the host does search, so
  the fix outlives the mod. Behind an in-App confirmation; never overwrites an
  existing path; nothing in the KiroCrew install changes.

## Routes (for the page; `floofy.mod("agent-backend-doctor").routes`)

| Route | What |
|---|---|
| `GET status` | the whole picture above (probes run off the event loop) |
| `POST reresolve {backend?}` | reset the cache(s) on the loop, then re-probe; all non-kiro backends when no id is given |
| `POST codex-path {enabled, path?}` | export / drop `CODEX_PATH`; 400 when the path is not an executable file |
| `POST symlink {confirm: true, entries: [{name}]}` | create `~/.local/bin/<name>` links from the current repair plan |
| `GET codex-awsenv` | the Codex ↔ AWS picture: codex config facts, the selected profile chain (key names only), file states, the last preflight, recent launches |
| `POST codex-awsenv/rebuild` | delete and re-create the isolated pair now |
| `POST codex-awsenv/probe {sandboxed: true}` | `aws sts get-caller-identity` with exactly the child's environment inside the child's own credential mask; only the exit status and Account / Arn are kept. Refuses to run unmasked when the sandbox cannot be built |
| `POST codex-awsenv/toggle {enabled}` | switch the injection off (children get the host's environment untouched) or on |

### For other mods (`sys.modules["floofy_mods.agent-backend-doctor.hook"]`)

`codex_launch_record(session_key)` — what the child of that session was handed
(`applied`, `preflight`, `profile`, `credential_source`, `superseded`, …);
`repair_codex_awsenv(session_key, reason, client)` — re-create the pair and
re-point a client's `_extra_env` (the agent-backend-router's one repair before
its one retry); `classify_codex_failure(message, transient=, reached_model=)` —
the redacted failure category (`credentials` / `credential_process` /
`endpoint_auth` / `throttle` / `timeout` / `model` / `server` / `stream_cleanup` /
`http` / `transient` / `error`) and whether it is repairable;
`backend_ready(backend)` (0.2.1) — would a NEW session on that backend start
right now: the host's install probe, this mod's discovery when the host says
missing, the spawn path's cache (`needs_forget`: a cached "not found" the
caller clears with `forget()` on the event loop before starting), and for
Codex with the launch environment on, the read-only preflight verdict
(`static_only` / `profile_missing` / … block; an unbuilt pair does not, the
launch hook builds it). Blocking, reads only, never raises — the Model Retry
Policy's backend failover asks it before moving a throttled chat.

## What it never does

Reads no credential value (`~/.aws/credentials`, `~/.codex/auth.json` and every
`credential_process` output stay unread), opens no socket (`network.hosts` is
empty), writes nothing under the host install; the one directory it creates
under the host data home is the `0700` `codex-aws/` pair, and its own state is
`ctx.config` and `ctx.data_dir`. A seam the running host lacks degrades that
feature to *report only* and the page says so.

## Develop

```bash
floofy validate .                                   # schema, hashes, targets, network scan
python tests/smoke_test.py --refresh                # rewrite files[] after editing
uv run pytest tests/ -q                             # discovery, awsenv and hook tests, both host shapes
python tests/page_probe.py                          # headless Chromium render + actions
floofy dev . --follow                               # link into your own gateway
```
