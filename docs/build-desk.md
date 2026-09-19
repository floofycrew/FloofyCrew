# Build desk `***REMOVED***-4sx`

The FloofyCrew build/test farm (Requirement 13.1). Everything the Forge does
that is heavy, noisy or ***REMOVED***-bound runs here — never on the daily desk that
hosts the live KiroCrew gateway.

| Fact | Value |
|---|---|
| Host | `dev-dsk-***REMOVED***-2b-74435542.us-west-2.***REMOVED***` (alias `***REMOVED***-4sx`) |
| OS / arch | Amazon Linux 2023, x86_64, 8 vCPU (c6i.2xlarge), 15 GiB RAM, 500 GB `/` (489 GB free at bootstrap) |
| Home | `/home/***REMOVED*** -> /local/home/***REMOVED***` |
| Login shell | zsh (`~/.zshenv` sources `~/.forge-env.sh`, so non-interactive `ssh host cmd` gets the PATH) |
| Privileges | passwordless `sudo` (used only for `dnf install` and `loginctl enable-linger`) |
| Egress | github.com, nodejs.org, registry.npmjs.org, pypi.org, astral.sh, cli.kiro.dev, download.crew.kiro.dev all reachable; code.***REMOVED*** answers 401 until ***REMOVED*** |
| Bootstrapped | 2026-09-19 by `forge/bootstrap-desk.sh` (log: `~/forge/logs-bootstrap-run1.txt`, status: `~/forge/bootstrap-status.txt`) |

## Bootstrap

`forge/bootstrap-desk.sh` is idempotent and non-interactive. Copy it, run it,
read the status table it prints (also written to `~/forge/bootstrap-status.txt`):

```bash
DESK=dev-dsk-***REMOVED***-2b-74435542.us-west-2.***REMOVED***
ssh $DESK 'mkdir -p ~/forge' && scp forge/bootstrap-desk.sh $DESK:~/forge/
ssh $DESK 'bash ~/forge/bootstrap-desk.sh'            # install what is missing, then status table
ssh $DESK 'bash ~/forge/bootstrap-desk.sh --verify'   # status table only
```

What it installs and how:

| Item | Method | Result on 2026-09-19 |
|---|---|---|
| Python 3.12 | `sudo dnf install python3.12 python3.12-devel python3.12-pip` (AL2023 repo); falls back to `uv python install 3.12` without sudo | `/usr/bin/python3.12` = 3.12.14 |
| Node 24 | official tarball `node-v24.21.0-linux-x64.tar.xz` from nodejs.org, verified against `SHASUMS256.txt` **and** a sha256 pinned in the script; unpacked to `~/.local/node/`, `current` symlink, `node/npm/npx/corepack` linked into `~/.local/bin` | v24.21.0, npm 11.19.0 |
| uv | `astral.sh/uv/install.sh` into `~/.local/bin` (no rc edits) | 0.12.17 |
| kiro-cli | public installer `cli.kiro.dev/install` into `~/.local/bin` (needs no ***REMOVED***; `***REMOVED*** install kiro-cli` is the alternative once ***REMOVED*** exists) | 2.22.1 — logged in (IAM Identity Center, device flow) on 2026-09-19 |
| Durable tmux | `~/.local/bin/tmux-durable.sh` (copy of the daily desk's script with HOME-relative paths, socket `~/.tmux-forge.sock`, log `~/forge/logs/tmux-durable.log`), server spawned through `systemd-run --user`; `sudo loginctl enable-linger ***REMOVED***` | server unit `tmux-server-*.service` under `user@13611434.service/app.slice`, keeper session `__keeper`, Linger=yes |
| Scratch dirs | `~/forge/{scratch,logs,ws,src}` | created |
| PATH | `~/.forge-env.sh` sourced from `~/.zshenv` and `~/.bashrc` (guarded block); adds `~/.local/bin` and `~/.***REMOVED***/bin`, exports `FORGE_HOME`, `TMUX_DURABLE_SOCK` | in place |
| git-lfs | AL2023 package already present; `git lfs install --skip-repo` | 3.7.1 |
| ***REMOVED*** | already present at `/usr/local/bin/***REMOVED***`; every `***REMOVED***` command needs a ***REMOVED*** cookie | present; usable since `***REMOVED*** -o` on 2026-09-19 |
| ***REMOVED*** tooling | `***REMOVED*** install ***REMOVED***cli` (gives `***REMOVED***`, `***REMOVED***-build`, `***REMOVED***-path`, `***REMOVED***-recursive-cmd` in `~/.***REMOVED***/bin`) | installed 2026-09-19 (`***REMOVED***-build` in `~/.***REMOVED***/bin`) |

Also present from the image: git 2.x, make 4.3, gcc 11.5, jq, rsync, unzip, xz, tmux 3.6a.

## Owner steps (Yubikey) — completed 2026-09-19

The script detects both and reports them as BLOCKED instead of prompting; the
owner ran them on the desk and `bash ~/forge/bootstrap-desk.sh --verify`
reports every row OK (verified over ssh 2026-09-19T21:01Z). For a rebuilt desk:

```bash
***REMOVED*** -o                                  # ***REMOVED*** cookie for ***REMOVED*** / ***REMOVED*** / code.***REMOVED***
***REMOVED*** install ***REMOVED***cli                  # ***REMOVED***, ***REMOVED***-build, ***REMOVED***-path, ***REMOVED***-recursive-cmd
kiro-cli login --license pro --identity-provider https://amzn.awsapps.com/start \
               --region us-east-1 --use-device-flow
bash ~/forge/bootstrap-desk.sh --verify    # every row should read OK
```

***REMOVED*** cookies expire daily; the Forge's internal-lane steps (***REMOVED***
provisioning, ***REMOVED*** builds, ***REMOVED*** commits) will need a refreshed cookie or a
non-***REMOVED*** credential path — that is an open item for package 8.

## Conventions on the desk

- Long-running work goes into the durable tmux server, never a bare `nohup`:
  `tmux-durable.sh new-session <name> <cmd…>`; inspect with
  `tmux -S ~/.tmux-forge.sock ls | attach -t <name> | kill-session -t <name>`.
- Source trees for diagnosis live under `~/forge/src/` (public clone) and
  `~/forge/ws/` (***REMOVED*** workspaces). They exist for fingerprint generation, the
  compat matrix and diagnosis only — no host built here is ever shipped (DR-1,
  Requirement 12.8).
- Per-version Forge sandboxes go to `~/forge/scratch/<ver>/` with
  `KIROCREW_HOME` pointed inside them and, for ***REMOVED*** provisioning,
  `BUILDER_***REMOVED***_HOME=<ver>/.***REMOVED***` plus the versioned client binary
  (`~/.***REMOVED***/tools/***REMOVED***/<ver>/***REMOVED***`) — ***REMOVED*** ignores `$HOME`
  (spike 1.6).
- Build logs: `~/forge/*.log`; the first `make build` is `~/forge/build-public.log`
  (driver: `forge/build-public.sh`).
