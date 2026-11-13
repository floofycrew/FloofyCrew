"""Static network scan of a mod's code (Requirement 1.10, 11.6).

Mod traffic must use an encrypted channel; the only exception is loopback
(``localhost``, ``127.0.0.0/8``, ``::1``, unix sockets). The manifest declares the
remote hosts a mod contacts in ``network.hosts[]``. This module walks the mod's
text files and reports, as *warnings the user may accept*:

* ``PlaintextNetwork`` — an ``http://`` or ``ws://`` URL to a non-loopback host;
* ``UndeclaredHost`` — an ``https://`` or ``wss://`` URL whose host matches no
  declared pattern;
* ``CredentialsHint`` (informational) — the code mentions credentials while the
  manifest says ``network.credentials`` is false.

The Loader enforces the same rules at run time for ``ctx.http`` / ``floofy.fetch``
(Requirement 11.6 d); :func:`host_matches` is shared with it.
"""
from __future__ import annotations

import ipaddress
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

__all__ = [
    "CODE_SUFFIXES",
    "IDENTIFIER_HOSTS",
    "NetworkHit",
    "TEXT_SUFFIXES",
    "host_matches",
    "is_loopback_host",
    "iter_text_files",
    "scan_credentials",
    "scan_urls",
    "split_authority",
]

#: Files scanned for URLs. Markdown and other documentation are deliberately excluded.
TEXT_SUFFIXES: frozenset[str] = frozenset(
    {".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".json", ".html", ".htm", ".css", ".toml", ".yaml", ".yml", ".sh"}
)

#: Files scanned for credential hints (code only).
CODE_SUFFIXES: frozenset[str] = frozenset({".py", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".jsx", ".sh"})

#: Hosts that appear in code as identifiers (XML namespaces, schema ids), never contacted.
IDENTIFIER_HOSTS: frozenset[str] = frozenset({"www.w3.org", "json-schema.org", "schema.org", "xmlns.com", "purl.org"})

#: Directories never descended into.
_SKIP_DIRS: frozenset[str] = frozenset({".git", "__pycache__", "node_modules", ".hypothesis", ".pytest_cache"})

_URL_RE = re.compile(
    r"\b(?P<scheme>https?|wss?)://(?P<authority>\[[0-9A-Fa-f:.]+\](?::[0-9]+)?|[^\s/?#\"'<>\\`()\[\]{},;]+)",
    re.IGNORECASE,
)
_CREDENTIAL_RE = re.compile(r"(?i)\bauthorization\b|\bbearer\b|\bpassword\b|\bapi[_-]?key\b|[?&]token=|\bsecret\b")
_PLAINTEXT = frozenset({"http", "ws"})
_DEFAULT_PORTS = {"http": 80, "ws": 80, "https": 443, "wss": 443}


@dataclass(frozen=True)
class NetworkHit:
    """One URL occurrence: ``path`` is repository-relative, ``line`` 1-based."""

    path: str
    line: int
    scheme: str
    host: str
    port: int | None
    url: str

    @property
    def plaintext(self) -> bool:
        return self.scheme in _PLAINTEXT

    @property
    def effective_port(self) -> int:
        return self.port if self.port is not None else _DEFAULT_PORTS[self.scheme]


def iter_text_files(root: Path, suffixes: Iterable[str] = TEXT_SUFFIXES, *, skip: Iterable[str] = ("floofy.json",)) -> Iterator[Path]:
    """Yield scannable files under ``root`` in deterministic order."""
    wanted = frozenset(s.lower() for s in suffixes)
    skipped = frozenset(skip)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in _SKIP_DIRS and not d.startswith("."))
        for name in sorted(filenames):
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if rel in skipped or path.suffix.lower() not in wanted or path.is_symlink():
                continue
            yield path


def split_authority(authority: str) -> tuple[str, int | None]:
    """``user@host:port`` → (lower-case host, port or None). IPv6 literals keep no brackets."""
    text = authority.rsplit("@", 1)[-1]
    if text.startswith("["):
        host, _, rest = text[1:].partition("]")
        port = rest[1:] if rest.startswith(":") else ""
    else:
        host, _, port = text.partition(":")
    host = host.lower().rstrip(".")
    return host, (int(port) if port.isdigit() else None)


def is_loopback_host(host: str) -> bool:
    """Requirement 11.6 b: ``localhost``, ``127.0.0.0/8`` and ``::1`` are the only exempt hosts."""
    candidate = host.lower().rstrip(".")
    if candidate == "localhost":
        return True
    try:
        return ipaddress.ip_address(candidate).is_loopback
    except ValueError:
        return False


def _pattern_to_regex(pattern: str) -> re.Pattern[str]:
    labels = [r"[^.]+(?:\.[^.]+)*" if label == "*" else re.escape(label) for label in pattern.split(".")]
    return re.compile("^" + r"\.".join(labels) + "$")


def host_matches(host: str, pattern: str, port: int | None = None) -> bool:
    """``True`` when ``host`` (and ``port`` when the pattern names one) matches a declared pattern.

    A ``*`` label matches one or more labels at that position: ``*.example.com``
    matches ``api.example.com`` and ``a.b.example.com`` but not ``example.com``.
    """
    pattern_host, pattern_port = split_authority(pattern.lower())
    if pattern_port is not None and port is not None and pattern_port != port:
        return False
    return _pattern_to_regex(pattern_host).match(host.lower().rstrip(".")) is not None


def scan_urls(root: Path) -> list[NetworkHit]:
    """Every URL occurrence in the mod's text files, deduplicated per file, scheme and host."""
    hits: list[NetworkHit] = []
    seen: set[tuple[str, str, str]] = set()
    for path in iter_text_files(root):
        rel = path.relative_to(root).as_posix()
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for match in _URL_RE.finditer(line):
                scheme = match.group("scheme").lower()
                host, port = split_authority(match.group("authority"))
                if not host or host in IDENTIFIER_HOSTS:
                    continue
                key = (rel, scheme, host)
                if key in seen:
                    continue
                seen.add(key)
                hits.append(NetworkHit(rel, number, scheme, host, port, match.group(0)))
    return hits


def scan_credentials(root: Path) -> tuple[str, int] | None:
    """The first ``(path, line)`` in code files mentioning credentials, else ``None``."""
    for path in iter_text_files(root, CODE_SUFFIXES):
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            if _CREDENTIAL_RE.search(line):
                return path.relative_to(root).as_posix(), number
    return None
