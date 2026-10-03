# agent-backend-doctor

Finds, un-caches and repairs KiroCrew's agent-backend harnesses — Claude Code,
Codex, OpenCode, pi — so that *"I installed it and KiroCrew still says missing"*
stops being a thing. A FloofyCrew mod: a `python-hook` in the gateway plus its
own page in the FloofyCrew App. Unofficial; not affiliated with Kiro or KiroCrew.

## The two host gaps it covers

Both verified in the installed 0.7.0.5 and 0.8.0.6 builds, both reached through
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

## What it never does

Reads no credential, opens no socket (`network.hosts` is empty), writes nothing
under the host install or the host data home; its own state is `ctx.config` and
`ctx.data_dir`. A seam the running host lacks degrades that feature to
*report only* and the page says so.

## Develop

```bash
floofy validate .                                   # schema, hashes, targets, network scan
python tests/smoke_test.py --refresh                # rewrite files[] after editing
uv run pytest tests/discovery_test.py tests/hook_test.py -q   # 26 tests, both host shapes
python tests/page_probe.py                          # headless Chromium render + actions
floofy dev . --follow                               # link into your own gateway
```
