# Installing from a link vs. from the registry

`floofy install` takes a registry id, a local directory or archive, an `https://`
archive URL — or a **git reference**: a repository and a tag, cloned with your own
credentials. What differs between these is not what the manager *does* (it
validates, discloses and asks every time) but what anyone else has vouched for.
That is the mod's **source tier**, and the manager shows it everywhere a mod
appears: `floofy search`, `floofy info`, `floofy list`, `floofy status`, the
manager page.

## The three tiers

| Tier | You get it when | Who vouched | What is verified before install |
|---|---|---|---|
| `unlisted` | `floofy install ssh://…@<tag>`, `https://…[.git]@<tag>`, a local directory, an archive, an `https://` archive URL | nobody but you | the mod's own claims: `floofy validate` (schema, every `files[]` hash, targets, network scan); the commit of the checkout is recorded |
| `listed` | `floofy install <id>` from a registry record | a curator merged the record; the index verified against the key pinned for your edition (or you allowed that source unsigned) | everything above, **plus** the checkout or archive is held to the record: commit, canonical-manifest hash and every file (link record) or archive hash and size (asset record) |
| `tested` | a `listed` mod whose compatibility row for **your** host version says `tested` | the Forge ran it on exactly this host version | everything above; the matrix row is the evidence, and `floofy doctor` shows it |

`unlisted` is not a judgement on the mod — it is the absence of anyone else's
judgement. A local directory you wrote yourself is unlisted; so is a colleague's
repository you were sent a link to. Nothing about an unlisted source is trusted
beyond your consent, and the manager says so.

## Installing from a git reference

```bash
floofy install ssh://<host>/<path>@<tag>                     # a repository holding the mod at its root
floofy install https://<host>/<owner>/<repo>.git@<tag>       # the same over https
floofy install ssh://<host>/<path>@<tag>#mods/<id>           # the mod lives in a subdirectory
floofy install https://<host>/<owner>/<repo> --ref <branch|commit>   # unpinned, on purpose
```

The grammar is strict on purpose:

- **the scheme is required** — `ssh://` or `https://`; a bare `host/owner/repo`
  is read as a registry id, `git://` and `http://` are refused (mod traffic and
  FloofyCrew's own traffic are encrypted-only; loopback is the one exception);
- **the tag is required** — `@<tag>` after the repository, the last `@` of the
  path starting it (an `ssh://git@host/…` user is fine). A reference without a
  tag is refused unless you pass `--ref <branch|commit>` to say you want an
  unpinned checkout; the install is still recorded with the commit it resolved to;
- `#<subdirectory>` names the mod's directory inside the repository when it is
  not the root.

What happens: a shallow clone at the tag (`git clone --depth 1 --branch <tag>
--single-branch`) into `<host home>/floofy/cache/sources/git/`, with **your**
git credentials — the ssh agent, `GIT_SSH_COMMAND`, a credential helper; the
manager only turns off git's terminal prompt so an unauthenticated clone fails
instead of hanging. The commit is recorded, every `files[]` entry of the manifest
is checked (hash, and size where known), `floofy validate` runs on the checkout,
and then the disclosure appears with one line the registry path never shows:

```
UNLISTED SOURCE: unlisted source: no curator review, no compatibility data — ssh://…@1.2.0 (commit 3f9a1c2b0d7e)
  nothing about this source is trusted beyond your consent; the mod's own files[] hashes were verified
```

That line is part of the **consent screen**, and it is confirmed like the
one-time warning of `floofy init`: you type `I ACCEPT` at the prompt, or
automation passes `--accept-unlisted-source` (recorded as such in the audit
row). `--yes` never accepts it — `--yes` answers the ordinary yes/no questions
(code parts, flags) and nothing that stands for consent. The audit row of the
install carries the source, the commit, the tier and how the line was accepted.

After that the install is the normal one: staged into `pending/` while a gateway
runs (or applied with `--now`), code parts land disabled, the seam handlers run.
`floofy update` does not move an unlisted mod — re-run `floofy install` with the
new tag when you want it.

## Installing from the registry

```bash
floofy registry defaults        # your edition's registry, verified against the pinned key
floofy search theme
floofy install rimuru-branding  # the newest version known to work on your host version
```

A registry record is one of two shapes ([publishing.md](publishing.md)): an
**asset record** names an archive by URL, hash and size; a **link record** names
the mod's repository, the tag, the commit the curator saw and the SHA-256 of the
canonical manifest. A link record is resolved through the very same clone as a
git reference — and then held to the record: a moved tag (another commit), a
changed manifest or a single differing file refuses the install. Either way the
tier is `listed`; when the compatibility row for your host says `tested`, it is
`tested`, and `floofy search` tells you *before* you install.

## Moving up a tier

Publishing is what turns an unlisted mod into a listed one, and the Forge's
matrix into a tested one: tag the release, add the record, pass the gate
([publishing.md](publishing.md); the internal edition's specifics live with its
edition adapter). Until then, sharing a git reference is a perfectly good way to
hand a mod to someone — as long as both of you know exactly what the tier means.

## Where the tier shows

- `floofy search` — per result, the tier the install *would* have here;
- `floofy info <id>` — the manifest summary, the source record (kind, reference,
  commit, link facts), the tier, the compatibility verdict and the files;
- `floofy list` / `floofy status` — per installed mod;
- the manager page — a badge next to the host-compat badge;
- the audit log — `tier` on every install row.

---
Covers Requirements 8.8, 8.9, 8.11, 11.1, 11.3, 11.6.
