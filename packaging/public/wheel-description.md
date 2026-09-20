# FloofyCrew

An **unofficial** modding ecosystem for KiroCrew — not affiliated with Kiro or
KiroCrew. This wheel installs the `floofy` mod manager: a standard-library-only
Python 3.12 command that runs on the KiroCrew host's own interpreter.

- `floofy init` — the one-time warning (mods run with the gateway's privileges,
  may bypass the host's governance ceiling, and the risk of every mod you install
  is yours), then the FloofyCrew Loader app is installed through the host's own
  App Kit and the re-apply trigger is set up. Nothing is applied before you
  acknowledge the warning.
- `floofy install <id|path|url>` — install a mod from the signed registry, a
  directory or an archive; parts, seams, declared hosts and governance flags are
  shown before anything lands; code mods land disabled until you enable them.
- `floofy doctor`, `status`, `verify`, `restore --all` — see what FloofyCrew did
  and get back to a vanilla host at any time.

FloofyCrew attaches to the KiroCrew you already have and never ships a rebuilt
host. Documentation, the Loader app archive and the single-file `floofy.pyz` are
on the project's releases; the mod manifest reference, the seam guide, the
Python and SPA APIs and the consent and trust model are under `docs/` in the
source repository.
