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
4. builds ``index.json`` and ``app-registry.json``, signs everything when a
   private key is given (the key id must be the one the adapter pins).

The wrapper ``scripts/init_registry_repo.sh <target> --edition public|internal``
picks the adapter config by its ``edition`` field and passes the rest through.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from floofy_core.anchors import check_anchors
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


def deterministic_zip(mod_dir: Path, out: Path, *, top: str) -> tuple[str, int]:
    """Zip ``mod_dir`` under ``top/`` with fixed timestamps and sorted entries; returns ``(sha256, size)``."""
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(p for p in mod_dir.rglob("*") if p.is_file() and "__pycache__" not in p.parts and not p.name.endswith(".pyc")):
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


def _placeholders(config: dict[str, Any]) -> dict[str, str]:
    source = config["sources"][0]
    edition = str(config["edition"])
    template = str(config["archiveUrlTemplate"])
    archives_in_repo = "/archives/" in template
    return {
        "SOURCE_LABEL": str(config["sourceLabel"]),
        "EDITION": edition,
        "KEY_ID": str(source.get("keyId") or (config["keys"][0].get("keyId") if config["keys"] else "")),
        "ARCHIVE_URL_TEMPLATE": template,
        "HOST_REPO": str(source.get("repo") or ""),
        "HOST_BRANCH": str(source.get("branch") or "main"),
        "FLOOFYCREW_REPO": str(config["floofycrewRepo"]),
        "SOURCE_URL": str(source["url"]),
        "ARCHIVE_NOTE": (
            "Mod archives are committed under `archives/<id>/` in this repository and fetched raw; `release.json` names them by URL, `sha256` and `size`."
            if archives_in_repo
            else "Mod archives are **not** in this repository: they are release assets of the mod's repository (tagged identically to the version); `release.json` names them by URL, `sha256` and `size`."
        ),
        "ASSET_HOW": (
            "On this registry the archive is committed under `archives/<id>/<id>-<version>.zip` in the same pull request."
            if archives_in_repo
            else "Attach it to a GitHub release of your repository; the URL is `https://github.com/<org>/<repo>/releases/download/<tag>/<file>`."
        ),
    }


def _render(text: str, values: dict[str, str]) -> str:
    for key, value in values.items():
        text = text.replace("{{" + key + "}}", value)
    return text


def _copy_template(target: Path, values: dict[str, str], *, github: bool) -> list[Path]:
    written: list[Path] = []
    source_dir = TEMPLATE_DIR / "registry-repo"
    for path in sorted(p for p in source_dir.rglob("*") if p.is_file()):
        relative = path.relative_to(source_dir)
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(_render(path.read_text(encoding="utf-8"), values), encoding="utf-8")
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


def _seed_mod(repo: RegistryRepo, mod_dir: Path, *, floofycrew_repo: str, channel: str, archives_in_repo: bool) -> dict[str, Any]:
    manifest = json.loads((mod_dir / "floofy.json").read_text(encoding="utf-8"))
    mod_id, version = str(manifest["id"]), str(manifest["version"])
    file_name = f"{mod_id}-{version}.zip"
    tag = f"{mod_id}-{version}" if "{id}-{version}" in str(repo.config.get("archiveUrlTemplate", "")) else version
    url = repo.archive_url(mod_id=mod_id, version=version, tag=tag, file=file_name)
    if url is None:
        raise BootstrapError("registry.json has no archiveUrlTemplate")
    archive_path = (repo.root / "archives" / mod_id / file_name) if archives_in_repo else (repo.root / ".dist" / file_name)
    digest, size = deterministic_zip(mod_dir, archive_path, top=mod_id)
    record_dir = repo.version_dir(mod_id, version)
    record_dir.mkdir(parents=True, exist_ok=True)
    (record_dir / "floofy.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    release = {"tag": tag, "channel": channel, "publishedAt": _now(), "files": [{"url": url, "sha256": digest, "size": size, "name": file_name}]}
    (record_dir / "release.json").write_text(json.dumps(release, indent=2) + "\n", encoding="utf-8")
    curated = {"repo": floofycrew_repo, "tags": sorted({str(t) for t in manifest.get("tags") or []}), "links": {"source": f"{floofycrew_repo}", "docs": f"{floofycrew_repo}"} if floofycrew_repo.startswith("https://") else {}}
    (record_dir.parent / "mod.json").write_text(json.dumps(curated, indent=2) + "\n", encoding="utf-8")
    return {"id": mod_id, "version": version, "archive": str(archive_path), "sha256": digest, "size": size, "url": url, "tag": tag}


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
) -> dict[str, Any]:
    target = Path(target)
    if target.exists() and any(p.name != ".git" for p in target.iterdir()):
        raise BootstrapError(f"{target} exists and is not empty (a fresh clone holding only .git/ is fine)")
    config = _load_edition_config(edition_config)
    values = _placeholders(config)
    archives_in_repo = "/archives/" in values["ARCHIVE_URL_TEMPLATE"]
    github = "github.com" in values["HOST_REPO"] or "github.com" in values["SOURCE_URL"]
    target.mkdir(parents=True, exist_ok=True)
    written = _copy_template(target, values, github=github)
    repo = RegistryRepo.load(target)
    seeded = [_seed_mod(repo, Path(floofycrew_root) / "mods" / mod_id, floofycrew_repo=values["FLOOFYCREW_REPO"], channel="stable", archives_in_repo=archives_in_repo) for mod_id in seed_mods]
    summary: dict[str, Any] = {"target": str(target), "edition": values["EDITION"], "source": values["SOURCE_LABEL"], "keyId": values["KEY_ID"], "written": [str(p.relative_to(target)) for p in written], "seeded": seeded, "github": github}
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

    _path, rows = write_app_registry(repo, result.index)
    summary["appRows"] = len(rows)
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
        )
    except (BootstrapError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        print(f"registry repository written to {summary['target']} ({summary['source']}, edition {summary['edition']}, key {summary['keyId'] or '-'})")
        for seed in summary["seeded"]:
            print(f"  seeded {seed['id']}@{seed['version']}: archive {seed['archive']} ({seed['size']} bytes, sha256 {seed['sha256'][:12]}…) -> {seed['url']}")
        if summary.get("compatRow"):
            print(f"  compat row: {summary['compatRow']['edition']}/{summary['compatRow']['channel']}/{summary['compatRow']['hostVersion']} loader={summary['compatRow']['framework']['loader']}")
        print(f"  app-registry.json: {summary['appRows']} row(s); signed: {summary.get('signedWith') or 'no (run `registry_tools build . --sign KEY` and `sign compat.json`)'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
