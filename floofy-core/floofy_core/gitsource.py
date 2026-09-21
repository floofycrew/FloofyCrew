"""Installing a mod straight from a git reference (Requirement 8.8, 8.9, 11.1, 11.3, 11.6; design "Manager (CLI + UI)").

A **git reference** names a repository, a pinned point in it and, when the mod is
not at the repository root, the directory inside it::

    ssh://<host>/<path>[@<tag>][#<subdirectory>]
    https://<host>/<owner>/<repo>[.git][@<tag>][#<subdirectory>]

Only these two spellings are accepted: the scheme is mandatory (a bare
``host/owner/repo`` is a registry id as far as the manager is concerned), and the
transport must be encrypted — ``git://`` and ``http://`` are refused, exactly as
Requirement 11.6 refuses them for every other byte FloofyCrew itself moves.
``file://`` is a test convenience only: it is accepted when the environment sets
:data:`LOCAL_GIT_ENV` (``FLOOFY_ALLOW_LOCAL_GIT=1``), which the test-suite does for
its local bare repositories, and never by default. The last ``@`` of the URL path
starts the tag; a ``#`` fragment names the mod directory inside the checkout.

Pinning is the rule, not the exception: a reference without ``@<tag>`` is refused
unless the caller passes an explicit ``--ref <branch|commit>``; the install is
then still recorded with the commit the clone resolved to, so an audit reader
knows exactly what landed.

The flow (:func:`fetch`) is the one Requirement 8.8 describes: a shallow clone
at the tag (``git clone --depth 1 --branch <tag> --single-branch``) with the
**user's own credentials** — ``GIT_SSH_COMMAND``, the ssh agent, the credential
helper: the manager never handles credentials (Requirement 11.6) and only
disables the terminal prompt (``GIT_TERMINAL_PROMPT=0``) so an unauthenticated
clone fails instead of hanging; ``commit`` is ``git rev-parse HEAD``; the caller
then runs ``floofy validate`` on the checkout, verifies every ``files[]`` entry
(:func:`verify_files`) and shows the disclosure with the extra line
*unlisted source: no curator review, no compatibility data*
(:data:`UNLISTED_SOURCE_LINE`), which the user confirms like the rest of the
consent screen — typed, or with the explicit ``--accept-unlisted-source`` flag;
``--yes`` never accepts it. Nothing about the source is trusted beyond that
consent: the tier of such an install is ``unlisted`` (Requirement 8.11).

A registry **link record** (Requirement 8.9) pins a **commit** — no tag is
involved: the record names the repository, the mod's directory inside it and the
commit the curator recorded, and the client fetches exactly that commit
(:meth:`GitRef.at_commit`, a ``git fetch --depth 1 origin <sha>``, which both
forges FloofyCrew targets serve), then compares the checkout's canonical-manifest hash
(:func:`manifest_sha256`) and ``files[]`` with the record and refuses any
difference (:mod:`floofy_core.installer`). A reference with neither a tag nor a
``--ref`` (:meth:`GitRef.default_branch`) clones the repository's **default
branch** — what the registry tooling reads to make or refresh a record.

Checkouts live under ``<data home>/cache/sources/git/<key>/`` (one directory per
repository + pinned point, replaced on the next fetch of the same key; a cache
the user may delete at any time). Standard library only; ``git`` is run as a
subprocess with a timeout.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import canonical_bytes
from .datahome import DataHome

__all__ = [
    "GIT_REF_SCHEMES",
    "GitCloneError",
    "GitRef",
    "GitRefError",
    "GitVerifyError",
    "LOCAL_GIT_ENV",
    "UNLISTED_SOURCE_LINE",
    "checkout_key",
    "clone_into",
    "fetch",
    "git_env",
    "local_git_allowed",
    "manifest_sha256",
    "verify_files",
]

#: The transports a git reference may use (Requirement 11.6: encrypted only). ``file`` joins under :data:`LOCAL_GIT_ENV`.
GIT_REF_SCHEMES = ("ssh", "https")
#: Plaintext or non-git schemes that are recognised only to be refused with a clear message.
_REFUSED_SCHEMES = ("git", "http", "ftp", "git+http")
#: Test-only switch: ``file://`` references (local bare repositories) are accepted when this is ``1``.
LOCAL_GIT_ENV = "FLOOFY_ALLOW_LOCAL_GIT"
#: The extra consent line of an unlisted install (Requirement 8.8 wording).
UNLISTED_SOURCE_LINE = "unlisted source: no curator review, no compatibility data"
#: Seconds a clone may take before it is abandoned.
CLONE_TIMEOUT = 300
_ARCHIVE_SUFFIXES = (".zip", ".tar.gz", ".tgz")
_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_REF_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]*$")


class GitRefError(ValueError):
    """A reference the manager refuses to act on; ``code`` is stable for callers and tests."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class GitCloneError(GitRefError):
    """``git`` failed or timed out (``code`` ``CloneFailed``)."""

    def __init__(self, message: str):
        super().__init__("CloneFailed", message)


class GitVerifyError(GitRefError):
    """The checkout does not match what it was checked against (``code`` ``FilesMismatch`` / ``ManifestMismatch`` / ``CommitMismatch``)."""


def local_git_allowed(env: dict[str, str] | None = None) -> bool:
    """Whether ``file://`` references are accepted (the test-only switch :data:`LOCAL_GIT_ENV`)."""
    return ((os.environ if env is None else env).get(LOCAL_GIT_ENV) or "").strip() == "1"


def _valid_ref_name(name: str) -> bool:
    return bool(_REF_NAME.match(name)) and ".." not in name and "@{" not in name and not name.endswith(".lock") and not name.endswith("/")


def _valid_subdirectory(text: str) -> bool:
    if not text or text.startswith("/") or "\\" in text:
        return False
    return all(part not in ("", ".", "..") for part in text.split("/"))


@dataclass(frozen=True)
class GitRef:
    """A parsed git reference: the repository URL, the pinned point, the mod directory inside the checkout."""

    url: str
    tag: str | None = None
    #: An explicit ``--ref`` (branch name or commit) — the unpinned form the user asked for by name.
    ref: str | None = None
    subdirectory: str = ""

    @property
    def scheme(self) -> str:
        return urllib.parse.urlsplit(self.url).scheme.lower()

    @property
    def commitish(self) -> str:
        """What the clone checks out: the tag, else the explicit ``--ref``; empty for the default branch."""
        return self.tag or self.ref or ""

    @property
    def pinned(self) -> bool:
        """Whether the reference names a fixed point (a tag or a commit)."""
        return self.tag is not None or self.is_commit

    @property
    def is_commit(self) -> bool:
        return self.tag is None and self.ref is not None and bool(_HEX40.match(self.ref))

    @property
    def is_default_branch(self) -> bool:
        """Neither a tag nor a ``--ref``: the clone takes the repository's default branch."""
        return self.tag is None and self.ref is None

    @classmethod
    def at_commit(cls, url: str, commit: str, subdirectory: str = "", *, allow_local: bool | None = None) -> "GitRef":
        """The reference a link record resolves to: ``url`` at the 40-hex ``commit`` (Requirement 8.9)."""
        if not _HEX40.match(str(commit or "").lower()):
            raise GitRefError("BadRef", f"{commit!r} is not a 40-hex commit")
        base = cls.parse(url, ref=str(commit).lower(), allow_local=allow_local)
        return cls(url=base.url, tag=None, ref=base.ref, subdirectory=subdirectory)

    @classmethod
    def default_branch(cls, url: str, subdirectory: str = "", *, allow_local: bool | None = None) -> "GitRef":
        """``url`` at its default branch (what a registry record is made from); ``fetch`` records the commit it resolved to."""
        base = cls.parse(url, ref="HEAD", allow_local=allow_local)
        return cls(url=base.url, tag=None, ref=None, subdirectory=subdirectory)

    @property
    def text(self) -> str:
        """The canonical spelling: ``<url>[@<tag>][#<subdirectory>]`` (an explicit ``--ref`` is not part of it)."""
        out = self.url
        if self.tag:
            out += f"@{self.tag}"
        if self.subdirectory:
            out += f"#{self.subdirectory}"
        return out

    @property
    def stem(self) -> str:
        """The repository's last path segment without ``.git`` (``FloofyCrew``, ``repo``)."""
        name = urllib.parse.urlsplit(self.url).path.rstrip("/").rsplit("/", 1)[-1]
        return name[:-4] if name.endswith(".git") else name

    def to_dict(self) -> dict[str, Any]:
        return {"url": self.url, "tag": self.tag, "ref": self.ref, "subdirectory": self.subdirectory or None}

    @staticmethod
    def looks_like(text: str, *, explicit_ref: str | None = None) -> bool:
        """Whether ``text`` is a git reference rather than an archive URL, a path or a registry id.

        ``ssh://``, ``file://`` and the refused ``git://`` forms always are (so the
        refusal is the typed one); an ``https://`` URL is one when it ends in
        ``.git``, carries ``@<tag>`` after its last ``/``, carries a ``#`` fragment,
        or the caller passed ``--ref`` — an ``https://…/mod.zip`` download keeps its
        meaning.
        """
        parsed = urllib.parse.urlsplit(text)
        scheme = parsed.scheme.lower()
        if scheme in ("ssh", "file", "git+ssh") or scheme in _REFUSED_SCHEMES and scheme != "http":
            return True
        if scheme not in ("https", "http"):
            return False
        last = parsed.path.rstrip("/").rsplit("/", 1)[-1]
        if last.lower().endswith(_ARCHIVE_SUFFIXES):
            return False
        return explicit_ref is not None or bool(parsed.fragment) or last.endswith(".git") or "@" in last

    @classmethod
    def parse(cls, text: str, *, ref: str | None = None, allow_local: bool | None = None) -> "GitRef":
        """Parse a reference; every refusal is a :class:`GitRefError` with a stable ``code``."""
        allow_local = local_git_allowed() if allow_local is None else allow_local
        parsed = urllib.parse.urlsplit(text.strip())
        scheme = parsed.scheme.lower()
        if scheme in _REFUSED_SCHEMES or scheme in ("git+http",):
            raise GitRefError("PlaintextTransport", f"refusing {text}: git references must use ssh:// or https:// — {scheme}:// is not encrypted (Requirement 11.6)")
        if scheme == "file":
            if not allow_local:
                raise GitRefError("LocalTransport", f"refusing {text}: file:// references are for the test-suite's local repositories only ({LOCAL_GIT_ENV}=1); install a local checkout by path instead")
        elif scheme not in GIT_REF_SCHEMES:
            raise GitRefError("NotAGitReference", f"{text!r} is not a git reference: use ssh://<host>/<path>[@<tag>] or https://<host>/<owner>/<repo>[.git][@<tag>] (the scheme is required; a bare host/owner/repo is not accepted)")
        if parsed.query:
            raise GitRefError("NotAGitReference", f"{text!r}: a git reference carries no query string")
        if scheme != "file" and not parsed.hostname:
            raise GitRefError("NotAGitReference", f"{text!r}: no host in the reference")
        path = parsed.path
        tag: str | None = None
        if "@" in path:
            path, _, tag = path.rpartition("@")
            if not tag or not _valid_ref_name(tag):
                raise GitRefError("BadTag", f"{text!r}: {tag!r} is not a usable tag name")
        if not path.strip("/"):
            raise GitRefError("NotAGitReference", f"{text!r}: no repository path")
        if path.endswith("/") and len(path) > 1:
            path = path.rstrip("/")
        subdirectory = urllib.parse.unquote(parsed.fragment or "")
        if subdirectory and not _valid_subdirectory(subdirectory):
            raise GitRefError("BadSubdirectory", f"{text!r}: the #subdirectory must be a relative path inside the repository (no leading /, no ..)")
        explicit = (ref or "").strip() or None
        if explicit is not None:
            if tag is not None:
                raise GitRefError("AmbiguousReference", f"{text!r} pins @{tag} and --ref {explicit} was given too; use one of them")
            if not (_HEX40.match(explicit) or _valid_ref_name(explicit)):
                raise GitRefError("BadRef", f"--ref {explicit!r} is neither a branch name nor a 40-hex commit")
        if tag is None and explicit is None:
            raise GitRefError("UnpinnedReference", f"{text} names no tag: add @<tag> (the mod's version tag), or pass --ref <branch|commit> to install an unpinned checkout on purpose (the install is recorded with the commit it resolved to)")
        url = urllib.parse.urlunsplit((scheme, parsed.netloc, path, "", ""))
        return cls(url=url, tag=tag, ref=explicit, subdirectory=subdirectory)


def checkout_key(ref: GitRef) -> str:
    """The cache directory name for ``ref``: ``<stem>-<8 hex of url>@<commitish>`` made filesystem-safe."""
    digest = hashlib.sha256(ref.url.encode("utf-8")).hexdigest()[:8]
    point = re.sub(r"[^A-Za-z0-9._-]+", "_", ref.commitish)[:60] or "HEAD"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", ref.stem)[:40] or "repo"
    return f"{stem}-{digest}@{point}"


def git_env() -> dict[str, str]:
    """The environment ``git`` runs with: the user's own (``GIT_SSH_COMMAND``, agent, credential helper untouched), minus git's terminal prompt.

    ``GIT_TERMINAL_PROMPT=0`` makes an https clone without a stored credential
    fail with git's own message instead of waiting on a hidden prompt; ssh keeps
    its normal behaviour, so a passphrase prompt (the user's own key) still works
    on a terminal and the clone timeout bounds everything else.
    """
    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def _git_args(*, allow_local: bool) -> list[str]:
    args = ["git"]
    if allow_local:
        # only under the test switch: newer git refuses file:// transports in some contexts
        args += ["-c", "protocol.file.allow=always"]
    return args


def _run(args: list[str], *, cwd: Path | None, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(args, cwd=str(cwd) if cwd else None, env=env, capture_output=True, text=True, timeout=timeout, check=False, stdin=subprocess.DEVNULL)
    except FileNotFoundError as exc:
        raise GitCloneError("git is not installed (or not on PATH); installing from a git reference needs it") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitCloneError(f"git timed out after {timeout}s: {' '.join(args[:4])}…") from exc


def fetch(ref: GitRef, home: DataHome, *, timeout: int = CLONE_TIMEOUT, allow_local: bool | None = None) -> tuple[Path, str]:
    """Shallow-clone ``ref`` into the cache and return ``(checkout root, commit)``.

    The previous checkout of the same key is replaced; :func:`clone_into` does the
    cloning (a tag or branch with ``git clone --depth 1 --branch <name>
    --single-branch``, an explicit commit with ``git init`` + ``git fetch --depth 1
    origin <sha>`` — servers that refuse to serve an arbitrary commit fail with
    their own message).
    """
    destination = home.cache_git / checkout_key(ref)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        destination.unlink()
    elif destination.exists():
        shutil.rmtree(destination, ignore_errors=True)
    commit = clone_into(ref, destination, timeout=timeout, allow_local=allow_local)
    return destination, commit


def clone_into(ref: GitRef, destination: Path, *, timeout: int = CLONE_TIMEOUT, allow_local: bool | None = None) -> str:
    """Shallow-clone ``ref`` into the (absent or empty) ``destination`` and return the checked-out commit.

    Shared by the manager's cache (:func:`fetch`) and the registry tooling, which
    clones link records into a scratch directory to validate them (Requirement 8.7,
    8.10). ``destination`` is removed again on failure.
    """
    allow_local = local_git_allowed() if allow_local is None else allow_local
    if ref.scheme == "file" and not allow_local:
        raise GitRefError("LocalTransport", f"refusing {ref.url}: file:// references need {LOCAL_GIT_ENV}=1 (test-suite only)")
    destination = Path(destination)
    env = git_env()
    git = _git_args(allow_local=allow_local)
    if ref.is_commit:
        destination.mkdir(parents=True, exist_ok=True)
        steps = (
            [*git, "init", "-q", str(destination)],
            [*git, "-C", str(destination), "remote", "add", "origin", ref.url],
            [*git, "-C", str(destination), "fetch", "-q", "--depth", "1", "origin", ref.ref or ""],
            [*git, "-C", str(destination), "checkout", "-q", "--detach", "FETCH_HEAD"],
        )
        for step in steps:
            done = _run(step, cwd=None, env=env, timeout=timeout)
            if done.returncode != 0:
                shutil.rmtree(destination, ignore_errors=True)
                raise GitCloneError(f"cannot fetch {ref.url} at {ref.ref}: {_tail(done.stderr)}")
    elif ref.is_default_branch:
        done = _run([*git, "clone", "-q", "--depth", "1", "--single-branch", ref.url, str(destination)], cwd=None, env=env, timeout=timeout)
        if done.returncode != 0:
            shutil.rmtree(destination, ignore_errors=True)
            raise GitCloneError(f"cannot clone {ref.url} (default branch): {_tail(done.stderr)}")
    else:
        done = _run([*git, "clone", "-q", "--depth", "1", "--branch", ref.commitish, "--single-branch", ref.url, str(destination)], cwd=None, env=env, timeout=timeout)
        if done.returncode != 0:
            shutil.rmtree(destination, ignore_errors=True)
            detail = _tail(done.stderr)
            if "Remote branch" in detail and "not found" in detail:
                # git says "Remote branch X not found" for a missing TAG too: say what is missing instead of relaying git's wording
                raise GitCloneError(f"cannot clone {ref.url} at {ref.commitish}: the repository has no tag or branch named {ref.commitish!r} (git: {detail})")
            raise GitCloneError(f"cannot clone {ref.url} at {ref.commitish}: {detail}")
    head = _run([*git, "-C", str(destination), "rev-parse", "HEAD"], cwd=None, env=env, timeout=60)
    if head.returncode != 0 or not _HEX40.match(head.stdout.strip()):
        shutil.rmtree(destination, ignore_errors=True)
        raise GitCloneError(f"cannot read the commit of the clone of {ref.url}: {_tail(head.stderr) or head.stdout.strip()}")
    return head.stdout.strip()


def _tail(text: str, limit: int = 400) -> str:
    lines = [line.strip() for line in (text or "").strip().splitlines() if line.strip()]
    tail = " | ".join(lines[-4:])
    return tail[-limit:] if len(tail) > limit else tail or "(no output)"


def manifest_sha256(root: Path) -> str:
    """The SHA-256 of the **canonical** ``floofy.json`` (:mod:`floofy_core.canonical`), the hash a link record carries (Requirement 8.9)."""
    import json  # noqa: PLC0415

    document = json.loads((Path(root) / "floofy.json").read_text(encoding="utf-8"))
    return hashlib.sha256(canonical_bytes(document)).hexdigest()


def verify_files(root: Path, entries: list[dict[str, Any]], *, what: str = "the manifest") -> None:
    """Every ``{path, sha256[, size]}`` entry must exist under ``root`` with that hash (and size when given); else :class:`GitVerifyError`."""
    root = Path(root)
    problems: list[str] = []
    for entry in entries:
        rel = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(rel, str) or not rel:
            continue
        target = root / rel
        if not target.is_file() or target.is_symlink():
            problems.append(f"{rel}: missing")
            continue
        data = target.read_bytes()
        expected = str(entry.get("sha256") or "").lower()
        actual = hashlib.sha256(data).hexdigest()
        if expected and expected != actual:
            problems.append(f"{rel}: sha256 {actual[:12]}… ≠ {expected[:12]}…")
        size = entry.get("size")
        if isinstance(size, int) and not isinstance(size, bool) and size != len(data):
            problems.append(f"{rel}: {len(data)} bytes ≠ {size}")
    if problems:
        raise GitVerifyError("FilesMismatch", f"the checkout differs from {what}'s files[] ({len(problems)} problem(s)): " + "; ".join(problems[:6]) + (" …" if len(problems) > 6 else ""))
