# os-notify-bridge

Mirror KiroCrew's in-app notifications to **native OS banners** — approval
waits, cron reports, skill reviews, resource pressure and the rest of the
notification feed reach your desktop even when the dashboard is buried behind
other windows.

Unofficial; a FloofyCrew mod, not part of KiroCrew.

## What it does

- Listens to the dashboard's own notification fan-out event
  (`mc-notification`), so the host's channel mute and priority settings keep
  their meaning: a channel muted in KiroCrew's notification settings never
  reaches the OS through this mod, and passive notes stay passive.
- Enriches every banner from `GET /api/notifications`: real title, real body
  (markdown stripped), and the note's deep link — clicking the banner focuses
  the dashboard and navigates to it.
- A settings page in the FloofyCrew App (Mods → OS notification bridge):
  - a real **permission surface** — status plus a button that *awaits*
    `Notification.requestPermission()` (a user gesture, as the platform
    requires), with plain words for the `denied` and unsupported states;
  - a master switch, a **background-only** policy (default: banner only when
    the tab is hidden or the window unfocused, so the in-app toast stays the
    only surface while you are looking), and one switch per kind, persisted in
    the mod's config store and pushed live to the bridge — no reload;
  - **Agent turn complete** surfaces the host's own hidden
    `mc-notify-chat-complete` flag: when on, the host renders its richer
    per-session "response ready" banner and the bridge stays out of the way;
  - a test banner button.
- Rate-limited (per-tag repeat suppression plus a global cap) so a note storm
  cannot flood the notification center.

## What it deliberately does not do

- It cannot *remove* the host's two built-in native paths (the hidden-tab
  approval banner and the unread-count banner). The bridge reuses the host's
  own banner tags (`approval_id`/`job_id`/`task_id`, `kirocrew-approval`) so
  the OS collapses duplicates where the platform supports tag replacement;
  the corner cases that remain are upstream behaviour.
- The Notification permission and the turn flag are **per browser** — grant
  once per machine you watch the dashboard from. The kind switches are shared
  through the mod's config store.
- No python-hook, no patches, no network hosts, no credentials: one `spa`
  runtime part and one `ui` settings page, all same-origin.

## Kinds

`approval` (critical — an agent is blocked on you), `turn`, `cron`, `hook`,
`agent`, `taskrunner`, `skills`, `safety_override`, `resources`, `heartbeat`
(off by default), plus an "everything else" switch for app channels and kinds
this version does not know. `subagent` is passive by default and never reaches
the fan-out, so it has no switch of its own.

## Install / develop

```
floofy dev mods/os-notify-bridge        # from a FloofyCrew source checkout
floofy validate mods/os-notify-bridge
```

Keep the manifest honest after edits:
`python scripts/hash_example_files.py mods/os-notify-bridge`.
