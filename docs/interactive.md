# Using `floofy` interactively

Run `floofy` with no subcommand on a terminal and it opens a keyboard-driven
interface: the banner, a summary of this host, and menus for everything the
subcommands do. It is the same manager — every action runs the very handler the
corresponding subcommand runs and writes the same audit row (with `actor: tui`)
— arranged so you can browse instead of remembering flags. FloofyCrew is
unofficial and says so on every screen.

## What opens

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
- **Set up FloofyCrew (`floofy init`)** — or *Re-run floofy init* once the
  consent is recorded. An action that needs the consent (install, enable,
  update, switch profile) offers `init` first when no record exists.

The output of every action lands in a scrollable pane with the command line it
ran and its exit status, so nothing the interface does is different from typing
the command.

## Keys

| Key | In a menu | In a pane | In a text field |
|---|---|---|---|
| `↑` / `↓`, `j` / `k` | move the selection | scroll a line | — |
| `Tab` | next item | scroll a line | — |
| `PgUp` / `PgDn` | move a screenful | scroll a screenful | — |
| `Home` / `End` | first / last item | top / bottom | — |
| `Enter` | select | close | submit |
| `Esc` | back (clears an active filter first) | close | cancel |
| `q` | back; on the home screen: quit | close | types a `q` |
| `/` | filter the list (a text field; Esc clears) | — | types a `/` |
| `Backspace` | shorten the active filter | — | delete a character |
| `Ctrl-U` | — | — | clear the field |
| `Ctrl-C` | leave the interface (exit 130) | leave | leave |

Digits and every other printable character type into a text field. No mouse
is needed, so it works over ssh; `Esc` is told apart from the start of an arrow
sequence by a short timeout, as in every full-screen program.

While an action runs ("Running: floofy … please wait…"), `Ctrl-C` interrupts
it the way it does at the plain CLI (the pane then shows exit 130) and you are
back in the menus.

## Confirmations keep their grade

- An ordinary yes/no is a two-item menu.
- The one-time consent is the full-screen `[ I AGREE ]` frame of
  [terminal.md](terminal.md#the-one-time-consent) — literally the same frame,
  drawn on the interface's own screen; Enter agrees, Esc or `q` declines; the
  acknowledgement is recorded `how: "screen"` by the same `init` step.
- A governance-altering target is a **text field that must contain the exact
  path**. There is no button and no menu for it, here or anywhere
  (Requirement 11.4).
- The unlisted-source line of a git reference shows its disclosure with an
  explicit *I ACCEPT* item that supplies the phrase you would otherwise type.
- Loosening a registry's signature requirement shows the same warning as
  `--allow-unsigned` and needs an explicit confirm item.

## The terminal it needs

The interface draws with the standard library alone — `termios` raw mode, ANSI
control sequences and `select` — through the same small driver the consent
screen uses (`floofy_core.cli.term`). It does **not** use `curses` and needs no
terminfo database, so it runs:

- on an interpreter built without `_curses` — the `python3.12` bundled with the
  host, which the internal installer selects, is one;
- with `TERM` unset — a shell that never exported it is treated as an ANSI
  terminal, exactly as `kiro-cli` treats it;
- over ssh, in `tmux`/`screen`, in a terminal multiplexer pane of any size: the
  layout follows the window, and a resize (`SIGWINCH`, or the size re-read
  every half second) redraws at the new size.

It is drawn on the alternate screen buffer with the cursor hidden (shown only
inside a text field), and redraws rewrite only the rows that changed, so
navigation does not flicker. Leaving — with `q`, `Ctrl-C`, or an interrupt
from outside — restores your previous screen and the terminal's line
discipline, always.

Colours come from the same rules as the rest of the CLI
([terminal.md](terminal.md#styling-colours-weights-and-the-switches)): 24-bit
under `COLORTERM=truecolor`, the 256-colour cube when `TERM` names it, the 16
ANSI colours otherwise (the banner in a single bright magenta), and **nothing**
— no colour, no banner, no reverse video, the `▸` marker alone showing the
selection — under `NO_COLOR` or `--no-color`.

The interface opens whenever **stdin and stdout are both terminals** and `TERM`
is not `dumb`. Otherwise bare `floofy` prints the usage as it always did
(exit 2); under `TERM=dumb` it adds one hint line:

```
floofy: the interactive mode needs a terminal (stdin and stdout TTYs; not TERM=dumb); the subcommands above work everywhere.
```

Every subcommand works in either case, and none of this changes flags, exit
codes or `--json` output.

---
Covers Requirements 15.3, 15.4, 15.6, 14.2.
