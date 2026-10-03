"""Where a Node ACP adapter can hide, and how to find it without the host's help.

Pure functions with no host import, so they test on any interpreter. The hook
(``hook/__init__.py``) feeds them the names the host uses (``bin_name``, the npm
package, the dependency marker) and the environment; everything here is
``os.path`` and ``os.environ``.

KiroCrew resolves ``claude-agent-acp`` / ``codex-acp`` / ``pi-acp`` through
``acp.client._resolve_node_adapter_argv``: an env override, a vendored
``node_modules``, mise, and finally ``env.augmented_path()`` — a fixed list of bin
directories (``~/.local/bin``, ``~/.npm-packages/bin``, the CLI tooling's bin, mise /
asdf / nvm / fnm per-version bins, Homebrew, ``/usr/local/bin``) plus the gateway's
inherited ``PATH``. An npm global prefix outside that list — ``~/.local/node``
(``npm`` installed from a tarball), ``~/.npm-global``, a ``prefix=`` in
``~/.npmrc``, ``$NPM_CONFIG_PREFIX`` — is invisible even when ``node`` itself was
found. This module enumerates exactly those prefixes and looks for the adapter's
entry script with its ``@agentclientprotocol/sdk`` dependency resolvable from it —
nested inside the package the way a global ``npm i -g`` lays it out, or hoisted
beside it the way a project-local install does (the only layout the host checks;
an entry whose dependency is nowhere dies at ESM import, after the spawn).
"""
from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from typing import Iterable

#: ``bin_name`` → npm package, as the host spells them (``ACP_BACKEND_NODE_ADAPTER_PACKAGES``).
ADAPTER_PACKAGES: dict[str, str] = {
    "claude-agent-acp": "@agentclientprotocol/claude-agent-acp",
    "codex-acp": "@agentclientprotocol/codex-acp",
    "pi-acp": "pi-acp",
}
#: The hoisted dependency every adapter imports; a tree without it is incomplete.
DEP_MARKER = os.path.join("@agentclientprotocol", "sdk")
ENTRY_SEGMENTS = ("dist", "index.js")


@dataclass
class Found:
    """One adapter resolution: the ``node`` to run it with and the entry script."""

    bin_name: str
    entry: str
    node: str
    prefix: str
    rung: str
    argv: list[str] = field(init=False)

    def __post_init__(self) -> None:
        self.argv = [self.node, self.entry]


def _is_exec(path: str) -> bool:
    try:
        st = os.stat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and bool(st.st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))


def _real(path: str) -> str:
    try:
        return os.path.realpath(path)
    except OSError:
        return path


def npmrc_prefix(home: str, environ: dict[str, str] | None = None) -> str | None:
    """``prefix=`` from ``$NPM_CONFIG_USERCONFIG`` or ``~/.npmrc`` (the file is read for that one key only)."""
    env = os.environ if environ is None else environ
    path = env.get("NPM_CONFIG_USERCONFIG") or os.path.join(home, ".npmrc")
    try:
        with open(path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith(("#", ";")):
                    continue
                key, sep, value = line.partition("=")
                if sep and key.strip().lower() == "prefix":
                    value = value.strip().strip("\"'")
                    if value.startswith("~"):
                        value = home + value[1:]
                    value = os.path.expandvars(value)
                    return value or None
    except OSError:
        return None
    return None


def candidate_prefixes(home: str, environ: dict[str, str] | None = None, *, node_on_path: str | None = None) -> list[tuple[str, str]]:
    """``(rung, prefix)`` pairs, most specific first, deduplicated; a prefix is where ``lib/node_modules`` lives.

    The rungs: the explicit ``$NPM_CONFIG_PREFIX`` / ``$npm_config_prefix``, the
    ``~/.npmrc`` ``prefix=``, the tree the found ``node`` lives in (``<prefix>/bin/node``,
    through symlinks — this is what covers ``~/.local/node/current``), the layouts
    tarball installs use (``~/.local/node``, ``~/.local/node/current``,
    ``~/.local/share/node``), the layouts the npm docs recommend for a user prefix
    (``~/.npm-global``, ``~/.npm-packages``, ``~/.local``), ``$PREFIX`` (Termux and
    friends), and ``$NVM_BIN``'s tree.
    """
    env = os.environ if environ is None else environ
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def add(rung: str, prefix: str | None) -> None:
        if not prefix:
            return
        prefix = os.path.normpath(os.path.expanduser(prefix))
        key = _real(prefix)
        if key in seen or not os.path.isdir(prefix):
            return
        seen.add(key)
        out.append((rung, prefix))

    add("NPM_CONFIG_PREFIX", env.get("NPM_CONFIG_PREFIX") or env.get("npm_config_prefix"))
    add("~/.npmrc prefix", npmrc_prefix(home, env))
    if node_on_path:
        node_real = _real(node_on_path)
        add("the tree node lives in", os.path.dirname(os.path.dirname(node_real)))
        add("the tree node is linked from", os.path.dirname(os.path.dirname(node_on_path)))
    for rel in (".local/node/current", ".local/node", ".local/share/node", ".npm-global", ".npm-packages", ".local"):
        add("~/" + rel, os.path.join(home, rel))
    # per-version tarball layouts: ~/.local/node/node-v24.21.0-linux-x64/, ~/.local/share/node/v22/ …
    for parent in (os.path.join(home, ".local", "node"), os.path.join(home, ".local", "share", "node")):
        try:
            children = sorted(os.listdir(parent), reverse=True)
        except OSError:
            continue
        for child in children:
            candidate = os.path.join(parent, child)
            if os.path.isdir(candidate) and (os.path.isdir(os.path.join(candidate, "lib", "node_modules")) or _is_exec(os.path.join(candidate, "bin", "node"))):
                add(f"~/{os.path.relpath(parent, home)}/<version>", candidate)
    add("$PREFIX", env.get("PREFIX"))
    nvm_bin = env.get("NVM_BIN")
    if nvm_bin:
        add("$NVM_BIN", os.path.dirname(nvm_bin))
    return out


def node_modules_roots(prefix: str) -> list[str]:
    """The global ``node_modules`` directories a prefix may carry (POSIX ``lib/``, and the Windows flat layout)."""
    roots = [os.path.join(prefix, "lib", "node_modules"), os.path.join(prefix, "node_modules")]
    return [r for r in roots if os.path.isdir(r)]


def node_for_prefix(prefix: str, fallback: str | None) -> str | None:
    """The ``node`` that belongs to *prefix* when it ships one, else *fallback*."""
    own = os.path.join(prefix, "bin", "node")
    if _is_exec(own):
        return own
    return fallback


def find_node(environ: dict[str, str] | None, search_path: str, home: str) -> str | None:
    """A ``node`` executable: the host's search path first, then the well-known trees."""
    env = os.environ if environ is None else environ
    for directory in [p for p in search_path.split(os.pathsep) if p] + [p for p in env.get("PATH", "").split(os.pathsep) if p]:
        candidate = os.path.join(directory, "node")
        if _is_exec(candidate):
            return candidate
    for rel in (".local/node/current/bin/node", ".local/node/bin/node", ".local/share/node/bin/node", ".npm-global/bin/node", ".local/bin/node"):
        candidate = os.path.join(home, rel)
        if _is_exec(candidate):
            return candidate
    return None


def dependency_satisfied(root: str, package: str, dep_marker: str = DEP_MARKER) -> str | None:
    """Where the adapter's hoisted dependency resolves from its entry, or ``None`` when it does not.

    Two layouts are real. A project-local / vendored install hoists it beside the
    package (``<root>/@agentclientprotocol/sdk`` — the host's own completeness
    rule). A global ``npm i -g`` nests it INSIDE the package
    (``<root>/<package>/node_modules/@agentclientprotocol/sdk``), which is where
    Node's resolution looks first; the host's hoisted check can never match that
    layout, which is one more reason a global install reads as missing.
    """
    nested = os.path.join(root, *package.split("/"), "node_modules", dep_marker)
    if os.path.isdir(nested):
        return nested
    hoisted = os.path.join(root, dep_marker)
    if os.path.isdir(hoisted):
        return hoisted
    return None


def find_adapter(bin_name: str, *, home: str, search_path: str = "", environ: dict[str, str] | None = None,
                 package: str | None = None, dep_marker: str = DEP_MARKER) -> Found | None:
    """Look for *bin_name*'s entry script under every candidate npm prefix; ``None`` when nowhere.

    A tree counts only when ``<root>/<package>/dist/index.js`` is a file AND the
    adapter's dependency resolves from it (:func:`dependency_satisfied`), so an
    incomplete copy is skipped rather than spawned and crashed at ESM import. The
    returned ``argv`` runs the entry with the prefix's own ``node`` when the prefix
    ships one (an adapter installed by that npm was built against it), else the
    first ``node`` the host's search path or the well-known trees offer.
    """
    env = os.environ if environ is None else environ
    package = package or ADAPTER_PACKAGES.get(bin_name) or bin_name
    node_on_path = find_node(env, search_path, home)
    for rung, prefix in candidate_prefixes(home, env, node_on_path=node_on_path):
        for root in node_modules_roots(prefix):
            entry = os.path.join(root, *package.split("/"), *ENTRY_SEGMENTS)
            if os.path.isfile(entry) and dependency_satisfied(root, package, dep_marker):
                node = node_for_prefix(prefix, node_on_path)
                if node:
                    return Found(bin_name, _real(entry), node, prefix, rung)
    return None


def launcher_candidates(bin_name: str, *, home: str, environ: dict[str, str] | None = None) -> list[str]:
    """``bin/<bin_name>`` launchers an ``npm i -g`` dropped under any candidate prefix (for the symlink repair)."""
    env = os.environ if environ is None else environ
    out: list[str] = []
    for _rung, prefix in candidate_prefixes(home, env, node_on_path=find_node(env, "", home)):
        candidate = os.path.join(prefix, "bin", bin_name)
        if _is_exec(candidate) or os.path.islink(candidate):
            out.append(candidate)
    return out


def on_search_path(bin_name: str, search_path: str) -> str | None:
    """Where the host's own ladder would find *bin_name* as a launcher, if anywhere."""
    for directory in [p for p in search_path.split(os.pathsep) if p]:
        candidate = os.path.join(directory, bin_name)
        if _is_exec(candidate):
            return candidate
    return None


def describe_dirs(search_path: str, limit: int = 12) -> list[str]:
    seen: list[str] = []
    for p in search_path.split(os.pathsep):
        if p and p not in seen:
            seen.append(p)
    return seen[:limit] + ([f"… {len(seen) - limit} more"] if len(seen) > limit else [])


def iter_unique(items: Iterable[str]) -> list[str]:
    out: list[str] = []
    for item in items:
        if item and item not in out:
            out.append(item)
    return out
