# Consent, transparency and trust

FloofyCrew is an **unofficial** project, not affiliated with Kiro or KiroCrew. It
identifies itself as such in the CLI, the manager page, every artifact and every
warning, and never uses the host's branding as its own.

## The principle

Requirement 11 of the FloofyCrew specification, quoted verbatim:

> **User Story:** As a user, I decide what runs on my machine: like a game mod,
> FloofyCrew installs and stays enabled on my consent even where the host's
> governance would say no — and in exchange it tells me plainly what it is
> doing, keeps everything attributable and reversible, and never hides a change
> from me.
>
> Principle: **user consent outranks host governance; governance is a warning,
> not a gate.** The user identifies and accepts the security risk of each mod,
> exactly as game modders do. FloofyCrew's job is to make that choice informed,
> logged and reversible.

Every mechanism below exists to hold up the second half of that bargain.

## The one-time warning

`floofy init` (or the FloofyCrew App's first action) shows this text and waits
for your acknowledgement — on a terminal as a full-screen confirmation on the
alternate screen buffer with a single `[ I AGREE ]` control (Enter agrees, Esc or
`q` declines, your previous screen comes back either way), in the App as a modal
with the same text and the same `[ I AGREE ]` control (Esc declines), elsewhere
as the typed phrase `I ACCEPT`; nothing is installed or patched before it:

> FloofyCrew is UNOFFICIAL and not affiliated with Kiro or KiroCrew.
>
> Mods run inside the KiroCrew gateway with the gateway's full privileges
> (your files, your network, your credentials in memory). Mods may bypass the
> host's governance ceiling. FloofyCrew shows you what each mod does before
> install, logs every change it makes and can restore the host to vanilla
> (floofy restore --all) — but the risk of every mod you install is yours.

The acknowledgement is recorded in `<host home>/floofy/consent.json`
(`warningVersion`, `acknowledgedAt`, `by`, `how`: `screen`, `app`, `typed` or
`flag` — the same record whichever way you agreed; automation keeps
`--i-accept-the-risk`, and `--yes` never answers this question). Until that
record exists for the **current** warning text the Loader is inert — every mod
carries the reason `ConsentRequired` and nothing activates; when the text changes
materially the version is bumped and the warning is shown again.
`--i-accept-the-risk` exists for automation you own and is recorded as such;
`--yes` never covers the warning.

## Governance is a warning, never a gate

FloofyCrew reads the host's effective governance — the status API, the config,
the policy files the edition adapter knows — for **information only**. Where
governance would forbid a seam, FloofyCrew still proceeds on your consent and
uses its own path where the host's route is closed:

| Host says | FloofyCrew does |
|---|---|
| theme installation disabled | writes the pack directly, byte-equivalent to what the host's validator would have written, and warns |
| third-party apps not allowed to run without a trust grant | offers — with your confirmation — to record the per-app `agent.apps_trusted` grant (an operator-writable setting), and shows the host's admission verdict as a warning |
| update commands pinned so a host update will wipe patches | warns that the patch will not survive the next update and asks you to confirm |
| an app admission verdict of `banned` | shows it; you decide |

The verdict appears as a `governance` **warning** per affected mod in `floofy
doctor`, `floofy status` and the manager page. Governance is never a non-load
reason — the typed reasons are `Error`, `Duplicate`, `Conflict`, `Dependency`,
`Released`, `Feature`, `Unsupported`, `MissingFiles`, `Quarantined`,
`UserDisabled` and `ConsentRequired`, nothing else.

Every governance-crossing action is explicit, named and confirmed. FloofyCrew
never *silently* weakens a host protection.

## What FloofyCrew itself never does

- **Edit the host's governance files** — the security policy, the admission
  policies, the denied-commands list, the trust directory. It routes around a
  closed route; it does not rewrite the policy. A *mod* whose files or patch
  targets touch those paths is flagged **governance-altering** at validation and
  at install, needs an explicit **per-file typed confirmation** (the exact path;
  `--yes` never covers it), and is recorded as such in the audit log — it is not
  refused.
- **Modify host Python in place, the launcher or the install bookkeeping
  files**, or register the host's plugin entry point ([seams.md](seams.md)).
- **Ship or rebuild the host.** It attaches to the KiroCrew you already have.
- **Handle host or user credentials.** A mod that needs credentials asks you
  directly and says so in its manifest (`network.credentials: true`).
- **Hold back host updates by default.** `floofy hold` exposes the host's own
  pause as an explicit, logged action, never automatically.
- **Load code from a third-party origin** into the dashboard, or ask for a CSP
  change ([spa-api.md](spa-api.md)).

## Mods are untrusted code you chose

Before anything lands, `floofy install` shows the mod's parts with the seam
each one uses and whether it modifies any payload file, the remote hosts it
declares, the validator's network and governance flags, and the host's own
admission verdict for `app` parts. `python-hook`, `patch` and `app` kinds need an
explicit yes; flags need acceptance. Mods with `spa` or `python-hook` parts land
**disabled** until you `floofy enable` them. `floofy status` and the manager page
show, for every mod, its state, seam, compat badge, typed reason and warnings.

## The network rule

FloofyCrew's own traffic — registry indexes, mod files, the compatibility matrix
— is HTTPS with pinned signature keys. Mods may make their own requests, subject
to: (a) an encrypted channel (`https`, `wss`, TLS sockets); (b) the only
exception is loopback (`localhost`, `127.0.0.0/8`, `::1`, unix sockets), so a
mod can talk to the local gateway in plaintext; (c) the remote hosts a mod
contacts are declared in `floofy.json` `network.hosts[]` and shown at install;
(d) the Loader enforces (a)–(c) for requests made through `ctx.http` /
`floofy.fetch` and audits every denial, and the validator flags any mod whose
code contains a plaintext `http://` / `ws://` URL to a non-loopback host or an
undeclared host — a flag you may accept ([python-api.md](python-api.md)).

## Unlisted sources — your explicit choice

`floofy install <git reference>` (`ssh://…`, `https://…[.git]`, `@<tag>`
optional — the default branch without it)
clones a repository nobody but you vouches for. The manager verifies what the
mod says about itself (`floofy validate`, every `files[]` hash) and records the
commit, then adds one line to the consent screen — **unlisted source: no curator
review, no compatibility data** — which you confirm like the one-time warning:
typed `I ACCEPT`, or `--accept-unlisted-source` for automation you own, recorded
in the audit row as such. `--yes` never accepts it. Nothing about the source is
trusted beyond that consent, and the mod carries the source tier `unlisted`
wherever it appears (`search`, `info`, `list`, `status`, the manager page); a
registry record is `listed`, and `tested` when the matrix says so for your host
([installing-from-a-link.md](installing-from-a-link.md)).

## Registry trust controls — yours

The defaults are safe: an index whose signature does not verify against the keys
pinned for your edition (or a key you pinned per source) is refused; every
download is checked against its `sha256` and size; the compatibility matrix is
trusted under the same rule. You may loosen any of it, per source:

```bash
floofy registry add <url> --allow-unsigned          # admit an unsigned index from this source (audited)
floofy registry add <url> --public-key <record>     # pin an extra key for this source
floofy registry add <url> --key-id <id>             # restrict a source to one key
floofy registry add <url> --trust owner|index       # per-source trust level
```

Each loosening writes an audit row. The host's own app and theme admission is
consulted only to explain what the host will think of the result.

## The same questions in the App

The FloofyCrew App (the Loader app's page inside KiroCrew) runs every action
through a Loader route that calls the **same** `floofy` handler as the CLI, so
it asks the same questions, with the same grade, before anything changes:

| The CLI asks | The App shows | Recorded |
|---|---|---|
| the one-time warning, `[ I AGREE ]` / `I ACCEPT` | a modal with the same text and an `[ I AGREE ]` control; Esc declines | `consent.json` `how: app`, a `consent` audit row |
| an ordinary yes/no (a `python-hook`/`patch`/`app` part, the flags, an uninstall) | the install disclosure — parts and seams, whether payload files are modified, the declared network hosts and credentials, the flags, the host's verdicts, the source tier — with the exact prompt and Yes / No; the staged-vs-apply-now choice sits here (staged is the default) | the row the CLI writes, `actor: app` |
| a governance-altering target typed as its exact path | a **text field** per target; the button unlocks only when the field equals the path, and the Loader compares what was typed byte for byte — never a button, never trimmed | `governance-target-confirm` (`typed at the prompt`) |
| the unlisted-source line of a git reference, `I ACCEPT` | the disclosure with the line and an explicit *I ACCEPT — install from this unlisted source anyway* item | the `install` row's `unlistedSource.how: typed` |
| `--allow-unsigned` on a registry source | the same loosening warning and an explicit accept item | `registry-trust-loosened` |

A request that does not carry the answer gets a `409` with the question and
**nothing happens**; the App has no `--yes`, `--i-accept-the-risk`,
`--accept-unlisted-source` or `--confirm-governance-target` — those exist for
automation you run yourself at a terminal.

## The audit log

Every mutating operation appends one JSON line to `<host home>/floofy/audit.jsonl`:
`ts`, `op` (`consent`, `init`, `install`, `uninstall`, `enable`, `disable`, `apply-confirm`,
`governance-target-confirm`, `grant-apps-trusted`, `restore`, `restore-all`, `yeet`,
`yeet-restore`, `hold`, `host-change`, `registry-add`, `registry-trust-loosened`,
`net-denied`, …), the mod and version,
the payload and files touched, the actor, the result, and `consentRef` — the
warning version and the hash of the exact `consent.json` the operation ran
under; an `install` row also carries the source (kind, reference, commit), the
tier and, for a git reference, how the unlisted-source line was accepted. `floofy audit` reads it. Governance-crossing steps and trust loosenings
are recorded with their confirmation. This is in addition to whatever the host's
own audit records for app and theme installs.

## Reversibility

`floofy restore --all` returns every payload to vanilla by manifest and by
sweep, even when the manifest is lost; `floofy uninstall` reverses a mod's seam
parts and records the previous values it changed; `floofy deinit` removes the
trigger, the early shim and the Loader app and restores every payload; `floofy
--vanilla` boots the host once with every mod disabled
([updating.md](updating.md)).

---
Covers Requirements 2.8, 5.9, 5.10, 8.8, 8.11, 11.1, 11.2, 11.3, 11.4, 11.5, 11.6, 11.7, 11.8.
