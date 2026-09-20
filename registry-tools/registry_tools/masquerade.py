"""The host-name masquerade check (Requirement 8.7, 11.8).

A mod listed in a registry must not pass itself off as the host or its vendor:
its ``id`` may not contain (and its display ``name`` may not have a word equal
to, or a word containing the host's name) any of the protected terms — by
default ``kiro``, ``kirocrew``, ``crew``, ``amazon`` and ``aws`` — after
**homoglyph normalisation**: Unicode compatibility decomposition, combining marks
stripped, a confusables table folding Cyrillic/Greek look-alikes and digit/symbol
substitutes (``k1r0crew`` → ``kirocrew``), lower case, separators removed.
Curators clear a legitimate exception by listing the mod id in the registry's
``masquerade-allow.txt``. The term list is the registry's choice (``--terms``).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

__all__ = ["DEFAULT_TERMS", "MasqueradeHit", "check_masquerade", "normalize"]

DEFAULT_TERMS: tuple[str, ...] = ("kirocrew", "kiro", "crew", "amazon", "aws")

#: Characters that read like ASCII letters — Cyrillic, Greek, fullwidth and the usual digit/symbol substitutes.
CONFUSABLES: dict[str, str] = {
    # Cyrillic
    "а": "a", "е": "e", "о": "o", "р": "p", "с": "c", "у": "y", "х": "x", "і": "i", "ј": "j", "ѕ": "s", "к": "k", "м": "m", "т": "t", "в": "b", "н": "h", "ԁ": "d", "ԛ": "q", "ԝ": "w", "ɑ": "a",
    # Greek
    "α": "a", "ο": "o", "ρ": "p", "ν": "v", "ι": "i", "κ": "k", "τ": "t", "υ": "u", "ε": "e", "χ": "x", "ω": "w", "μ": "u",
    # digits and symbols used as letters
    "0": "o", "1": "l", "3": "e", "4": "a", "5": "s", "7": "t", "8": "b", "9": "g", "@": "a", "$": "s", "|": "l", "!": "i", "€": "e", "ℓ": "l",
    # typographic
    "ı": "i", "ł": "l", "ø": "o", "đ": "d", "ß": "ss", "æ": "ae", "œ": "oe",
}

SEPARATORS = re.compile(r"[\s_\-.\u2010-\u2015\u2212]+")


def normalize(text: str) -> str:
    """Fold ``text`` to lower-case ASCII-ish letters with separators removed, for containment checks."""
    decomposed = unicodedata.normalize("NFKD", text)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    folded = "".join(CONFUSABLES.get(ch, CONFUSABLES.get(ch.lower(), ch.lower())) for ch in stripped)
    # `1` → `l` above; `kiro` spelled `k1ro` needs `i`, so try both readings when matching (see _readings)
    return SEPARATORS.sub("", folded)


def _readings(text: str) -> set[str]:
    """The normalised text plus its variant with ``l`` read as ``i`` (``1`` and ``|`` stand in for both)."""
    base = normalize(text)
    return {base, base.replace("l", "i")}


def _tokens(text: str) -> list[str]:
    return [t for t in SEPARATORS.split(text) if t]


@dataclass(frozen=True)
class MasqueradeHit:
    field: str  # "id" | "name"
    value: str
    term: str
    normalized: str

    def format(self) -> str:
        return f"{self.field} {self.value!r} reads as {self.normalized!r}, which contains the protected term {self.term!r}"


def check_masquerade(mod_id: str, name: str | None, *, terms: tuple[str, ...] | list[str] = DEFAULT_TERMS, allowlist: set[str] | frozenset[str] = frozenset()) -> list[MasqueradeHit]:
    """Return every protected term the id contains (substring) or the name carries as a word; empty for an allowlisted id."""
    if mod_id in allowlist:
        return []
    hits: list[MasqueradeHit] = []
    protected = [normalize(t) for t in terms]
    for reading in sorted(_readings(mod_id)):
        for term in protected:
            if term in reading:
                hits.append(MasqueradeHit("id", mod_id, term, reading))
                break
        if hits:
            break
    if name:
        for token in _tokens(name):
            found = None
            for reading in sorted(_readings(token)):
                for term in protected:
                    # a word equal to a protected term, or any word containing the host's full name
                    if reading == term or (term == normalize(terms[0]) and term in reading):
                        found = (term, reading)
                        break
                if found:
                    break
            if found:
                hits.append(MasqueradeHit("name", name, found[0], found[1]))
                break
    return hits
