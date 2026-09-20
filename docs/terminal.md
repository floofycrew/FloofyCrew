# Using `floofy` at the terminal

FloofyCrew's terminal surfaces — the install scripts, the one-time consent and
the `floofy` command itself — are made to be read at a glance: colour and weight
where the terminal supports them, numbered steps and menus instead of strings to
type, and a keyboard-driven interactive mode. None of it changes what automation
relies on: flags, exit codes and `--json` output are the same with and without
any of it. FloofyCrew is unofficial and says so in every banner.

## Styling: colours, weights and the switches

The CLI paints its human output with a small semantic palette — `heading`, `ok`,
`warn`, `danger`, `muted`, `accent` — and the weights bold, dim and italic. What a
line *says* never depends on colour: the same words are printed with or without
the escape sequences, and the transcript the manager page reads is plain text.

The colour depth comes from the terminal:

| Environment | Depth |
|---|---|
| `COLORTERM=truecolor` or `COLORTERM=24bit` | 24-bit (the branding's sunset colours) |
| `TERM` naming `256color` (or `direct`) | 256 colours (the nearest cube entries) |
| anything else | the 16 ANSI colours |

Styling is **off** — plain text, no banner — when any of these holds:

- stdout is not a terminal (a pipe, a file, the manager page's in-process call);
- `NO_COLOR` is set in the environment, with any value (even empty);
- `--no-color` is given;
- `TERM=dumb`;
- `--json` is given — the document is printed exactly as it would be without
  styling, byte for byte, on a pipe and on a terminal alike.

`FORCE_COLOR=1` turns colour on for a pipe (for tests, or a pager that
understands colour); it never overrides `NO_COLOR`, `--no-color`, `TERM=dumb` or
`--json`, and the banner still needs a real terminal. Warnings and errors on
stderr are painted only when stderr is a terminal too.

## The banner

`floofy --version`, `floofy doctor` and the interactive mode lead with the
FloofyCrew mark — the ASCII art of `branding/ascii/floofy.json`, built by
`branding/build_ascii.py` and shipped inside the CLI. It is rendered in 24-bit
colour where the terminal advertises it, quantised to the 256-colour cube where
that is what the terminal has, as a single bright-magenta rendering on 16-colour
terminals, and **omitted** when colours are off or the terminal is narrower than
the art plus its margins (38 + 4 = 42 columns). It is decoration: it never
appears in `--json` output, in the manager page's transcript, or when stdout is
a pipe.

## The install scripts

Both editions' `install.sh` share one structure:

1. five numbered steps `[1/5] … [5/5]`, one status glyph each — `✓` done, `✗`
   failed, `·` in progress or skipped (`+`, `x`, `-` when the locale is not
   UTF-8) — Python, the release or package, the command, your choices, and
   `floofy init`;
2. the same palette and banner as the CLI, under the same switches (`NO_COLOR`,
   `--no-color`, not a terminal; `--no-banner` or `FLOOFYCREW_NO_BANNER=1` skip
   the art alone; a terminal under 42 columns skips it too);
3. **numbered menus with a default** for the two enumerated answers — which
   re-apply trigger to install (the edition's default set, a single kind, or
   none) and whether to run `floofy init` now — an empty answer takes the default,
   a wrong one re-asks;
4. a final summary: where the command, the zipapp and the Loader app landed,
   which interpreter runs it, and the **next command** to run.

A run without a terminal (`curl … | sh > log`, a provisioning script, CI) takes
the defaults, prints them and **never blocks on a prompt**; `floofy init` is not
run — a terminal is needed to acknowledge the one-time warning — and the summary
names the command to run later. Every previous flag and environment variable
still works: `FLOOFYCREW_NO_INIT=1` / `--no-init`, `FLOOFYCREW_PREFIX` /
`--prefix DIR`, `FLOOFYCREW_VERSION`, `FLOOFYCREW_REPO`, `FLOOFYCREW_ASSET_DIR`,
`FLOOFY_PYTHON`, and for the internal script flags after `--` for `floofy init`.
`FLOOFYCREW_TRIGGER` / `--trigger` (`default`, `timer`, `wrapper` — public edition
only — or `none`) pre-answer the trigger menu; the answer becomes
`floofy init --trigger KIND` or `--no-trigger`. The scripts never acknowledge
the warning for you: `floofy init` runs on your terminal (through `/dev/tty` when
the script is piped into `sh`), and its exit status is the script's.

## The one-time consent

On a terminal, `floofy init` shows the warning of
[consent-and-trust.md](consent-and-trust.md) as a **full-screen confirmation on
the alternate screen buffer**: the banner when it fits, the text wrapped to your
terminal's width (scroll with the arrows or `j`/`k`, PgUp/PgDn when it is taller
than the screen), and a single focused control:

```
                          [ I AGREE ]
  Enter agrees · Esc or q declines · ↑/↓ scroll
```

**Enter** agrees; **Esc**, **q** or Ctrl-C decline (exit code 3, nothing
written). Your previous screen comes back either way. The acknowledgement is
recorded exactly as before in `consent.json`, with `how: "screen"`; the typed
`I ACCEPT` prompt is still used when stdout is not a terminal (`how: "typed"`),
`--i-accept-the-risk` still records `how: "flag"` for automation you own, and
`--yes` never answers this question. On a terminal too small for the screen
(under 40×8) the typed prompt is used.

Typed confirmations stay typed: a mod whose files touch a host governance file
still asks you to **type the exact path** of every such file — at the CLI, in the
interactive mode (a text field) and in the manager App. There is no button for
that, by design (Requirement 11.4).

## The interactive mode

Run `floofy` with no subcommand on a terminal and it opens:

- **Home** — the banner, a host summary (edition, version, channel, Loader
  state, consent record, data home, and the one-line FloofyCrew update notice
  when a newer release supports this host — the same daily cached check
  `doctor` and `status` use, [updating.md](updating.md)) and the menu.
- **Mods** — every installed mod with its state (`[on ]`/`[off]`), version and
  source tier (`unlisted` / `listed` / `tested`); Enter on a mod for Info,
  Enable/Disable, Update, Uninstall, Yeet; plus *Install from the registry*
  (a search field, then the matching records), *Install from a git reference*
  (a text field for `ssh://…@tag` or `https://…@tag`), *Restore a quarantined
  set*, *Show the quarantine*, *Status*.
- **Registries** — the sources and their trust; Enter on a source for Refresh,
  Change the trust level, Accept/require unsigned indexes, Remove; plus *Add a
  source* (URL, trust level, signature requirement), *Refresh every source*,
  *Record the edition's default source*, *List*.
- **Profiles** — Enter on a profile for the plan, Switch (staged), Switch now,
  Export; plus *Save the installed set*, *Import from a file*.
- **Doctor** and **Check for updates** (then an explicit "update all now").
- **Update FloofyCrew itself** — a fresh `floofy self-update --check`, then an
  explicit "update now" that runs `floofy self-update` (its own yes/no).

Keys: `↑`/`↓` or `j`/`k` move, PgUp/PgDn and Home/End jump, **Enter** selects,
**Esc** (or `q`) goes back, `/` filters a list, `q` on the home screen quits.
No mouse is needed, so it works over ssh.

Every action runs **the same handler as the corresponding subcommand** and
writes the same audit row (with `actor: tui`); the command's output is shown in
a scrollable pane with its exit status, so nothing the interface does is
different from typing the command. Confirmations keep their grade: an ordinary
yes/no is a two-item menu; the one-time consent is the full-screen `[ I AGREE ]`
frame above; a governance-altering target is a text field that must contain
the exact path; the unlisted-source line of a git reference shows its disclosure
with an explicit *I ACCEPT* item; loosening a registry's signature requirement
shows the same warning as `--allow-unsigned` and needs an explicit confirm item.

The interactive mode uses only the standard library (`curses`). Without a
terminal, bare `floofy` prints the usage as it always did (exit 2). On a
terminal `curses` cannot drive — `TERM` unset or `dumb`, no terminfo — it prints
the usage plus one hint line, and every subcommand works as before.

## Guarantees for automation

- Flags, exit codes and `--json` documents are unchanged; `--json` output is
  byte-identical with and without styling.
- Styling is off whenever stdout is not a terminal; `NO_COLOR` and `--no-color`
  turn it off on a terminal too.
- Bare `floofy` without a terminal prints the usage (exit 2); it never waits for
  a key.
- The install scripts take and print their defaults when there is no terminal
  and never block; their flags and environment variables are unchanged.
- The consent is never acknowledged by a script, a default or `--yes`: only
  Enter on the `[ I AGREE ]` control, the typed phrase, or your own
  `--i-accept-the-risk`, each recorded as such.

---
Covers Requirements 15.1, 15.2, 15.3, 15.4, 15.5, 15.6, 14.2, 14.3.
