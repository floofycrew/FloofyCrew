"""``python -m registry_tools.bootstrap`` — instantiate a registry repository from the template (task 7.5; Requirement 8.2, 10.5).

Both registries — the public GitHub repository and the internal one — are the
same layout with different **values**, and every value comes from the edition
adapter's ``registry.json`` (``--edition-config``): the source label, the
signing key id, the archive URL template, the host-registry row and the
FloofyCrew repository URL. Nothing edition-specific lives here.

What it does, in order:

1. copies ``templates/registry-repo/`` into the target, substituting the
   ``{{PLACEHOLDER}}`` tokens; for a GitHub-hosted registry also installs the CI
   workflows (``templates/ci-*.yml``);
2. seeds the first records from the FloofyCrew checkout's ``mods/<id>/``: the
   manifest copy, a deterministic archive of the mod (``<id>-<version>.zip``,
   fixed timestamps so a rebuild is byte-identical), and ``release.json`` naming
   it — the archive is written where the edition keeps archives (``archives/``
   inside the repository when the template points there, else ``.dist/`` for the
   maintainer to upload as a release asset);
3. writes the first ``compat.json`` row for the host the caller names: with a
   payload copy at hand the framework block is measured offline (bundle
   fingerprints against its ``static/dist``, Python anchors against its
   ``kiro_crew``), otherwise ``loader: ok`` on trust; the seeded mods get the
   verdict the caller asserts (``--verdict``, default ``expected``);
4. builds ``index.json`` and ``app-registry.json``, refreshes the README's mod
   table, signs everything when a private key is given (the key id must be the
   one the adapter pins);
5. when the adapter config carries a ``registryPackage`` block, renders the
   **gated package** (Requirement 8.10): the build-gate module and its pytest
   suite from ``templates/registry-gate/`` (neutral), the adapter's own build
   files, CR template and reserved names (``registryPackage.files``, appended or
   copied in), the pinned signing key record, the FloofyCrew tooling vendored
   under ``vendor/`` and ``RENDERED.md`` naming the source commit and every
   vendored file's hash.

The wrapper ``scripts/init_registry_repo.sh <target> --edition public|internal``
picks the adapter config by its ``edition`` field and passes the rest through.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from floofy_core.anchors import check_anchors
from floofy_core.canonical import canonical_bytes
from floofy_core.spa_report import check_bundle_fingerprints
from floofy_core.signing import load_private_key, sign_document
from floofy_core.sigverify import SignatureError

from .build import build_index, write_index
from .compat_merge import load_compat, merge_row
from .repo import RegistryRepo

__all__ = ["BootstrapError", "deterministic_zip", "init_registry_repo", "main"]

TEMPLATE_DIR = Path(__file__).resolve().parent.parent / "templates"
ZIP_EPOCH = (2026, 1, 1, 0, 0, 0)


class BootstrapError(Exception):
    pass


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def source_files(directory: Path) -> list[Path]:
    """Every regular file under ``directory``, sorted, minus Python byte-code caches.

    A ``__pycache__/*.pyc`` appears next to any ``setup.py`` or module that some tool
    imported in place (a test, an editor, ``pip``); it is not part of a template or a
    mod and is not UTF-8, so every tree walk that renders or archives files uses this.
    """
    return sorted(p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc"))


def deterministic_zip(mod_dir: Path, out: Path, *, top: str) -> tuple[str, int]:
    """Zip ``mod_dir`` under ``top/`` with fixed timestamps and sorted entries; returns ``(sha256, size)``."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in source_files(mod_dir):
            info = zipfile.ZipInfo(f"{top}/{path.relative_to(mod_dir).as_posix()}", date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, path.read_bytes())
    data = out.read_bytes()
    return hashlib.sha256(data).hexdigest(), len(data)


def _load_edition_config(path: Path) -> dict[str, Any]:
    try:
        config = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BootstrapError(f"{path}: {exc}") from exc
    for key in ("edition", "sourceLabel", "floofycrewRepo", "archiveUrlTemplate", "sources", "keys"):
        if key not in config:
            raise BootstrapError(f"{path}: missing {key!r}")
    if not config["sources"] or not isinstance(config["sources"][0], dict):
        raise BootstrapError(f"{path}: sources[] is empty")
    return config


def _placeholders(config: dict[str, Any], *, record_kind: str | None = None) -> dict[str, str]:
    source = config["sources"][0]
    edition = str(config["edition"])
    template = str(config["archiveUrlTemplate"])
    archives_in_repo = "/archives/" in template
    kind = record_kind or str(config.get("recordKind") or "archive")
    if kind not in ("link", "archive"):
        raise BootstrapError(f"recordKind must be link or archive, got {kind!r}")
    return {
        "SOURCE_LABEL": str(config["sourceLabel"]),
        "EDITION": edition,
        "KEY_ID": str(source.get("keyId") or (config["keys"][0].get("keyId") if config["keys"] else "")),
        "ARCHIVE_URL_TEMPLATE": template,
        "HOST_REPO": str(source.get("repo") or ""),
        "HOST_BRANCH": str(source.get("branch") or "main"),
        "FLOOFYCREW_REPO": str(config["floofycrewRepo"]),
        "SOURCE_URL": str(source["url"]),
        "RECORD_KIND": kind,
        "REPO_URL_PATTERN": str(config.get("repoUrlPattern") or ""),
        "RECORD_NOTE": (
            "Version records are **link-based** (Requirement 8.9): `release.json` names the mod's repository and the mod's directory in it, pinned at a commit — no tag locates a version; the index carries the commit, the canonical-manifest hash and the checkout's files, and a client fetches that commit with its own credentials and verifies all three. Nothing is downloaded from this repository but the index."
            if kind == "link"
            else "Version records name **archives**: `release.json` lists them by URL, `sha256` and `size`; a client downloads and verifies each one."
        ),
        "ARCHIVE_NOTE": (
            (
                "Archives are transitional here: `archives/<id>/` may still hold the deterministic archive of a version whose record used to be an archive record (`registry_tools.bootstrap.deterministic_zip` reproduces it byte for byte) until its link record has been verified from a fresh install; no link record references one, and the directory is removed afterwards."
                if kind == "link"
                else "Mod archives are committed under `archives/<id>/` in this repository and fetched raw; `release.json` names them by URL, `sha256` and `size`."
            )
            if archives_in_repo
            else "Mod archives are **not** in this repository: they are release assets of the mod's repository (tagged identically to the version); `release.json` names them by URL, `sha256` and `size`."
        ),
        "ASSET_HOW": (
            "On this registry the archive is committed under `archives/<id>/<id>-<version>.zip` in the same pull request."
            if archives_in_repo
            else "Attach it to a GitHub release of your repository; the URL is `https://github.com/<org>/<repo>/releases/download/<tag>/<file>`."
        ),
    }


def _render(text: str, values: dict[str, str], *, json_strings: bool = False) -> str:
    """Substitute ``{{KEY}}`` tokens; inside a JSON template the values are escaped as JSON string contents (a regex pattern with backslashes stays valid JSON)."""
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", json.dumps(value)[1:-1] if json_strings else value)
    return text


def _copy_template(target: Path, values: dict[str, str], *, github: bool) -> list[Path]:
    written: list[Path] = []
    source_dir = TEMPLATE_DIR / "registry-repo"
    for path in source_files(source_dir):
        relative = path.relative_to(source_dir)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_render(path.read_text(encoding="utf-8"), values, json_strings=destination.suffix == ".json"), encoding="utf-8")
        written.append(destination)
    if github:
        workflows = target / ".github" / "workflows"
        workflows.mkdir(parents=True, exist_ok=True)
        for name, out_name in (("ci-validate-submission.yml", "validate-submission.yml"), ("ci-publish-index.yml", "publish-index.yml")):
            destination = workflows / out_name
            destination.write_text((TEMPLATE_DIR / name).read_text(encoding="utf-8"), encoding="utf-8")
            written.append(destination)
    (target / "mods").mkdir(exist_ok=True)
    return written


# --- the gated package (Requirement 8.7, 8.10) ---------------------------------------------------------------

#: The FloofyCrew packages the gate runs; vendored as byte copies (tests and caches excluded).
VENDORED = (("floofy-core", "floofy_core"), ("registry-tools", "registry_tools"))
_VENDOR_SKIP = ("__pycache__", ".pytest_cache", ".hypothesis")


def _gate_values(config: dict[str, Any], values: dict[str, str]) -> dict[str, str]:
    """The extra placeholders of a gated package, from the adapter's ``registryPackage`` block."""
    block = config.get("registryPackage")
    if not isinstance(block, dict) or not isinstance(block.get("name"), str):
        raise BootstrapError("registry.json has no registryPackage block (name, gateModule, …); nothing to render a gated package from")
    module = str(block.get("gateModule") or re.sub(r"[^a-z0-9_]", "_", block["name"].lower()))
    if not re.match(r"^[a-z_][a-z0-9_]*$", module):
        raise BootstrapError(f"registryPackage.gateModule {module!r} is not a Python package name")
    return {
        **values,
        "PACKAGE_NAME": str(block["name"]),
        "GATE_MODULE": module,
        "GATE_DIST_NAME": str(block.get("gateDistName") or module.replace("_", "")),
        "GATE_ENV_PREFIX": str(block.get("gateEnvPrefix") or module.upper()),
        "GATE_COMMAND": str(block.get("gateCommand") or f"PYTHONPATH=src python -m {module}"),
        "CONTACT_KIND": str(block.get("contactKind") or "a contact"),
        "CONTACT_PATTERN": str(block.get("contactPattern") or "^\\S+$"),
    }


def _render_tree(source_dir: Path, target: Path, values: dict[str, str], *, rename: dict[str, str] | None = None) -> list[Path]:
    """Render every file under ``source_dir`` into ``target`` (placeholders substituted; ``rename`` maps template directory names)."""
    written: list[Path] = []
    for path in source_files(source_dir):
        parts = [(rename or {}).get(part, part) for part in path.relative_to(source_dir).parts]
        destination = target.joinpath(*parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_render(path.read_text(encoding="utf-8"), values, json_strings=destination.suffix == ".json"), encoding="utf-8")
        written.append(destination)
    return written


def _vendor(target: Path, floofycrew_root: Path) -> list[tuple[str, str]]:
    """Byte-copy the FloofyCrew packages the gate runs into ``vendor/``; returns ``(relative path, sha256)`` pairs."""
    vendor = target / "vendor"
    if vendor.exists():
        shutil.rmtree(vendor)
    copied: list[tuple[str, str]] = []
    for directory, package in VENDORED:
        source = Path(floofycrew_root) / directory / package
        if not source.is_dir():
            raise BootstrapError(f"{source}: the FloofyCrew checkout has no {package} package to vendor")
        for path in source_files(source):
            relative = path.relative_to(source)
            if any(part in _VENDOR_SKIP for part in relative.parts):
                continue
            destination = vendor / package / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            copied.append((f"vendor/{package}/{relative.as_posix()}", hashlib.sha256(path.read_bytes()).hexdigest()))
    return copied


def _source_commit(floofycrew_root: Path) -> str:
    """``<sha>[ +dirty]`` of the FloofyCrew checkout the package was rendered from, or ``unknown``."""
    import subprocess  # noqa: PLC0415

    try:
        head = subprocess.run(["git", "-C", str(floofycrew_root), "rev-parse", "HEAD"], capture_output=True, text=True, check=False, timeout=30).stdout.strip()
        dirty = subprocess.run(["git", "-C", str(floofycrew_root), "status", "--porcelain", "--untracked-files=no", "--", "floofy-core", "registry-tools"], capture_output=True, text=True, check=False, timeout=30).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    if not re.match(r"^[0-9a-f]{40}$", head):
        return "unknown"
    return head + (" +uncommitted changes in floofy-core/ or registry-tools/" if dirty else "")


def _write_rendered(target: Path, *, values: dict[str, str], floofycrew_root: Path, vendored: list[tuple[str, str]], written: list[Path]) -> Path:
    lines = [
        f"# RENDERED — how {values['PACKAGE_NAME']} was generated",
        "",
        "Nothing in this repository except the records under `mods/`, `compat.json`,",
        "`masquerade-allow.txt` and the signatures is written by hand: the rest is rendered",
        "by `registry-tools/scripts/init_registry_repo.sh` from the FloofyCrew repository",
        f"(`{values['FLOOFYCREW_REPO']}`). Regenerate there; do not edit the rendered files.",
        "",
        f"- FloofyCrew source: commit `{_source_commit(floofycrew_root)}`",
        f"- rendered at: {_now()}",
        f"- gate module: `src/{values['GATE_MODULE']}/` (`{values['GATE_COMMAND']}`, or `PYTHONPATH=src python -m {values['GATE_MODULE']}`)",
        "",
        "## Vendored FloofyCrew tooling (`vendor/`, byte copies, standard library only)",
        "",
        "| File | sha256 |",
        "|---|---|",
        *(f"| `{relative}` | `{digest}` |" for relative, digest in vendored),
        "",
        "## Rendered files",
        "",
        *(f"- `{relative}`" for relative in sorted(dict.fromkeys(p.relative_to(target).as_posix() for p in written))),
        "",
    ]
    path = target / "RENDERED.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _render_gate(target: Path, config: dict[str, Any], values: dict[str, str], *, floofycrew_root: Path, edition_config: Path) -> tuple[dict[str, str], list[Path], list[tuple[str, str]]]:
    """Render the gated package: the neutral gate template, the adapter's build files, the signing key record, the vendored tooling."""
    gate_values = _gate_values(config, values)
    written = _render_tree(TEMPLATE_DIR / "registry-gate", target, gate_values, rename={"gate_module": gate_values["GATE_MODULE"]})
    block = config["registryPackage"]
    extras = (Path(edition_config).parent / str(block.get("files") or "")).resolve() if block.get("files") else None
    if extras is not None:
        if not extras.is_dir():
            raise BootstrapError(f"registryPackage.files {extras} is not a directory")
        for path in source_files(extras):
            relative = path.relative_to(extras)
            text = _render(path.read_text(encoding="utf-8"), gate_values, json_strings=path.suffix == ".json")
            if relative.name.endswith(".append.md") or relative.name.endswith(".append"):
                # appended to the rendered file of the same name (README.append.md -> README.md, .gitignore.append -> .gitignore)
                base = relative.name[: -len(".append.md")] + ".md" if relative.name.endswith(".append.md") else relative.name[: -len(".append")]
                destination = target / relative.parent / base
                destination.write_text(destination.read_text(encoding="utf-8").rstrip("\n") + "\n" + text, encoding="utf-8")
            else:
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_text(text, encoding="utf-8")
            written.append(destination)
    # the public key clients trust, so the gate can verify the committed signatures
    key_record = next((k for k in config.get("keys") or [] if isinstance(k, dict) and k.get("keyId") == values["KEY_ID"]), None)
    if key_record is None:
        raise BootstrapError(f"registry.json pins key {values['KEY_ID']} but keys[] has no record for it")
    key_path = target / "schema" / "signing-key.pub.json"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps(key_record, indent=2) + "\n", encoding="utf-8")
    written.append(key_path)
    vendored = _vendor(target, floofycrew_root)
    return gate_values, written, vendored


def _seed_mod(repo: RegistryRepo, mod_dir: Path, *, floofycrew_repo: str, channel: str, archives_in_repo: bool, record_kind: str = "archive", link_source: Path | None = None, link_ref: str = "HEAD", contact: str | None = None, contact_pattern: str | None = None) -> dict[str, Any]:
    manifest = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))
    mod_id, version = str(manifest["id"]), str(manifest["version"])
    record_dir = repo.version_dir(mod_id, version)
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / "floofy.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    curated: dict[str, Any] = {"repo": floofycrew_repo, "tags": sorted({str(t) for t in manifest.get("tags") or []}), "links": {"source": f"{floofycrew_repo}", "docs": f"{floofycrew_repo}"} if floofycrew_repo.startswith("https://") else {}}
    # who answers for the mod (the gated package's community bar, Requirement 8.10): --contact, else the first author when it fits the edition's contact shape
    who = contact or next((str(a) for a in (manifest.get("authors") or []) if isinstance(a, str)), None)
    if who and (contact_pattern is None or re.match(contact_pattern, who)):
        curated["contact"] = who
    (record_dir.parent / "mod.json").write_text(json.dumps(curated, indent=2) + "\n", encoding="utf-8")
    if record_kind == "link":
        # a link record (Requirement 8.9): the mod lives at mods/<id> of the FloofyCrew repository on its default
        # branch; the facts (the pinned commit, canonical-manifest hash, files) come from the committed tree of
        # --link-source, a local checkout of that repository, at --link-ref (HEAD: the commit may not be pushed yet)
        from .links import LinkError, facts_from_local_commit  # noqa: PLC0415

        path = f"mods/{mod_id}"
        if link_source is None:
            raise BootstrapError(f"--record link needs --link-source <checkout of {floofycrew_repo}> so the record can pin the commit (or seed with --record archive)")
        try:
            facts = facts_from_local_commit(Path(link_source), link_ref, path)
        except LinkError as exc:
            raise BootstrapError(str(exc)) from exc
        if facts.manifest_sha256 != hashlib.sha256(canonical_bytes(manifest)).hexdigest():
            raise BootstrapError(f"{link_source} at {link_ref}: {path}/floofy.json differs from the seed {mod_dir}/floofy.json — commit the tree you seed from")
        release = {"channel": channel, "publishedAt": _now(), "link": {"repo": floofycrew_repo, "path": path, "commit": facts.commit, "manifestSha256": facts.manifest_sha256}, "files": facts.files}
        (record_dir / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
        return {"id": mod_id, "version": version, "record": "link", "repo": floofycrew_repo, "commit": facts.commit, "manifestSha256": facts.manifest_sha256, "files": len(facts.files)}
    file_name = f"{mod_id}-{version}.zip"
    tag = f"{mod_id}-{version}" if "{id}-{version}" in str(repo.config.get("archiveUrlTemplate", "")) else version
    url = repo.archive_url(mod_id=mod_id, version=version, tag=tag, file=file_name)
    if url is None:
        raise BootstrapError("registry.json has no archiveUrlTemplate")
    archive_path = (repo.root / "archives" / mod_id / file_name) if archives_in_repo else (repo.root / ".dist" / file_name)
    digest, size = deterministic_zip(mod_dir, archive_path, top=mod_id)
    release = {"tag": tag, "channel": channel, "publishedAt": _now(), "files": [{"url": url, "sha256": digest, "size": size, "name": file_name}]}
    (record_dir / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    return {"id": mod_id, "version": version, "record": "archive", "archive": str(archive_path), "sha256": digest, "size": size, "url": url, "tag": tag}


def _framework_block(payload: Path | None) -> dict[str, Any]:
    if payload is None:
        return {"loader": "ok"}
    package_dirs = [p for p in Path(payload).glob("**/kiro_crew") if (p / "__init__.py").is_file() and (p / "static" / "dist" / "index.html").is_file()]
    if not package_dirs:
        raise BootstrapError(f"{payload}: no kiro_crew package with static/dist inside")
    package_dir = sorted(package_dirs, key=lambda p: len(p.parts))[0]
    spa = check_bundle_fingerprints(package_dir / "static" / "dist")
    anchors = check_anchors(package_dir.parent)  # the directory holding kiro_crew/
    loader = "ok" if spa.ok and anchors.ok else "degraded"
    return {"loader": loader, "spaFingerprints": spa.to_dict()["spaFingerprints"], "pythonAnchors": anchors.to_dict()["pythonAnchors"], "shimVersion": None}


def init_registry_repo(
    target: Path,
    *,
    edition_config: Path,
    floofycrew_root: Path,
    seed_mods: list[str],
    host_version: str | None,
    channel: str | None,
    payload: Path | None = None,
    verdict: str = "expected",
    run: str | None = None,
    sign_key: Path | None = None,
    floofycrew_version: str | None = None,
    record_kind: str | None = None,
    link_source: Path | None = None,
    link_ref: str = "HEAD",
    replace: bool = False,
    gate: bool | None = None,
    contact: str | None = None,
) -> dict[str, Any]:
    """Instantiate (or, with ``replace``, re-render) a registry repository at ``target``.

    ``record_kind`` picks the shape of the seeded records — ``link`` (Requirement
    8.9; needs ``link_source``, a local checkout of the FloofyCrew repository,
    pinned at ``link_ref`` — ``HEAD`` by default) or ``archive`` — defaulting to the
    adapter's ``recordKind``. ``replace`` empties the target first (everything
    but ``.git/``) so a clone that already holds a rendered registry can be
    regenerated in place; the result is deterministic apart from timestamps.
    ``gate`` renders the **gated package** (Requirement 8.10): the build gate
    module and its tests from ``templates/registry-gate/``, the adapter's build
    files (``registryPackage.files``), the pinned signing key record and the
    vendored FloofyCrew tooling; it defaults to on whenever the adapter config
    carries a ``registryPackage`` block.
    """
    target = Path(target)
    if target.exists() and any(p.name != ".git" for p in target.iterdir()):
        if not replace:
            raise BootstrapError(f"{target} exists and is not empty (a fresh clone holding only .git/ is fine; --replace re-renders over an existing tree)")
        for child in target.iterdir():
            if child.name == ".git":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()
    config = _load_edition_config(edition_config)
    values = _placeholders(config, record_kind=record_kind)
    archives_in_repo = "/archives/" in values["ARCHIVE_URL_TEMPLATE"]
    github = "github.com" in values["HOST_REPO"] or "github.com" in values["SOURCE_URL"]
    target.mkdir(parents=True, exist_ok=True)
    written = _copy_template(target, values, github=github)
    repo = RegistryRepo.load(target)
    package_block = config.get("registryPackage") if isinstance(config.get("registryPackage"), dict) else {}
    seeded = [
        _seed_mod(repo, Path(floofycrew_root) / "mods" / mod_id, floofycrew_repo=values["FLOOFYCREW_REPO"], channel="stable", archives_in_repo=archives_in_repo, record_kind=values["RECORD_KIND"], link_source=link_source, link_ref=link_ref, contact=contact, contact_pattern=str(package_block.get("contactPattern")) if package_block.get("contactPattern") else None)
        for mod_id in seed_mods
    ]
    summary: dict[str, Any] = {"target": str(target), "edition": values["EDITION"], "source": values["SOURCE_LABEL"], "keyId": values["KEY_ID"], "recordKind": values["RECORD_KIND"], "written": [str(p.relative_to(target)) for p in written], "seeded": seeded, "github": github}
    compat = load_compat(repo.compat_path)
    if host_version:
        row = {
            "edition": values["EDITION"],
            "channel": channel or ("beta" if values["EDITION"] == "internal" else "stable"),
            "hostVersion": host_version,
            "framework": _framework_block(payload),
            "mods": {f"{s['id']}@{s['version']}": ({"verdict": verdict, "run": run} if run else verdict) for s in seeded},
            "checkedAt": _now(),
        }
        compat = merge_row(compat, row, source=values["SOURCE_LABEL"])
        summary["compatRow"] = row
    repo.compat_path.write_text(json.dumps(compat, indent=2) + "\n", encoding="utf-8")
    result = build_index(repo, floofycrew_version=floofycrew_version)
    if not result.ok:
        raise BootstrapError("index build failed: " + "; ".join(result.problems))
    write_index(repo, result)
    from .app_registry import write_app_registry  # noqa: PLC0415
    from .readme import update_readme  # noqa: PLC0415

    _path, rows = write_app_registry(repo, result.index)
    summary["appRows"] = len(rows)
    render_gate = bool(config.get("registryPackage")) if gate is None else gate
    if render_gate:
        gate_values, gate_written, vendored = _render_gate(target, config, values, floofycrew_root=Path(floofycrew_root), edition_config=Path(edition_config))
        written.extend(gate_written)
        summary["gate"] = {"package": gate_values["PACKAGE_NAME"], "module": gate_values["GATE_MODULE"], "vendored": len(vendored), "written": [str(p.relative_to(target)) for p in gate_written]}
    update_readme(repo, index=result.index)
    if render_gate:
        summary["gate"]["rendered"] = str(_write_rendered(target, values=gate_values, floofycrew_root=Path(floofycrew_root), vendored=vendored, written=written).relative_to(target))
        summary["written"] = [str(p.relative_to(target)) for p in written]
    if sign_key is not None:
        try:
            pair = load_private_key(Path(sign_key))
        except SignatureError as exc:
            raise BootstrapError(str(exc)) from exc
        if values["KEY_ID"] and pair.key_id != values["KEY_ID"]:
            raise BootstrapError(f"the adapter pins key {values['KEY_ID']} but {sign_key} is key {pair.key_id}")
        sign_document(repo.index_path, pair)
        sign_document(repo.compat_path, pair)
        summary["signedWith"] = pair.key_id
    return summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m registry_tools.bootstrap", description="instantiate a FloofyCrew registry repository from the template")
    parser.add_argument("target", help="an empty (or absent) directory to create the repository in")
    parser.add_argument("--edition-config", required=True, help="the edition adapter's registry.json (source label, key id, archive URL template, host-registry row)")
    parser.add_argument("--floofycrew-root", default=str(TEMPLATE_DIR.parent.parent), help="the FloofyCrew checkout holding mods/ (default: this checkout)")
    parser.add_argument("--seed", action="append", default=None, metavar="MOD_ID", help="a mods/<id> to seed (repeatable; default rimuru-branding)")
    parser.add_argument("--no-seed", action="store_true")
    parser.add_argument("--host-version", default=None, help="write the first compat row for this host version")
    parser.add_argument("--channel", default=None)
    parser.add_argument("--payload", default=None, help="a payload copy to measure the framework block against (offline fingerprints + anchors)")
    parser.add_argument("--verdict", default="expected", choices=["tested", "expected", "broken"], help="the seeded mods' verdict on that host (default expected)")
    parser.add_argument("--run", default=None, help="a link recorded with the verdict")
    parser.add_argument("--sign", default=None, metavar="KEY", help="sign index.json and compat.json with this private key")
    parser.add_argument("--floofycrew-version", default=None)
    parser.add_argument("--record", default=None, choices=["link", "archive"], help="the shape of the seeded records (default: the adapter's recordKind, else archive)")
    parser.add_argument("--link-source", default=None, metavar="DIR", help="for --record link: a local checkout of the FloofyCrew repository (the pinned commit and files come from its committed tree at --link-ref)")
    parser.add_argument("--link-ref", default="HEAD", metavar="COMMITISH", help="for --record link: the commit, branch or HEAD of --link-source to pin (default HEAD)")
    parser.add_argument("--replace", action="store_true", help="re-render over an existing tree (everything but .git/ is removed first)")
    parser.add_argument("--gate", dest="gate", action="store_true", default=None, help="render the gated package (build gate module, tests, vendored tooling); default: when the adapter config has a registryPackage block")
    parser.add_argument("--no-gate", dest="gate", action="store_false", help="do not render the gated package")
    parser.add_argument("--contact", default=None, help="the `contact` written into the seeded mods' mod.json (default: the manifest's first author when it fits the edition's contact shape)")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        summary = init_registry_repo(
            Path(args.target),
            edition_config=Path(args.edition_config),
            floofycrew_root=Path(args.floofycrew_root),
            seed_mods=[] if args.no_seed else (args.seed or ["rimuru-branding"]),
            host_version=args.host_version,
            channel=args.channel,
            payload=Path(args.payload) if args.payload else None,
            verdict=args.verdict,
            run=args.run,
            sign_key=Path(args.sign) if args.sign else None,
            floofycrew_version=args.floofycrew_version,
            record_kind=args.record,
            link_source=Path(args.link_source) if args.link_source else None,
            link_ref=args.link_ref,
            replace=args.replace,
            gate=args.gate,
            contact=args.contact,
        )
    except (BootstrapError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"registry repository written to {summary['target']} ({summary['source']}, edition {summary['edition']}, key {summary['keyId'] or '-'})")
        for seed in summary["seeded"]:
            if seed.get("record") == "link":
                print(f"  seeded {seed['id']}@{seed['version']}: link record -> {seed['repo']} at {seed['tag']} (commit {seed['commit'][:12]}, manifest {seed['manifestSha256'][:12]}…, {seed['files']} file(s))")
            else:
                print(f"  seeded {seed['id']}@{seed['version']}: archive {seed['archive']} ({seed['size']} bytes, sha256 {seed['sha256'][:12]}…) -> {seed['url']}")
        if summary.get("compatRow"):
            print(f"  compat row: {summary['compatRow']['edition']}/{summary['compatRow']['channel']}/{summary['compatRow']['hostVersion']} loader={summary['compatRow']['framework']['loader']}")
        print(f"  app-registry.json: {summary['appRows']} row(s); signed: {summary.get('signedWith') or 'no (run `registry_tools build . --sign KEY` and `sign compat.json`)'}")
        if summary.get("gate"):
            print(f"  gated package {summary['gate']['package']}: gate module src/{summary['gate']['module']}, {summary['gate']['vendored']} vendored file(s), {summary['gate']['rendered']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
